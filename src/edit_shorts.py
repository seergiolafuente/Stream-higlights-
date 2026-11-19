"""Shorts / Reels / TikTok verticales (1080x1920).

Dos diseños:
  • "blur":    el juego en el centro y el mismo vídeo desenfocado de fondo.
  • "facecam": tu webcam arriba y el juego recortado abajo (configura la zona
               de la webcam en config.json → shorts.facecam, en proporciones 0–1).
Incluye gancho de texto arriba los primeros segundos y subtítulos dinámicos
palabra a palabra (estilo TikTok).
"""
from __future__ import annotations

from pathlib import Path

from .detect import overlap
from .subtitles import short_ass
from .utils import (AUDIO_ARGS, fmt_time, log, run_ffmpeg, safe_name,
                    video_encoder_args)

HASHTAGS_BASE = ["#twitch", "#streamer", "#gaming", "#clips", "#españa"]


def _fit_window(m: dict, transcript: dict, cfg: dict, duration: float) -> tuple[float, float]:
    s = cfg["shorts"]
    lo, hi = s["duracion_min_seg"], s["duracion_max_seg"]
    start, end = m["start"], m["end"]
    if end - start > hi:
        # centramos en el pico, con un poco más de margen después (la reacción)
        start = max(start, m["peak"] - hi * 0.55)
        end = start + hi
        # que termine al final de una frase si es posible
        best_end = end
        for seg in transcript.get("segments", []):
            if start + lo < seg["end"] + 0.4 <= end:
                best_end = seg["end"] + 0.4
        end = best_end
    if end - start < lo:
        pad = lo - (end - start)
        start = max(0.0, start - pad * 0.4)
        end = min(duration, start + lo)
    return round(start, 2), round(min(end, start + hi), 2)


def select_shorts(moments: list[dict], cfg: dict) -> list[dict]:
    """Los mejores momentos aptos para vertical, con variedad de categorías:
    primero el mejor de cada categoría, luego el resto por nota."""
    n = int(cfg["shorts"]["cantidad"])
    if n <= 0:
        return []
    ok = [m for m in moments if m.get("short_ok", True)]
    pool = sorted(ok, key=lambda m: m["score"], reverse=True)
    floor = (pool[0]["score"] * 0.3) if pool else 0
    pool = [m for m in pool if m["score"] >= floor] or pool
    chosen: list[dict] = []

    def add(m):
        if m not in chosen and all(overlap(m, c) <= 0.3 for c in chosen):
            chosen.append(m)

    seen = set()
    for m in pool:
        if len(chosen) >= n:
            break
        if m["category"] not in seen:
            seen.add(m["category"])
            add(m)
    for m in pool:
        if len(chosen) >= n:
            break
        add(m)
    if len(chosen) < n:  # si faltan, usamos también los "no aptos"
        for m in sorted(moments, key=lambda m: m["score"], reverse=True):
            if len(chosen) >= n:
                break
            add(m)
    return sorted(chosen, key=lambda m: m["score"], reverse=True)


def _filter_blur(info: dict, fps: int) -> str:
    return (
        "[0:v]split=2[bgsrc][fgsrc];"
        "[bgsrc]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
        "boxblur=10:3,eq=brightness=-0.10:saturation=1.2,scale=1080:1920:flags=bicubic[bg];"
        # el juego un poco ampliado (recortando laterales) para que se vea grande
        "[fgsrc]scale=1440:-2:flags=lanczos,crop=1080:ih[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2-60,setsar=1,fps={fps},format=yuv420p"
    )


def _filter_facecam(info: dict, fps: int, fc: dict) -> str:
    sw, sh = info["width"], info["height"]
    cx, cy = int(fc["x"] * sw), int(fc["y"] * sh)
    cw, ch = int(fc["w"] * sw) // 2 * 2, int(fc["h"] * sh) // 2 * 2
    cam_h = int(round(1080 * ch / max(cw, 1) / 2) * 2)
    cam_h = max(400, min(cam_h, 820))
    game_h = 1920 - cam_h
    # recorte central del juego con la proporción 1080:game_h
    gw = int(sh * 1080 / game_h) // 2 * 2
    gw = min(gw, sw)
    gx = (sw - gw) // 2
    return (
        "[0:v]split=2[cam][game];"
        f"[cam]crop={cw}:{ch}:{cx}:{cy},scale=1080:{cam_h}:force_original_aspect_ratio=increase,"
        f"crop=1080:{cam_h}[c];"
        f"[game]crop={gw}:{sh}:{gx}:0,scale=1080:{game_h}:flags=lanczos[g];"
        f"[c][g]vstack=inputs=2,setsar=1,fps={fps},format=yuv420p"
    )


def render_short(video: Path, info: dict, m: dict, start: float, end: float, transcript: dict,
                 out: Path, cfg: dict, work: Path, idx: int) -> Path:
    s = cfg["shorts"]
    layout = s.get("layout", "blur")
    fps = s["fps"] if info["fps"] >= 50 else min(s["fps"], 30)
    dur = end - start
    ass = short_ass(work / f"short_{idx:02d}.ass", transcript, start, end,
                    m.get("hook") or m["title"], cfg, layout, m["category"])
    base = _filter_facecam(info, fps, s["facecam"]) if layout == "facecam" else _filter_blur(info, fps)
    fc = (base + f",ass={ass.name},fade=t=in:st=0:d=0.15,"
          f"fade=t=out:st={dur - 0.35:.3f}:d=0.35[v]")
    args = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(video)]
    if info["has_audio"]:
        fc += (f";[0:a]aresample=48000,loudnorm=I=-14:TP=-1.5:LRA=11,"
               f"afade=t=out:st={dur - 0.4:.3f}:d=0.4[a]")
        amap = "[a]"
    else:
        args += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        amap = "1:a"
    args += ["-filter_complex", fc, "-map", "[v]", "-map", amap, *video_encoder_args(cfg),
             *AUDIO_ARGS, "-t", f"{dur:.3f}", "-movflags", "+faststart", str(out)]
    run_ffmpeg(args, cwd=work, desc=out.name)
    return out


def render_shorts(video: Path, info: dict, moments: list[dict], transcript: dict,
                  game: str, outdir: Path, cfg: dict, work: Path) -> list[Path]:
    chosen = select_shorts(moments, cfg)
    if not chosen:
        log("⚠️  No hay momentos para Shorts.")
        return []
    sdir = outdir / "shorts"
    sdir.mkdir(exist_ok=True)
    for old in sdir.glob("short_*.mp4"):  # quita los de una pasada anterior
        old.unlink(missing_ok=True)
    log(f"📱 Creando {len(chosen)} Shorts/Reels/TikToks verticales...")
    tags = list(HASHTAGS_BASE)
    if game:
        tags.insert(0, "#" + "".join(ch for ch in game.lower() if ch.isalnum()))
    if "gta" in (game or "").lower():
        tags += ["#gta", "#gtav", "#gtaonline"]
    outs = []
    lines = []
    for i, m in enumerate(chosen, 1):
        start, end = _fit_window(m, transcript, cfg, info["duration"])
        name = f"short_{i:02d}_{safe_name(m['title'], 40)}.mp4"
        log(f"   [{i}/{len(chosen)}] {m['title']} ({end - start:.0f}s, desde {fmt_time(start, True)})")
        out = render_short(video, info, m, start, end, transcript, sdir / name, cfg, work, i)
        outs.append(out)
        cat_tag = {"gracioso": "#humor #risas", "wow": "#epico #locura",
                   "profesional": "#pro #jugada", "raro": "#bug #raro",
                   "epico": "#epico"}.get(m["category"], "")
        lines += [f"=== {name} ===",
                  f"Título: {m['title']}",
                  f"Texto: {m['title']} 🎮 Directos en {cfg['twitch_url']}",
                  f"Hashtags: {' '.join(tags)} {cat_tag} #shorts #reels #fyp",
                  ""]
    (sdir / "textos_para_publicar.txt").write_text("\n".join(lines), encoding="utf-8")
    log(f"✅ {len(outs)} Shorts listos en la carpeta 'shorts'.")
    return outs
