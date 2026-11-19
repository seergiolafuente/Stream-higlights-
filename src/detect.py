"""Detección de los mejores momentos del directo.

Combina varias señales, segundo a segundo:
  • Audio: subidas de volumen respecto al ambiente (gritos, reacciones, explosiones).
  • Risa: patrón rítmico típico de las carcajadas + "jajaja" en la transcripción.
  • Vídeo: cantidad de movimiento y cortes de escena (acción en pantalla).
  • Palabras: expresiones de sorpresa, risa, jugadas pro o cosas raras.
  • Chat de Twitch (opcional): picos de mensajes y emotes (KEKW, Pog, LUL...).
Luego busca los picos, ajusta cada clip a frases completas y les asigna categoría.
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
import wave
from pathlib import Path

import numpy as np

from .utils import log, popen_ffmpeg, drain_stderr, fmt_time

CATEGORIES = ["gracioso", "wow", "profesional", "raro"]

CATEGORY_LABELS = {
    "gracioso": "MOMENTO GRACIOSO",
    "wow": "MOMENTO WOW",
    "profesional": "JUGADA PRO",
    "raro": "ESTO NO ES NORMAL",
    "epico": "MOMENTO ÉPICO",
}

DEFAULT_TITLES = {
    "gracioso": ["No puedo más", "Me meo", "Qué acaba de pasar", "Lo peor que me ha pasado"],
    "wow": ["¡Qué locura!", "Madre mía", "No me lo creo", "Brutal"],
    "profesional": ["Jugada de crack", "Esto es nivel pro", "Limpio", "Perfección"],
    "raro": ["Esto no es normal", "Pero qué es esto", "Nunca había visto esto", "Bug legendario"],
    "epico": ["Momento épico", "Qué momentazo", "Para el recuerdo"],
}

# --------------------------------------------------------------------------- #
# Vocabulario (español de España, jerga gamer y de stream)
# --------------------------------------------------------------------------- #
KEYWORDS = {
    "gracioso": [
        r"ja(ja)+", r"je(je)+", r"jiji", r"risas", r"me meo", r"me muero", r"no puedo",
        r"que cabron", r"que idiota", r"que tonto", r"pero que haces", r"payaso",
        r"me parto", r"lloro", r"xd+", r"que verguenza", r"ridiculo", r"que mal",
        r"me cago", r"que asco", r"tonto", r"subnormal",
    ],
    "wow": [
        r"madre mia", r"increible", r"que locura", r"locura", r"brutal", r"toma+",
        r"vamo+s+", r"ostia", r"hostia", r"no me lo creo", r"no puede ser", r"flipa",
        r"flipante", r"que bestia", r"bestial", r"tremendo", r"impresionante",
        r"wow+", r"que fuerte", r"dios mio", r"de locos", r"espectacular", r"epico",
        r"pero bueno", r"como", r"siii+", r"noo+",
    ],
    "profesional": [
        r"headshot", r"a la cabeza", r"clutch", r"ace", r"uno contra", r"1 ?v ?\d",
        r"limpio", r"perfecto", r"crack", r"maquina", r"mvp", r"pro", r"nivel",
        r"tiro", r"punteria", r"carrera", r"record", r"primero", r"victoria", r"gane",
        r"hemos ganado", r"gg", r"lo tengo", r"facil", r"de manual", r"mision completada",
    ],
    "raro": [
        r"que ha pasado", r"que es eso", r"bug", r"bugeado", r"glitch", r"nunca habia visto",
        r"imposible", r"que raro", r"raro", r"que coño", r"pero que", r"como es posible",
        r"esto es nuevo", r"que cojones", r"wtf", r"se ha vuelto loco", r"volando",
    ],
}

CHAT_EMOTES = {
    "gracioso": ["kekw", "lul", "lol", "omegalul", "lmao", "xd", "jaja", "pepelaugh",
                 "icant", "kek", "😂", "🤣", "haha", "lulw", "gigachad"],
    "wow": ["pog", "pogchamp", "poggers", "pogu", "wow", "omg", "hype", "clip", "clipea",
            "clip it", "🔥", "😱", "😮", "monkas", "pausechamp", "locura", "madre mia"],
    "profesional": ["gg", "ez", "crack", "maquina", "bestia", "god", "clean", "nice",
                    "insane", "goat", "mvp", "👏", "💪"],
    "raro": ["wtf", "???", "??", "que", "bug", "monkaw", "🤔", "😳", "hmm"],
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


# --------------------------------------------------------------------------- #
# Utilidades numéricas
# --------------------------------------------------------------------------- #
def _smooth(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1 or len(x) == 0:
        return x.astype(float)
    k = np.ones(win) / win
    return np.convolve(x, k, mode="same")


def _rolling_median(x: np.ndarray, win: int) -> np.ndarray:
    """Mediana móvil aproximada (por bloques) — rápida incluso en directos de 8 h."""
    n = len(x)
    if n == 0:
        return x
    step = max(1, win // 6)
    centers = np.arange(0, n, step)
    meds = np.array([np.median(x[max(0, c - win // 2): c + win // 2 + 1]) for c in centers])
    return np.interp(np.arange(n), centers, meds)


def _robust01(x: np.ndarray, clip: float = 5.0) -> np.ndarray:
    """Normaliza a 0..1 por desviación robusta: solo lo que destaca sube."""
    if len(x) == 0:
        return x
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826 + 1e-9
    z = (x - med) / mad
    return np.clip(z, 0, clip) / clip


# --------------------------------------------------------------------------- #
# Señales de audio
# --------------------------------------------------------------------------- #
def audio_features(wav_path: Path, duration: float) -> dict:
    log("🔊 Analizando el audio (gritos, reacciones, risas)...")
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        nframes = w.getnframes()
        raw = w.readframes(nframes)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    n_sec = int(np.ceil(duration)) if duration else int(len(audio) / sr)
    hop = sr // 50  # tramos de 20 ms
    n_hops = len(audio) // hop
    frames = audio[: n_hops * hop].reshape(n_hops, hop)
    env = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-10)  # envolvente RMS (50 Hz)
    env_db = 20 * np.log10(env + 1e-6)

    loud = np.full(n_sec, -90.0)
    peak = np.full(n_sec, -90.0)
    laugh = np.zeros(n_sec)
    for s in range(min(n_sec, n_hops // 50)):
        seg = env_db[s * 50:(s + 1) * 50]
        loud[s] = float(np.mean(seg))
        peak[s] = float(np.percentile(seg, 95))
    # Excitación: volumen respecto al ambiente de los últimos/próximos ~3 min
    base = _rolling_median(loud, 180)
    excite = np.clip((0.6 * (loud - base) + 0.4 * (peak - base - 6)) / 12.0, 0, 1)
    # Silencios largos (pantallas de carga, AFK) no deben puntuar
    silent = loud < (np.median(loud) - 15)

    # Risa: modulación rítmica de 4–7 Hz en la envolvente (carcajadas "ja-ja-ja")
    e = env - _smooth(env, 25)
    for s in range(min(n_sec, n_hops // 50)):
        a, b = max(0, (s - 1) * 50), min(len(e), (s + 2) * 50)
        x = e[a:b]
        if len(x) < 60 or loud[s] < base[s] - 3:
            continue
        x = x - x.mean()
        denom = float(np.dot(x, x)) + 1e-12
        ac = np.array([np.dot(x[:-lag], x[lag:]) / denom for lag in range(5, 15)])
        # retardos 7..12 tramos (=4..7 Hz) frente al resto
        rhythmic = float(ac[2:8].max())
        laugh[s] = max(0.0, rhythmic - 0.15)
    laugh = np.clip(_smooth(laugh, 3) * 2.2, 0, 1)

    return {"excite": excite, "laugh": laugh, "silent": silent, "loud": loud}


# --------------------------------------------------------------------------- #
# Señales de vídeo
# --------------------------------------------------------------------------- #
def video_features(video: Path, duration: float) -> dict:
    """Movimiento y cortes de escena. Solo decodifica fotogramas clave (muy rápido)."""
    log("🎬 Analizando la imagen (acción y cambios de escena)...")
    n_sec = int(np.ceil(duration))
    W, H = 64, 36
    fsize = W * H

    def _scan(extra_in: list[str], vf_prefix: str):
        proc = popen_ffmpeg(
            ["-loglevel", "info", *extra_in, "-i", str(video), "-an", "-sn",
             "-vf", f"{vf_prefix}scale={W}:{H},format=gray,showinfo",
             "-fps_mode", "passthrough", "-f", "rawvideo", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        times: list[float] = []
        pat = re.compile(r"pts_time:\s*([0-9.]+)")

        def on_line(line: str):
            m = pat.search(line)
            if m and "showinfo" in line:
                times.append(float(m.group(1)))
        t = drain_stderr(proc, on_line)
        frames = []
        while True:
            buf = proc.stdout.read(fsize)
            if not buf or len(buf) < fsize:
                break
            frames.append(np.frombuffer(buf, dtype=np.uint8))
        proc.wait()
        t.join(timeout=5)
        k = min(len(frames), len(times))
        return np.array(times[:k]), (np.stack(frames[:k]).astype(np.float32) if k else None)

    times, frames = _scan(["-skip_frame", "nokey"], "")
    if frames is None or len(times) < max(10, duration / 6):
        # Pocos fotogramas clave: analizo a 1 fps (más lento pero fiable)
        times, frames = _scan([], "fps=1,")
    motion = np.zeros(n_sec)
    scene = np.zeros(n_sec)
    if frames is not None and len(frames) > 2:
        diffs = np.abs(np.diff(frames, axis=0)).mean(axis=1) / 255.0
        hist = [np.histogram(f, bins=16, range=(0, 255))[0] / fsize for f in frames]
        hdiff = np.array([np.abs(hist[i + 1] - hist[i]).sum() / 2 for i in range(len(hist) - 1)])
        t_mid = times[1:]
        idx = np.clip(t_mid.astype(int), 0, n_sec - 1)
        np.maximum.at(motion, idx, diffs)
        np.maximum.at(scene, idx, (hdiff > 0.35).astype(float))
        # rellenar huecos entre fotogramas clave
        filled = np.interp(np.arange(n_sec), idx, diffs) if len(idx) > 1 else motion
        motion = np.maximum(motion, filled)
    return {"motion": _robust01(_smooth(motion, 3)), "scene": scene}


# --------------------------------------------------------------------------- #
# Señales de la transcripción
# --------------------------------------------------------------------------- #
_KW_RE = {cat: re.compile(r"\b(" + "|".join(words) + r")\b") for cat, words in KEYWORDS.items()}


def transcript_features(transcript: dict, duration: float) -> dict:
    n_sec = int(np.ceil(duration))
    cats = {c: np.zeros(n_sec) for c in CATEGORIES}
    speech = np.zeros(n_sec)
    for seg in transcript.get("segments", []):
        text = _norm(seg["text"])
        a, b = int(seg["start"]), min(n_sec - 1, int(seg["end"]))
        if b < a:
            continue
        speech[a:b + 1] = 1
        excl = text.count("!") + text.count("?") * 0.5
        for cat, rx in _KW_RE.items():
            hits = len(rx.findall(text))
            if hits:
                # se marca en el punto exacto de la palabra si hay marcas por palabra
                pos = a
                for w in seg.get("words", []):
                    if rx.search(_norm(w["word"])):
                        pos = int(w["start"])
                        break
                cats[cat][min(pos, n_sec - 1)] += hits
        if excl:
            cats["wow"][a] += 0.3 * excl
    out = {c: np.clip(_smooth(v, 5) * 2.0, 0, 1) for c, v in cats.items()}
    out["speech"] = speech
    return out


# --------------------------------------------------------------------------- #
# Chat de Twitch (opcional)
# --------------------------------------------------------------------------- #
def _parse_hms(s: str) -> float | None:
    m = re.match(r"\[?\s*(\d+):(\d{2}):(\d{2})(?:\.\d+)?\s*\]?", s.strip())
    if m:
        h, mi, se = map(int, m.groups())
        return h * 3600 + mi * 60 + se
    m = re.match(r"\[?\s*(\d+):(\d{2})\s*\]?", s.strip())
    if m:
        mi, se = map(int, m.groups())
        return mi * 60 + se
    return None


def load_chat(path: Path) -> list[tuple[float, str]]:
    """Admite: JSON de TwitchDownloader, JSON de chat-downloader, o texto
    con líneas tipo "[0:12:34] usuario: mensaje"."""
    msgs: list[tuple[float, str]] = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        items = data.get("comments", data) if isinstance(data, dict) else data
        for c in items:
            if not isinstance(c, dict):
                continue
            t = c.get("content_offset_seconds", c.get("time_in_seconds", c.get("offset")))
            body = c.get("message", "")
            if isinstance(body, dict):
                body = body.get("body", "")
            if t is not None:
                msgs.append((float(t), str(body)))
    else:
        for line in raw.splitlines():
            m = re.match(r"\s*(\[?\d+:\d{2}(?::\d{2})?(?:\.\d+)?\]?)\s+(.*)", line)
            if m:
                t = _parse_hms(m.group(1))
                if t is not None:
                    body = m.group(2).split(":", 1)[-1]
                    msgs.append((t, body))
    return msgs


def chat_features(chat_path: Path | None, duration: float) -> dict | None:
    if not chat_path:
        return None
    try:
        msgs = load_chat(Path(chat_path))
    except Exception as e:
        log(f"⚠️  No pude leer el chat ({e}). Sigo sin él.")
        return None
    if not msgs:
        log("⚠️  El archivo de chat no tiene mensajes reconocibles.")
        return None
    log(f"💬 Analizando {len(msgs)} mensajes del chat...")
    n_sec = int(np.ceil(duration))
    rate = np.zeros(n_sec)
    cats = {c: np.zeros(n_sec) for c in CATEGORIES}
    for t, body in msgs:
        i = int(t)
        if not 0 <= i < n_sec:
            continue
        rate[i] += 1
        low = _norm(body)
        for cat, ems in CHAT_EMOTES.items():
            if any(e in low for e in ems):
                cats[cat][i] += 1
    # el chat reacciona con ~5-10 s de retraso: desplazamos hacia atrás
    shift = 6
    rate = np.concatenate([rate[shift:], np.zeros(shift)])
    for c in cats:
        cats[c] = np.concatenate([cats[c][shift:], np.zeros(shift)])
    rate_s = _smooth(rate, 10)
    base = _rolling_median(rate_s, 300) + 0.2
    burst = np.clip((rate_s / base - 1.0) / 3.0, 0, 1)
    out = {"burst": burst}
    for c, v in cats.items():
        out[c] = _robust01(_smooth(v, 10))
    return out


# --------------------------------------------------------------------------- #
# Combinación y extracción de momentos
# --------------------------------------------------------------------------- #
def build_timeline(audio: dict, video: dict, text: dict, chat: dict | None,
                   weights: dict, duration: float) -> dict:
    n = int(np.ceil(duration))

    def fit(x):
        x = np.asarray(x, dtype=float)
        return x[:n] if len(x) >= n else np.concatenate([x, np.zeros(n - len(x))])

    excite, laugh, motion, scene = (fit(audio["excite"]), fit(audio["laugh"]),
                                    fit(video["motion"]), fit(video["scene"]))
    kw = {c: fit(text[c]) for c in CATEGORIES}
    laugh = np.clip(laugh + 0.8 * kw["gracioso"], 0, 1)
    ch = {k: fit(v) for k, v in chat.items()} if chat else None

    w = weights
    score = (w["audio"] * excite + w["risa"] * laugh + w["movimiento"] * motion * 0.6
             + w["corte_escena"] * _smooth(scene, 4)
             + w["palabras"] * (kw["wow"] + kw["profesional"] + kw["raro"]) / 2)
    if ch:
        score += w["chat"] * (ch["burst"] + 0.5 * sum(ch[c] for c in CATEGORIES))
    score = score * np.where(fit(audio["silent"]) > 0, 0.3, 1.0)
    score = _smooth(score, 5)

    # Para la categoría pesan más las palabras y el chat (dicen QUÉ pasa);
    # el volumen solo dice que pasa ALGO.
    per_cat = {
        "gracioso": laugh * 1.5 + kw["gracioso"] * 2 + (ch["gracioso"] * 2 if ch else 0),
        "wow": 0.35 * excite + kw["wow"] * 1.6 + 0.15 * motion + (ch["wow"] * 2 if ch else 0),
        "profesional": kw["profesional"] * 2.2 + (ch["profesional"] * 2 if ch else 0),
        "raro": kw["raro"] * 2.2 + (ch["raro"] * 2 if ch else 0),
    }
    return {"score": score, "per_cat": per_cat, "loud": fit(audio["loud"]),
            "excite": excite, "laugh": laugh, "motion": motion,
            "chat": ch["burst"] if ch else np.zeros(n)}


def _snap_bounds(start: float, end: float, transcript: dict, loud: np.ndarray,
                 duration: float) -> tuple[float, float]:
    """Ajusta inicio/fin para no cortar frases a la mitad y caer en pausas."""
    segs = transcript.get("segments", [])
    if segs:
        # inicio: comienzo de la frase que esté en curso (máx. 6 s antes)
        for s in segs:
            if s["start"] <= start < s["end"] and start - s["start"] <= 6:
                start = s["start"] - 0.25
                break
        # fin: final de la frase en curso (máx. 6 s después)
        for s in segs:
            if s["start"] < end <= s["end"] and s["end"] - end <= 6:
                end = s["end"] + 0.6
                break
    else:
        # sin transcripción: buscamos el segundo más tranquilo cercano
        def quiet(t, lo, hi):
            a, b = int(max(0, t + lo)), int(min(len(loud) - 1, t + hi))
            if b <= a:
                return t
            return a + int(np.argmin(loud[a:b + 1]))
        start = float(quiet(start, -3, 2))
        end = float(quiet(end, -1, 3)) + 0.5
    return max(0.0, start), min(duration, end)


def _best_quote(transcript: dict, start: float, end: float, cat: str) -> str | None:
    best, best_score = None, 0.0
    rx = _KW_RE.get(cat)
    for s in transcript.get("segments", []):
        if s["end"] < start or s["start"] > end:
            continue
        txt = s["text"].strip()
        if not 3 <= len(txt.split()) <= 9:
            continue
        sc = txt.count("!") * 0.5 + (len(rx.findall(_norm(txt))) if rx else 0)
        if sc > best_score:
            best, best_score = txt, sc
    return best.strip(" .,¡!¿?").capitalize() if best else None


def find_moments(tl: dict, transcript: dict, duration: float, cfg: dict,
                 max_moments: int = 120) -> list[dict]:
    score = tl["score"]
    n = len(score)
    if n == 0:
        return []
    ycfg = cfg["youtube"]
    thr = float(np.percentile(score, 70))
    min_gap = 25
    # picos locales
    cand = [i for i in range(2, n - 2)
            if score[i] >= thr and score[i] == score[max(0, i - 10): i + 11].max()]
    cand.sort(key=lambda i: score[i], reverse=True)
    chosen: list[int] = []
    for i in cand:
        if all(abs(i - j) >= min_gap for j in chosen):
            chosen.append(i)
        if len(chosen) >= max_moments:
            break

    smax = float(score.max()) + 1e-9
    moments = []
    for k, p in enumerate(chosen):
        # extensión según cuánto dura la "zona caliente" alrededor del pico
        level = score[p] * 0.45
        a = p
        while a > 0 and score[a - 1] > level and p - a < 40:
            a -= 1
        b = p
        while b < n - 1 and score[b + 1] > level and b - p < 30:
            b += 1
        start = min(a, p - 10) - 2   # contexto: lo que pasa antes del pico
        end = max(b, p + 6) + 2      # la reacción después
        start, end = _snap_bounds(start, end, transcript, tl["loud"], duration)
        if end - start > ycfg["clip_max_seg"]:
            start = max(start, p - ycfg["clip_max_seg"] * 0.6)
            end = start + ycfg["clip_max_seg"]
        if end - start < ycfg["clip_min_seg"]:
            pad = (ycfg["clip_min_seg"] - (end - start)) / 2
            start, end = max(0, start - pad), min(duration, end + pad)
        cat_scores = {c: float(v[int(max(0, start)):int(end) + 1].sum()) for c, v in tl["per_cat"].items()}
        cat = max(cat_scores, key=cat_scores.get) if max(cat_scores.values()) > 0 else "wow"
        reasons = []
        if tl["excite"][p] > 0.5: reasons.append("subida fuerte de voz/sonido")
        if tl["laugh"][max(0, p - 5):p + 5].max() > 0.5: reasons.append("risas")
        if tl["motion"][p] > 0.6: reasons.append("mucha acción en pantalla")
        if tl["chat"][max(0, p - 5):p + 5].max() > 0.4: reasons.append("el chat explota")
        quote = _best_quote(transcript, start, end, cat)
        title = quote or DEFAULT_TITLES[cat][k % len(DEFAULT_TITLES[cat])]
        moments.append({
            "id": f"m{k + 1:03d}",
            "start": round(float(start), 2),
            "end": round(float(end), 2),
            "peak": float(p),
            "score": round(float(score[p] / smax) * 10, 2),
            "category": cat,
            "title": title,
            "hook": title,
            "short_ok": True,
            "source": "señales",
            "reasons": reasons,
        })
    log(f"   Encontrados {len(moments)} momentos candidatos por señales.")
    return moments


def overlap(a: dict, b: dict) -> float:
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    return max(0.0, inter) / max(1e-6, min(a["end"] - a["start"], b["end"] - b["start"]))


def timeline_peaks_text(tl: dict, top: int = 60) -> list[str]:
    """Resumen de picos para que la IA sepa dónde hubo reacciones fuertes."""
    score = tl["score"]
    idx = np.argsort(score)[::-1]
    out, used = [], []
    for i in idx:
        if len(out) >= top:
            break
        if any(abs(i - j) < 20 for j in used):
            continue
        used.append(int(i))
        tags = []
        if tl["excite"][i] > 0.5: tags.append("grito/sonido fuerte")
        if tl["laugh"][max(0, i - 4):i + 4].max() > 0.5: tags.append("risas")
        if tl["motion"][i] > 0.6: tags.append("mucha acción")
        if tl["chat"][max(0, i - 4):i + 4].max() > 0.4: tags.append("chat explota")
        out.append(f"[{fmt_time(i, True)}] intensidad {score[i]:.2f} {', '.join(tags)}")
    return sorted(out)
