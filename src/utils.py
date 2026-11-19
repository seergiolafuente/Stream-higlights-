"""Utilidades comunes: ffmpeg/ffprobe, registro de mensajes, tiempos y configuración."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- #
# Registro de mensajes (la GUI sustituye esta función para mostrar el progreso)
# --------------------------------------------------------------------------- #
_log_fn: Callable[[str], None] = lambda msg: print(msg, flush=True)


def set_logger(fn: Callable[[str], None]) -> None:
    global _log_fn
    _log_fn = fn


def log(msg: str) -> None:
    _log_fn(msg)


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> dict:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    local = ROOT / "config.local.json"
    if local.exists():
        cfg = _deep_merge(cfg, json.loads(local.read_text(encoding="utf-8")))
    if path:
        cfg = _deep_merge(cfg, json.loads(Path(path).read_text(encoding="utf-8")))
    return cfg


# --------------------------------------------------------------------------- #
# ffmpeg
# --------------------------------------------------------------------------- #
def find_bin(name: str) -> str:
    local = ROOT / "bin" / (name + (".exe" if os.name == "nt" else ""))
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(
            f"No encuentro '{name}'. Instala FFmpeg (ejecuta instalar.bat) "
            f"o copia {name}.exe en la carpeta 'bin' del programa."
        )
    return found


FFMPEG = None
FFPROBE = None


def ffmpeg_bin() -> str:
    global FFMPEG
    if FFMPEG is None:
        FFMPEG = find_bin("ffmpeg")
    return FFMPEG


def ffprobe_bin() -> str:
    global FFPROBE
    if FFPROBE is None:
        FFPROBE = find_bin("ffprobe")
    return FFPROBE


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # evita ventanas negras en Windows


def run_ffmpeg(args: list[str], cwd: str | Path | None = None, desc: str = "") -> None:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg falló{(' (' + desc + ')') if desc else ''}:\n"
                           f"{proc.stderr[-3000:]}\nComando: {' '.join(cmd)}")


def popen_ffmpeg(args: list[str], **kw) -> subprocess.Popen:
    cmd = [ffmpeg_bin(), "-hide_banner", *args]
    return subprocess.Popen(cmd, creationflags=_NO_WINDOW, **kw)


def probe(path: str | Path) -> dict:
    cmd = [ffprobe_bin(), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", creationflags=_NO_WINDOW)
    if out.returncode != 0:
        raise RuntimeError(f"No puedo leer el vídeo: {path}\n{out.stderr}")
    data = json.loads(out.stdout)
    v = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    if v is None:
        raise RuntimeError("El archivo no contiene vídeo.")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    return {
        "duration": float(data["format"].get("duration") or v.get("duration") or 0),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": fps if 1 < fps < 240 else 30.0,
        "has_audio": a is not None,
    }


_ENCODER_CACHE: dict[str, list[str]] = {}


def video_encoder_args(cfg: dict, kind: str = "final") -> list[str]:
    """Devuelve los argumentos de codificación. Usa NVENC (tu RTX) si está disponible."""
    key = f"{cfg.get('encoder')}-{cfg.get('calidad')}-{kind}"
    if key in _ENCODER_CACHE:
        return _ENCODER_CACHE[key]
    want = cfg.get("encoder", "auto")
    high = cfg.get("calidad", "alta") == "alta"
    nvenc_ok = False
    if want in ("auto", "nvenc"):
        try:
            run_ffmpeg(["-f", "lavfi", "-i", "color=black:s=256x256:d=0.2",
                        "-c:v", "h264_nvenc", "-f", "null", "-"], desc="test nvenc")
            nvenc_ok = True
        except Exception:
            nvenc_ok = False
    if nvenc_ok:
        args = ["-c:v", "h264_nvenc", "-preset", "p5" if high else "p3",
                "-tune", "hq", "-rc", "vbr", "-cq", "19" if high else "23",
                "-b:v", "0", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    else:
        args = ["-c:v", "libx264", "-preset", "medium" if high else "veryfast",
                "-crf", "18" if high else "22", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    if want == "x264-rapido":
        args = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p"]
    _ENCODER_CACHE[key] = args
    log(f"   Codificador de vídeo: {args[1]}")
    return args


AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


# --------------------------------------------------------------------------- #
# Tiempos y texto
# --------------------------------------------------------------------------- #
def fmt_time(sec: float, always_hours: bool = False) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h or always_hours:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
            .replace("\n", "\\N"))


def safe_name(text: str, maxlen: int = 50) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in text)
    keep = "_".join(keep.split())
    return keep[:maxlen] or "clip"


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def drain_stderr(proc: subprocess.Popen, on_line: Callable[[str], None] | None = None) -> threading.Thread:
    """Lee stderr en segundo plano (evita bloqueos de la tubería)."""
    def _run():
        for raw in iter(proc.stderr.readline, b""):
            if on_line:
                on_line(raw.decode("utf-8", "replace"))
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
