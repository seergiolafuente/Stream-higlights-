"""Montaje del vídeo resumen para YouTube (16:9, mínimo 10 minutos).

Estructura del vídeo:
  1. Teaser (cold open): 6–8 s del mejor momento para enganchar desde el segundo 0.
  2. Tarjeta de título sobre un fotograma desenfocado.
  3. Los mejores momentos en orden cronológico, cada uno con rótulo
     (categoría + título) y transiciones suaves.
  4. Outro con espacio para la pantalla final de YouTube.
Además genera: miniatura, capítulos, título/descr./etiquetas y subtítulos .srt.
"""
from __future__ import annotations

from pathlib import Path

from .detect import CATEGORY_LABELS, overlap
from .subtitles import (build_srt, lower_third_ass, outro_ass, thumbnail_ass,
                        title_card_ass)
from .utils import (AUDIO_ARGS, fmt_time, log, run_ffmpeg, video_encoder_args,
                    write_json)


# --------------------------------------------------------------------------- #
# Selección
# --------------------------------------------------------------------------- #
def select_for_youtube(moments: list[dict], cfg: dict, duration: float,
                       transcript: dict | None = None) -> list[dict]:
    y = cfg["youtube"]
    target = y["duracion_objetivo_min"] * 60
    minimum = y["duracion_minima_min"] * 60
    maximum = y["duracion_maxima_min"] * 60

    ranked = sorted(moments, key=lambda m: m["score"], reverse=True)
    top = ranked[0]["score"] if ranked else 0
    floor = max(2.5, top * 0.3)   # por debajo de esto se considera relleno
    chosen: list[dict] = []
    total = 0.0

    def try_add(m: dict) -> bool:
        nonlocal total
        m = dict(m)
        if any(overlap(m, c) > 0.25 for c in chosen):
            return False
        for c in chosen:  # recorta solapes pequeños
            if m["start"] < c["end"] <= m["end"]:
                m["start"] = c["end"]
            if m["start"] <= c["start"] < m["end"]:
                m["end"] = c["start"]
        if m["end"] - m["start"] < 8 or total + (m["end"] - m["start"]) > maximum:
            return False
        chosen.append(m)
        total += m["end"] - m["start"]
        return True

    # 1) Solo momentos buenos de verdad
    for m in ranked:
        if total >= target:
            break
        if m["score"] >= floor:
            try_add(m)

    # 2) Si no llegamos al mínimo, ampliamos el contexto de los buenos (antes y después)
    def grow(limit: float, step: float = 3.0, rounds: int = 15):
        nonlocal total
        for _ in range(rounds):
            if total >= limit:
                return
            chosen.sort(key=lambda c: c["start"])
            grew = False
            for i, c in enumerate(chosen):
                lo = chosen[i - 1]["end"] + 0.5 if i > 0 else 0.0
                hi = chosen[i + 1]["start"] - 0.5 if i + 1 < len(chosen) else duration
                room = y["clip_max_seg"] - (c["end"] - c["start"])
                if room <= 0:
                    continue
                ns = max(lo, c["start"] - min(step, room / 2))
                ne = min(hi, c["end"] + min(step, room / 2))
                if ns < c["start"] or ne > c["end"]:
                    total += (c["start"] - ns) + (ne - c["end"])
                    c["start"], c["end"] = ns, ne
                    grew = True
                if total >= limit:
                    return
            if not grew:
                return
    grow(minimum)

    # 3) Último recurso: momentos más flojos
    for m in ranked:
        if total >= minimum:
            break
        if m["score"] < floor:
            try_add(m)
    grow(minimum)

    chosen.sort(key=lambda c: c["start"])
    # Une clips casi pegados (menos de 3 s entre ellos) para que no haya saltos raros
    merged: list[dict] = []
    for c in chosen:
        if merged and c["start"] - merged[-1]["end"] < 3:
            prev = merged[-1]
            prev["end"] = c["end"]
            if c["score"] > prev["score"]:
                prev.update({k: c[k] for k in ("title", "category", "hook", "score")})
            continue
        merged.append(c)
    total = sum(c["end"] - c["start"] for c in merged)
    if total < minimum:
        log(f"⚠️  Solo hay {fmt_time(total)} de contenido bueno (el directo dura "
            f"{fmt_time(duration)}). El vídeo será algo más corto de {y['duracion_minima_min']} min.")
    log(f"   Seleccionados {len(merged)} momentos para YouTube ({fmt_time(total)} de contenido).")
    return merged


# --------------------------------------------------------------------------- #
# Renderizado
# --------------------------------------------------------------------------- #
def _vfilter(info: dict, W: int, H: int, fps: int, ass_name: str | None, dur: float,
             fade: float = 0.22) -> str:
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps},format=yuv420p")
    if ass_name:
        vf += f",ass={ass_name}"
    vf += f",fade=t=in:st=0:d={fade},fade=t=out:st={max(0, dur - fade):.3f}:d={fade}"
    return vf


def _afilter(dur: float, fade: float = 0.2) -> str:
    return (f"aresample=48000,aformat=channel_layouts=stereo,"
            f"afade=t=in:st=0:d={fade},afade=t=out:st={max(0, dur - fade - 0.05):.3f}:d={fade}")


def render_clip(video: Path, info: dict, start: float, dur: float, out: Path, cfg: dict,
                work: Path, ass_name: str | None = None) -> Path:
    y = cfg["youtube"]
    W, H = y["resolucion"]
    fps = y["fps"] if info["fps"] >= 50 else min(y["fps"], 30)
    args = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(video)]
    if not info["has_audio"]:
        args += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    args += ["-vf", _vfilter(info, W, H, fps, ass_name, dur),
             "-af", _afilter(dur), "-map", "0:v:0", "-map", "1:a:0" if not info["has_audio"] else "0:a:0",
             *video_encoder_args(cfg), "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
             "-t", f"{dur:.3f}", str(out)]
    run_ffmpeg(args, cwd=work, desc=out.name)
    return out


def render_card(frame_src: Path, frame_t: float, dur: float, out: Path, cfg: dict, work: Path,
                ass_name: str, info: dict) -> Path:
    """Tarjeta con un fotograma del directo desenfocado y oscurecido + texto."""
    y = cfg["youtube"]
    W, H = y["resolucion"]
    fps = y["fps"] if info["fps"] >= 50 else min(y["fps"], 30)
    still = work / (out.stem + "_frame.png")
    run_ffmpeg(["-ss", f"{frame_t:.3f}", "-i", str(frame_src), "-frames:v", "1",
                "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
                str(still)], desc="fotograma tarjeta")
    vf = (f"scale={W // 4}:{H // 4},boxblur=12:2,scale={W}:{H},eq=brightness=-0.18:saturation=1.25,"
          f"zoompan=z='min(pzoom+0.0006,1.06)':d=1:s={W}x{H}:fps={fps},setsar=1,format=yuv420p,"
          f"ass={ass_name},fade=t=in:st=0:d=0.25,fade=t=out:st={dur - 0.3:.3f}:d=0.3")
    run_ffmpeg(["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", still.name,
                "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-vf", vf, "-map", "0:v", "-map", "1:a", *video_encoder_args(cfg),
                "-c:a", "pcm_s16le", "-t", f"{dur:.3f}", str(out)], cwd=work, desc=out.name)
    return out


def make_thumbnail(video: Path, t: float, text: str, category: str, out: Path, cfg: dict,
                   work: Path) -> Path:
    ass = thumbnail_ass(work / "thumb.ass", 1280, 720, cfg, text, category)
    run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                "-vf", ("scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
                        "eq=contrast=1.12:saturation=1.35:brightness=0.02,unsharp=5:5:0.8,"
                        f"vignette=PI/5,ass={ass.name}"),
                "-q:v", "2", str(out)], cwd=work, desc="miniatura")
    return out


def render_long(video: Path, info: dict, selected: list[dict], transcript: dict,
                yt_meta: dict, outdir: Path, cfg: dict, work: Path) -> Path | None:
    if not selected:
        log("⚠️  No hay momentos para el vídeo de YouTube.")
        return None
    y = cfg["youtube"]
    W, H = y["resolucion"]
    parts: list[Path] = []
    chapters: list[tuple[float, str]] = []
    mapping: list[tuple[float, float, float]] = []
    t_out = 0.0
    best = max(selected, key=lambda m: m["score"])
    main_title = (yt_meta.get("titulos") or [best["title"]])[0]

    log("🎞️  Montando el vídeo de YouTube...")
    # 1) Teaser
    if y.get("teaser", True):
        p = best["peak"]
        ts = max(best["start"], p - 4)
        td = min(7.0, best["end"] - ts)
        if td >= 3:
            parts.append(render_clip(video, info, ts, td, work / "p000_teaser.mkv", cfg, work))
            mapping.append((ts, ts + td, t_out))
            chapters.append((0.0, "Intro"))
            t_out += td
    # 2) Tarjeta de título
    if y.get("tarjeta_titulo", True):
        dur = 3.0
        ass = title_card_ass(work / "card.ass", W, H, cfg, main_title, cfg["canal"], dur)
        parts.append(render_card(video, best["peak"], dur, work / "p001_card.mkv", cfg, work,
                                 ass.name, info))
        if not chapters:
            chapters.append((0.0, "Intro"))
        t_out += dur
    # 3) Momentos
    for i, m in enumerate(selected):
        dur = m["end"] - m["start"]
        ass_name = None
        if y.get("rotulos_momento", True):
            ass = lower_third_ass(work / f"lt_{i:03d}.ass", W, H, cfg,
                                  CATEGORY_LABELS.get(m["category"], "MOMENTO"),
                                  m["category"], m["title"], dur)
            ass_name = ass.name
        log(f"   [{i + 1}/{len(selected)}] {fmt_time(m['start'], True)} · {m['title']} ({dur:.0f}s)")
        parts.append(render_clip(video, info, m["start"], dur, work / f"p{i + 2:03d}_clip.mkv",
                                 cfg, work, ass_name))
        chapters.append((t_out, m["title"]))
        mapping.append((m["start"], m["end"], t_out))
        t_out += dur
    # 4) Outro
    od = float(y.get("outro_seg", 12))
    if od > 0:
        ass = outro_ass(work / "outro.ass", W, H, cfg, od)
        last = selected[-1]
        parts.append(render_card(video, max(0, last["end"] - 1), od,
                                 work / f"p{len(selected) + 2:03d}_outro.mkv", cfg, work,
                                 ass.name, info))
        chapters.append((t_out, "Final"))
        t_out += od

    # Unión + normalización de volumen a -14 LUFS (estándar de YouTube)
    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    final = outdir / "youtube_resumen.mp4"
    log("   Uniendo todo y ajustando el volumen al estándar de YouTube...")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", concat.name, "-map", "0:v", "-map", "0:a",
                "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", *AUDIO_ARGS,
                "-movflags", "+faststart", str(final)], cwd=work, desc="unir vídeo")

    # Miniatura
    thumb_text = yt_meta.get("texto_miniatura") or best["hook"] or best["title"]
    try:
        make_thumbnail(video, best["peak"], " ".join(thumb_text.split()[:4]), best["category"],
                       outdir / "youtube_miniatura.jpg", cfg, work)
    except Exception as e:
        log(f"⚠️  No se pudo crear la miniatura: {e}")

    # Subtítulos
    if y.get("subtitulos_srt", True) and transcript.get("segments"):
        build_srt(outdir / "youtube_subtitulos.srt", transcript, mapping)

    # Capítulos (YouTube exige ≥3, el primero en 0:00 y de ≥10 s cada uno)
    chap_clean: list[tuple[float, str]] = []
    for t, name in chapters:
        if chap_clean and t - chap_clean[-1][0] < 10:
            continue
        chap_clean.append((t, name))
    _write_youtube_txt(outdir / "youtube_publicacion.txt", yt_meta, main_title, chap_clean,
                       selected, cfg, t_out)
    write_json(outdir / "youtube_momentos.json", selected)
    log(f"✅ Vídeo de YouTube listo: {final.name} ({fmt_time(t_out)})")
    return final


def _write_youtube_txt(path: Path, meta: dict, main_title: str, chapters, selected, cfg, total):
    titles = meta.get("titulos") or [main_title, f"Lo mejor del directo | {cfg['canal']}",
                                      f"MOMENTAZOS del directo de {cfg['canal']}"]
    desc = meta.get("descripcion") or (
        f"Lo mejor del último directo: los momentos más locos, graciosos y épicos.\n"
        f"Si te ha gustado, te espero en directo en {cfg['twitch_url']}")
    tags = meta.get("etiquetas") or [cfg["canal"], "twitch", "directo", "mejores momentos",
                                     "gta", "gta 5", "gta v", "gaming", "momentos graciosos",
                                     "highlights", "español"]
    lines = ["TÍTULOS PROPUESTOS", "==================="]
    lines += [f"- {t}" for t in titles]
    lines += ["", "DESCRIPCIÓN (copiar y pegar)", "============================", desc, "",
              "Capítulos:"]
    lines += [f"{fmt_time(t)} {name}" for t, name in chapters]
    lines += ["", f"Directos: {cfg['twitch_url']}", "", "ETIQUETAS", "=========",
              ", ".join(tags), "", f"Duración total: {fmt_time(total)}", "",
              "MOMENTOS (tiempo en el directo original)", "======================================"]
    lines += [f"{fmt_time(m['start'], True)}–{fmt_time(m['end'], True)}  [{m['category']}] "
              f"{m['title']}  (nota {m['score']:.1f})" for m in selected]
    path.write_text("\n".join(lines), encoding="utf-8")
