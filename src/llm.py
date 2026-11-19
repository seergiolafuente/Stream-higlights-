"""Análisis inteligente con Claude (opcional).

Claude lee la transcripción completa con marcas de tiempo + los picos de
intensidad detectados, y decide qué momentos son realmente buenos (graciosos,
wow, jugadas pro, cosas raras), les pone título, gancho para Shorts y genera
título, descripción y etiquetas para YouTube.

Necesita la variable de entorno ANTHROPIC_API_KEY (o `ant auth login`).
Si no está disponible, el programa funciona igual solo con las señales.
"""
from __future__ import annotations

import json
import os

from .utils import log, fmt_time

SCHEMA = {
    "type": "object",
    "properties": {
        "momentos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "inicio": {"type": "string", "description": "H:MM:SS"},
                    "fin": {"type": "string", "description": "H:MM:SS"},
                    "categoria": {"type": "string",
                                  "enum": ["gracioso", "wow", "profesional", "raro", "epico"]},
                    "titulo": {"type": "string"},
                    "gancho": {"type": "string"},
                    "puntuacion": {"type": "integer"},
                    "apto_short": {"type": "boolean"},
                    "motivo": {"type": "string"},
                },
                "required": ["inicio", "fin", "categoria", "titulo", "gancho",
                             "puntuacion", "apto_short", "motivo"],
                "additionalProperties": False,
            },
        },
        "youtube": {
            "type": "object",
            "properties": {
                "titulos": {"type": "array", "items": {"type": "string"}},
                "descripcion": {"type": "string"},
                "etiquetas": {"type": "array", "items": {"type": "string"}},
                "texto_miniatura": {"type": "string"},
                "juego": {"type": "string"},
            },
            "required": ["titulos", "descripcion", "etiquetas", "texto_miniatura", "juego"],
            "additionalProperties": False,
        },
    },
    "required": ["momentos", "youtube"],
    "additionalProperties": False,
}

SYSTEM = """Eres el editor de vídeo de un streamer español de Twitch (canal {canal}), centrado sobre todo en GTA V (y GTA VI), aunque también juega a otras cosas.
Tu trabajo: a partir de la transcripción con marcas de tiempo de un directo y de los picos de intensidad (volumen, risas, acción, chat), elegir los MEJORES momentos para:
 1) un vídeo resumen de YouTube de {min_min}–{max_min} minutos,
 2) varios Shorts/Reels/TikToks verticales de 15–58 segundos.

Qué buscas (en este orden de prioridad):
 - "gracioso": situaciones que dan risa de verdad, reacciones exageradas, fails, bromas con el chat.
 - "wow": algo impresionante, persecuciones, explosiones, sustos, giros inesperados.
 - "profesional": jugadas muy bien hechas, conducción/puntería perfecta, misiones limpias, clutch.
 - "raro": bugs, cosas poco comunes, situaciones absurdas que casi nunca pasan.
 - "epico": momentos de clímax (victoria, final de misión, logro).

Reglas:
 - Cada momento debe entenderse solo: empieza un poco ANTES de lo que pasa (contexto) y termina DESPUÉS de la reacción. Nunca cortes una frase a la mitad.
 - Duración de cada momento: entre 12 y 75 segundos.
 - Sin solapes entre momentos.
 - Propón suficientes momentos para sumar al menos {target_min} minutos en total (mejor de más que de menos; se descartarán los peores). Ordénalos como quieras.
 - Descarta charlas de relleno, pantallas de carga, esperas, publicidad y silencios.
 - "puntuacion" de 1 a 10 (10 = clip viral).
 - "apto_short": true solo si el momento funciona sin contexto en vertical en menos de 58 s.
 - "titulo": corto, natural, en español de España, sin clickbait falso. Máx. 8 palabras.
 - "gancho": texto de 2 a 6 palabras EN MAYÚSCULAS para la parte superior del Short (que enganche en el primer segundo). Sin emojis.
 - YouTube: 3 títulos atractivos (máx. 70 caracteres, sin emojis), descripción de 3–6 líneas en primera persona invitando a seguir el canal en Twitch ({twitch}), 12–20 etiquetas, y un texto de miniatura de 2–4 palabras en mayúsculas.
 - Nada de llamadas a la acción genéricas tipo "dale al botón"; invita de forma natural a ver los directos.
 - Usa las marcas de tiempo de la transcripción; formato H:MM:SS.
"""


def _to_sec(t: str) -> float:
    parts = [float(p) for p in str(t).strip().split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def _transcript_text(transcript: dict) -> str:
    lines = []
    for s in transcript.get("segments", []):
        lines.append(f"[{fmt_time(s['start'], True)}] {s['text']}")
    return "\n".join(lines)


def available(cfg: dict) -> bool:
    if not cfg["ia_claude"].get("activar", True):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.path.isdir(os.path.expanduser("~/.config/anthropic")))


def analyze(transcript: dict, peaks: list[str], duration: float, cfg: dict) -> dict | None:
    if not available(cfg):
        log("ℹ️  IA de Claude desactivada o sin clave (ANTHROPIC_API_KEY). Uso solo las señales.")
        return None
    if not transcript.get("segments"):
        log("ℹ️  Sin transcripción no tiene sentido llamar a Claude. Uso solo las señales.")
        return None
    import anthropic

    y = cfg["youtube"]
    system = SYSTEM.format(canal=cfg["canal"], twitch=cfg["twitch_url"],
                           min_min=y["duracion_minima_min"], max_min=y["duracion_maxima_min"],
                           target_min=int(y["duracion_objetivo_min"] * 1.5))
    user = (f"Duración total del directo: {fmt_time(duration, True)}\n\n"
            f"PICOS DE INTENSIDAD DETECTADOS (audio/risas/acción/chat):\n" + "\n".join(peaks) +
            "\n\nTRANSCRIPCIÓN:\n" + _transcript_text(transcript))
    log("🧠 Claude está viendo la transcripción para elegir los mejores momentos...")
    client = anthropic.Anthropic()
    model = cfg["ia_claude"].get("modelo", "claude-opus-5-5")
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=32000,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": cfg["ia_claude"].get("esfuerzo", "high"),
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        log("⚠️  La clave de Claude no es válida. Sigo solo con las señales.")
        return None
    except anthropic.RateLimitError:
        log("⚠️  Claude está saturado (límite de uso). Sigo solo con las señales.")
        return None
    except anthropic.APIStatusError as e:
        log(f"⚠️  Error de la API de Claude ({e.status_code}): {e.message}. Sigo sin IA.")
        return None
    except anthropic.APIConnectionError:
        log("⚠️  Sin conexión con Claude. Sigo solo con las señales.")
        return None

    if msg.stop_reason == "refusal":
        log("⚠️  Claude no ha querido analizar este contenido. Sigo solo con las señales.")
        return None
    if msg.stop_reason == "max_tokens":
        log("⚠️  La respuesta de Claude se cortó. Sigo solo con las señales.")
        return None
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log("⚠️  Respuesta de Claude no válida. Sigo solo con las señales.")
        return None

    moments = []
    for i, m in enumerate(data.get("momentos", [])):
        try:
            a, b = _to_sec(m["inicio"]), _to_sec(m["fin"])
        except ValueError:
            continue
        if b - a < 5 or a >= duration:
            continue
        moments.append({
            "id": f"ia{i + 1:03d}",
            "start": max(0.0, a), "end": min(duration, b), "peak": (a + b) / 2,
            "score": float(max(1, min(10, m["puntuacion"]))),
            "category": m["categoria"], "title": m["titulo"].strip(),
            "hook": m["gancho"].strip().upper(), "short_ok": bool(m["apto_short"]),
            "source": "ia", "reasons": [m["motivo"]],
        })
    log(f"   Claude ha propuesto {len(moments)} momentos.")
    return {"moments": moments, "youtube": data.get("youtube", {})}
