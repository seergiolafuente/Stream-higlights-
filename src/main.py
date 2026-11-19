"""Proceso completo: analizar el directo → elegir momentos → montar YouTube + Shorts.

Uso por línea de comandos:
    python -m src.main input/mi_directo.mp4 [--chat chat.json] [opciones]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from . import detect, llm
from .edit_long import render_long, select_for_youtube
from .edit_shorts import render_shorts
from .transcribe import extract_audio, transcribe
from .utils import (ROOT, fmt_time, load_config, log, probe, read_json,
                    safe_name, write_json)


def _merge(signal_moments: list[dict], ia: dict | None, tl: dict) -> list[dict]:
    """Une los momentos propuestos por Claude con los detectados por señales."""
    if not ia or not ia.get("moments"):
        return signal_moments
    score = tl["score"]
    smax = float(score.max()) + 1e-9
    merged = []
    for m in ia["moments"]:
        a, b = int(m["start"]), int(m["end"]) + 1
        sig = float(score[a:b].max()) / smax * 10 if b > a else 0
        m["score"] = round(0.7 * m["score"] + 0.3 * sig, 2)
        if b > a:
            m["peak"] = float(a + int(np.argmax(score[a:b])))
        merged.append(m)
    for m in signal_moments:
        if all(detect.overlap(m, x) < 0.3 for x in merged):
            m = dict(m)
            m["score"] = round(m["score"] * 0.75, 2)   # la IA manda; las señales rellenan
            merged.append(m)
    return merged


def process(video: str | Path, chat: str | Path | None = None, cfg: dict | None = None,
            only_render: bool = False, do_long: bool = True, do_shorts: bool = True,
            outdir: str | Path | None = None) -> Path:
    t0 = time.time()
    cfg = cfg or load_config()
    video = Path(video).resolve()
    if not video.exists():
        raise FileNotFoundError(f"No existe el vídeo: {video}")
    out = Path(outdir) if outdir else ROOT / "output" / safe_name(video.stem, 60)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "_cache"
    cache.mkdir(exist_ok=True)
    work = out / "_temp"
    work.mkdir(exist_ok=True)

    info = probe(video)
    duration = info["duration"]
    log(f"📺 Directo: {video.name} — {fmt_time(duration, True)} — "
        f"{info['width']}x{info['height']} @ {info['fps']:.0f} fps")
    if duration < 20:
        raise RuntimeError("El vídeo es demasiado corto para sacar momentos.")

    moments_file = out / "momentos.json"
    if only_render and moments_file.exists():
        log("♻️  Uso los momentos guardados en momentos.json (modo solo montaje).")
        saved = read_json(moments_file)
        moments, yt_meta = saved["momentos"], saved.get("youtube", {})
        transcript = read_json(cache / "transcripcion.json") if (cache / "transcripcion.json").exists() else {"segments": []}
    else:
        # ---------- Análisis ----------
        if info["has_audio"]:
            wav = extract_audio(video, cache / "audio16k.wav")
            transcript = transcribe(wav, cache / "transcripcion.json", cfg, duration)
            audio = detect.audio_features(wav, duration)
        else:
            log("⚠️  El vídeo no tiene audio: análisis solo por imagen.")
            transcript = {"segments": []}
            n = int(np.ceil(duration))
            audio = {"excite": np.zeros(n), "laugh": np.zeros(n),
                     "silent": np.zeros(n), "loud": np.zeros(n)}
        vfile = cache / "video_signals.npz"
        if vfile.exists():
            vz = np.load(vfile)
            vid = {"motion": vz["motion"], "scene": vz["scene"]}
        else:
            vid = detect.video_features(video, duration)
            np.savez(vfile, **vid)
        text = detect.transcript_features(transcript, duration)
        chatf = detect.chat_features(Path(chat) if chat else None, duration)
        tl = detect.build_timeline(audio, vid, text, chatf, cfg["pesos"], duration)

        log("🔎 Buscando los mejores momentos...")
        sig_moments = detect.find_moments(tl, transcript, duration, cfg)
        ia = llm.analyze(transcript, detect.timeline_peaks_text(tl), duration, cfg)
        moments = _merge(sig_moments, ia, tl)
        yt_meta = (ia or {}).get("youtube", {})
        write_json(moments_file, {"video": str(video), "momentos": moments, "youtube": yt_meta})
        log(f"   {len(moments)} momentos guardados en momentos.json (puedes editarlo y "
            f"volver a montar con 'solo montaje').")

    if not moments:
        raise RuntimeError("No se ha encontrado ningún momento destacable.")

    # ---------- Montaje ----------
    if do_long:
        selected = select_for_youtube(moments, cfg, duration, transcript)
        render_long(video, info, selected, transcript, yt_meta, out, cfg, work)
    if do_shorts:
        render_shorts(video, info, moments, transcript, yt_meta.get("juego", ""), out, cfg, work)

    shutil.rmtree(work, ignore_errors=True)
    log(f"🏁 Terminado en {fmt_time(time.time() - t0)}. Todo está en: {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="stream-highlights",
        description="Saca los mejores momentos de tu directo y monta un vídeo de YouTube + Shorts.")
    ap.add_argument("video", help="Vídeo del directo (mp4, mkv...)")
    ap.add_argument("--chat", help="Chat del directo (JSON de TwitchDownloader o .txt)")
    ap.add_argument("--config", help="Archivo JSON con ajustes que sobrescriben config.json")
    ap.add_argument("--salida", help="Carpeta de salida (por defecto output/<nombre>)")
    ap.add_argument("--minutos", type=float, help="Duración objetivo del vídeo de YouTube")
    ap.add_argument("--shorts", type=int, help="Número de Shorts a crear")
    ap.add_argument("--layout", choices=["blur", "facecam"], help="Diseño de los Shorts")
    ap.add_argument("--sin-ia", action="store_true", help="No usar Claude")
    ap.add_argument("--sin-transcripcion", action="store_true", help="No transcribir (más rápido)")
    ap.add_argument("--solo-montaje", action="store_true",
                    help="Reutiliza momentos.json (por si lo has editado) y solo monta")
    ap.add_argument("--solo-youtube", action="store_true")
    ap.add_argument("--solo-shorts", action="store_true")
    ap.add_argument("--rapido", action="store_true", help="Calidad de prueba (render rápido)")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    if a.minutos:
        cfg["youtube"]["duracion_objetivo_min"] = a.minutos
        cfg["youtube"]["duracion_minima_min"] = min(cfg["youtube"]["duracion_minima_min"], a.minutos)
        cfg["youtube"]["duracion_maxima_min"] = max(cfg["youtube"]["duracion_maxima_min"], a.minutos + 4)
    if a.shorts is not None:
        cfg["shorts"]["cantidad"] = a.shorts
    if a.layout:
        cfg["shorts"]["layout"] = a.layout
    if a.sin_ia:
        cfg["ia_claude"]["activar"] = False
    if a.sin_transcripcion:
        cfg["transcripcion"]["activar"] = False
    if a.rapido:
        cfg["encoder"] = "x264-rapido"
    try:
        process(a.video, a.chat, cfg, only_render=a.solo_montaje,
                do_long=not a.solo_shorts, do_shorts=not a.solo_youtube, outdir=a.salida)
    except Exception as e:
        log(f"❌ Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
