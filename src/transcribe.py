"""Extracción de audio y transcripción con Whisper (faster-whisper).

En tu PC con la RTX 4060 Ti usa la GPU (CUDA). Si no hay GPU disponible,
pasa automáticamente a la CPU con un modelo más pequeño.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .utils import log, run_ffmpeg, read_json, write_json, fmt_time


def extract_audio(video: Path, wav: Path) -> Path:
    if wav.exists() and wav.stat().st_size > 1000:
        return wav
    log("🎧 Extrayendo el audio del directo...")
    run_ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(wav)], desc="extraer audio")
    return wav


def _add_cuda_dll_dirs() -> None:
    """En Windows, las DLL de CUDA instaladas con pip (nvidia-cublas/cudnn) no
    están en el PATH; las añadimos para que faster-whisper pueda usar la GPU."""
    if os.name != "nt":
        return
    for base in sys.path:
        nv = Path(base) / "nvidia"
        if nv.is_dir():
            for sub in nv.iterdir():
                b = sub / "bin"
                if b.is_dir():
                    try:
                        os.add_dll_directory(str(b))
                    except OSError:
                        pass
                    os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def _load_model(cfg: dict):
    from faster_whisper import WhisperModel  # import tardío: es pesado

    _add_cuda_dll_dirs()
    tcfg = cfg["transcripcion"]
    want = tcfg.get("dispositivo", "auto")
    if want in ("auto", "cuda"):
        try:
            model = WhisperModel(tcfg["modelo_gpu"], device="cuda", compute_type="float16")
            log(f"   Whisper en GPU (modelo {tcfg['modelo_gpu']}).")
            return model
        except Exception as e:  # sin CUDA / sin cuDNN
            if want == "cuda":
                raise
            log(f"   GPU no disponible para Whisper ({str(e)[:120]}). Uso la CPU.")
    model = WhisperModel(tcfg["modelo_cpu"], device="cpu", compute_type="int8")
    log(f"   Whisper en CPU (modelo {tcfg['modelo_cpu']}). Puede tardar un rato.")
    return model


def transcribe(wav: Path, out_json: Path, cfg: dict, duration: float) -> dict:
    if out_json.exists():
        log("📝 Transcripción encontrada en caché, la reutilizo.")
        return read_json(out_json)
    if not cfg["transcripcion"].get("activar", True):
        return {"segments": []}
    try:
        model = _load_model(cfg)
    except ImportError:
        log("⚠️  faster-whisper no está instalado: sigo sin transcripción.")
        return {"segments": []}
    except Exception as e:
        log(f"⚠️  No se pudo cargar Whisper ({e}). Sigo sin transcripción.")
        return {"segments": []}

    log("📝 Transcribiendo lo que dices en el directo...")
    t0 = time.time()
    segments_iter, _info = model.transcribe(
        str(wav),
        language=cfg.get("idioma", "es"),
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        condition_on_previous_text=False,
    )
    segments = []
    last_report = 0.0
    for seg in segments_iter:
        words = [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()}
                 for w in (seg.words or []) if w.word.strip()]
        segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2),
                         "text": seg.text.strip(), "words": words})
        if seg.end - last_report > 300 and duration > 0:
            last_report = seg.end
            pct = min(100, seg.end / duration * 100)
            log(f"   ... {fmt_time(seg.end)} / {fmt_time(duration)} ({pct:.0f}%)")
    data = {"segments": segments}
    write_json(out_json, data)
    log(f"   Transcripción lista: {len(segments)} frases en {time.time() - t0:.0f}s.")
    return data
