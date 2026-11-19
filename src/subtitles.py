"""Generación de subtítulos (.srt) y rótulos/tarjetas (.ass, renderizados con libass)."""
from __future__ import annotations

from pathlib import Path

from .utils import ass_escape, ass_time, srt_time

# Colores ASS en formato &HAABBGGRR
WHITE = "&H00FFFFFF"
BLACK = "&H00000000"
YELLOW = "&H0000E5FF"   # amarillo cálido
ACCENT = "&H00F05A91"   # morado tipo Twitch (BGR de #915AF0)
CAT_COLORS = {
    "gracioso": "&H0000D5FF",
    "wow": "&H003A3AFF",
    "profesional": "&H0066FF33",
    "raro": "&H00FF66CC",
    "epico": "&H0000A5FF",
}


def _header(w: int, h: int, styles: list[str]) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {w}\nPlayResY: {h}\nYCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + "\n".join(styles) +
        "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _style(name, font, size, primary=WHITE, outline=BLACK, back="&H80000000", bold=-1,
           border_style=1, outline_w=4, shadow=2, align=5, ml=40, mr=40, mv=40, spacing=0):
    return (f"Style: {name},{font},{size},{primary},{WHITE},{outline},{back},{bold},0,0,0,"
            f"100,100,{spacing},0,{border_style},{outline_w},{shadow},{align},{ml},{mr},{mv},1")


def _ev(start, end, style, text, layer=0):
    return f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}\n"


# --------------------------------------------------------------------------- #
# Vídeo largo (YouTube)
# --------------------------------------------------------------------------- #
def lower_third_ass(path: Path, w: int, h: int, cfg: dict, category_label: str,
                    category: str, title: str, dur: float) -> Path:
    s = h / 1080
    font = cfg.get("fuente", "Arial Black")
    styles = [
        _style("Badge", font, int(30 * s), primary=BLACK, outline=CAT_COLORS.get(category, YELLOW),
               back=CAT_COLORS.get(category, YELLOW), border_style=3, outline_w=int(10 * s),
               shadow=0, align=1, ml=int(70 * s), mv=int(150 * s), spacing=2),
        _style("Title", font, int(52 * s), primary=WHITE, outline=BLACK, back="&H96000000",
               border_style=3, outline_w=int(14 * s), shadow=0, align=1, ml=int(70 * s),
               mv=int(70 * s)),
    ]
    t_end = min(dur - 0.2, 4.2)
    ev = ""
    if t_end > 1.0:
        ev += _ev(0.3, t_end, "Badge", r"{\fad(200,250)}" + ass_escape(category_label))
        ev += _ev(0.45, t_end, "Title", r"{\fad(250,250)}" + ass_escape(title.upper()))
    path.write_text(_header(w, h, styles) + ev, encoding="utf-8")
    return path


def title_card_ass(path: Path, w: int, h: int, cfg: dict, title: str, subtitle: str,
                   dur: float) -> Path:
    s = h / 1080
    font = cfg.get("fuente", "Arial Black")
    styles = [
        _style("Big", font, int(96 * s), outline_w=int(8 * s), shadow=int(4 * s), align=5),
        _style("Small", font, int(40 * s), primary=YELLOW, outline_w=int(5 * s), shadow=2, align=2,
               mv=int(230 * s), spacing=3),
    ]
    ev = _ev(0, dur, "Big", r"{\fad(250,300)\fscx85\fscy85\t(0,350,\fscx100\fscy100)}"
             + ass_escape(_wrap(title.upper(), 22)))
    ev += _ev(0.3, dur, "Small", r"{\fad(300,300)}" + ass_escape(subtitle.upper()))
    path.write_text(_header(w, h, styles) + ev, encoding="utf-8")
    return path


def outro_ass(path: Path, w: int, h: int, cfg: dict, dur: float) -> Path:
    s = h / 1080
    font = cfg.get("fuente", "Arial Black")
    styles = [
        _style("Big", font, int(76 * s), outline_w=int(6 * s), shadow=3, align=8, mv=int(110 * s)),
        _style("Small", font, int(44 * s), primary=YELLOW, outline_w=int(5 * s), shadow=2, align=8,
               mv=int(215 * s)),
    ]
    ev = _ev(0.2, dur, "Big", r"{\fad(300,400)}GRACIAS POR VERLO")
    ev += _ev(0.5, dur, "Small", r"{\fad(300,400)}" +
              ass_escape(f"DIRECTOS EN {cfg['twitch_url'].upper()}"))
    path.write_text(_header(w, h, styles) + ev, encoding="utf-8")
    return path


def thumbnail_ass(path: Path, w: int, h: int, cfg: dict, text: str, category: str) -> Path:
    font = cfg.get("fuente", "Arial Black")
    s = h / 720
    styles = [_style("T", font, int(118 * s), primary=WHITE, outline=BLACK,
                     outline_w=int(12 * s), shadow=int(6 * s), align=1, ml=int(50 * s),
                     mv=int(50 * s))]
    words = text.upper().split()
    # última palabra resaltada en el color de la categoría (estilo YouTube)
    if len(words) > 1:
        body = " ".join(words[:-1]) + r"\N{\c" + CAT_COLORS.get(category, YELLOW) + "&}" + words[-1]
    else:
        body = r"{\c" + CAT_COLORS.get(category, YELLOW) + "&}" + (words[0] if words else "")
    path.write_text(_header(w, h, styles) + _ev(0, 10, "T", body), encoding="utf-8")
    return path


def _wrap(text: str, width: int) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def build_srt(path: Path, transcript: dict, mapping: list[tuple[float, float, float]]) -> Path:
    """mapping: (inicio_original, fin_original, inicio_en_video_final)"""
    out, n = [], 0
    for src_a, src_b, dst in mapping:
        for seg in transcript.get("segments", []):
            if seg["end"] <= src_a or seg["start"] >= src_b:
                continue
            a = max(seg["start"], src_a) - src_a + dst
            b = min(seg["end"], src_b) - src_a + dst
            if b - a < 0.3:
                continue
            n += 1
            out.append(f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{seg['text'].strip()}\n")
    path.write_text("\n".join(out), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Shorts (vertical 1080x1920)
# --------------------------------------------------------------------------- #
def short_ass(path: Path, transcript: dict, start: float, end: float, hook: str,
              cfg: dict, layout: str, category: str) -> Path:
    W, H = 1080, 1920
    scfg = cfg["shorts"]
    font = cfg.get("fuente_subtitulos", "Arial Black")
    sub_y = 1460 if layout == "blur" else 1180     # MarginV desde abajo (align 2)
    mv = H - sub_y
    styles = [
        _style("Sub", font, 84, primary=WHITE, outline=BLACK, outline_w=7, shadow=3, align=2,
               ml=70, mr=70, mv=mv),
        _style("Hook", font, 70, primary=BLACK, outline=WHITE, back=WHITE, border_style=3,
               outline_w=18, shadow=0, align=8, ml=80, mr=80, mv=250 if layout == "blur" else 120),
    ]
    ev = ""
    dur = end - start
    if hook:
        ev += _ev(0, min(dur, 3.8), "Hook",
                  r"{\fad(120,250)\fscx70\fscy70\t(0,160,\fscx105\fscy105)\t(160,260,\fscx100\fscy100)}"
                  + ass_escape(_wrap(hook.upper(), 18)), layer=2)
    if scfg.get("subtitulos", True):
        words = []
        for seg in transcript.get("segments", []):
            if seg["end"] < start or seg["start"] > end:
                continue
            if seg.get("words"):
                words += [w for w in seg["words"] if start <= w["start"] < end]
            else:  # sin marcas por palabra: repartimos el tiempo de la frase
                ws = seg["text"].split()
                if not ws:
                    continue
                step = (seg["end"] - seg["start"]) / len(ws)
                words += [{"start": seg["start"] + i * step, "end": seg["start"] + (i + 1) * step,
                           "word": w} for i, w in enumerate(ws) if start <= seg["start"] + i * step < end]
        per = max(1, int(scfg.get("palabras_por_bloque", 3)))
        hl = CAT_COLORS.get(category, YELLOW)
        i = 0
        while i < len(words):
            group = words[i:i + per]
            # cortar el bloque si hay una pausa larga
            for j in range(1, len(group)):
                if group[j]["start"] - group[j - 1]["end"] > 0.7:
                    group = group[:j]
                    break
            g_end = group[-1]["end"]
            nxt = words[i + len(group)]["start"] if i + len(group) < len(words) else g_end + 0.5
            g_end = min(max(g_end, group[0]["start"] + 0.35), nxt)
            for k, wd in enumerate(group):
                a = wd["start"] - start
                b = (group[k + 1]["start"] if k + 1 < len(group) else g_end) - start
                if b <= a:
                    continue
                parts = []
                for kk, w2 in enumerate(group):
                    txt = ass_escape(w2["word"].upper().strip(" ,."))
                    parts.append(r"{\c" + hl + "&}" + txt + r"{\c" + WHITE + "&}" if kk == k else txt)
                pop = r"{\fscx92\fscy92\t(0,90,\fscx100\fscy100)}" if k == 0 else ""
                ev += _ev(max(0, a), max(0, b), "Sub", pop + " ".join(parts), layer=1)
            i += len(group)
    path.write_text(_header(W, H, styles) + ev, encoding="utf-8")
    return path
