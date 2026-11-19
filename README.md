# 🎮 Stream Highlights

Programa para Windows que coge el vídeo de tu directo de Twitch, **lo analiza solo**, saca
los mejores momentos y te monta:

1. **Un vídeo resumen para YouTube** (16:9, 1080p, **mínimo 10 minutos**), listo para subir.
2. **Varios Shorts / Reels / TikToks** verticales (9:16, 15–58 s) con subtítulos dinámicos.

```
Stream-higlights-/
├── input/            ← aquí dejas el vídeo del directo (y el chat si lo tienes)
├── output/           ← aquí aparecen los vídeos terminados
├── src/
│   ├── transcribe.py ← pasa tu voz a texto (Whisper, usa tu RTX)
│   ├── detect.py     ← detecta los momentos (audio, risas, imagen, palabras, chat)
│   ├── llm.py        ← (opcional) Claude elige los mejores y pone títulos
│   ├── edit_long.py  ← monta el vídeo de YouTube
│   ├── edit_shorts.py← monta los Shorts verticales
│   ├── subtitles.py  ← subtítulos y rótulos
│   ├── main.py       ← proceso completo / línea de comandos
│   └── gui.py        ← la ventana del programa
├── config.json       ← todos los ajustes
├── instalar.bat      ← instalación (una sola vez)
├── iniciar.bat       ← abre el programa
└── procesar.bat      ← arrastra un vídeo encima y lo procesa sin abrir ventana
```

---

## 1. Instalación (una vez)

1. Descarga esta carpeta en tu PC (botón **Code → Download ZIP** en GitHub y descomprímela).
2. Doble clic en **`instalar.bat`**. Instala Python, FFmpeg y lo necesario.
   - Si te dice que cierres y vuelvas a abrirlo, hazlo.
3. (Opcional, pero recomendado) **IA de Claude** para elegir mejor los momentos y crear los
   títulos/descripciones: crea una clave en https://console.anthropic.com y añádela como
   variable de entorno de Windows llamada `ANTHROPIC_API_KEY`
   (Inicio → "variables de entorno" → Nueva). Sin clave el programa funciona igual, solo con
   el análisis automático.

> La primera vez que transcriba descargará el modelo de voz (~1,5 GB). Después ya no.

## 2. Uso

1. Descarga el vídeo de tu directo (VOD) de Twitch. Recomendado:
   [TwitchDownloader](https://github.com/lay295/TwitchDownloader) → descarga el **vídeo** y,
   si puedes, también el **chat en JSON** (mejora mucho la detección: KEKW, Pog, "clip"...).
2. Doble clic en **`iniciar.bat`** → elige el vídeo (y el chat) → **CREAR VÍDEOS**.
3. Cuando acabe, en `output/<nombre del directo>/` tendrás:

| Archivo | Qué es |
|---|---|
| `youtube_resumen.mp4` | El vídeo largo montado (teaser + título + momentos + outro) |
| `youtube_miniatura.jpg` | Miniatura 1280×720 con texto grande |
| `youtube_publicacion.txt` | 3 títulos, descripción con **capítulos**, etiquetas |
| `youtube_subtitulos.srt` | Subtítulos para subir a YouTube |
| `shorts/short_XX_*.mp4` | Los Shorts/Reels/TikToks verticales |
| `shorts/textos_para_publicar.txt` | Título y hashtags de cada Short |
| `momentos.json` | Todos los momentos encontrados (editable) |

También puedes **arrastrar el vídeo encima de `procesar.bat`** (y el chat como segundo archivo).

## 3. Cómo decide qué es un buen momento

Cada segundo del directo recibe una nota combinando:

- **Audio** – subidas fuertes respecto al ambiente (gritos, sustos, explosiones).
- **Risa** – el ritmo típico de una carcajada + "jajaja" en la transcripción.
- **Imagen** – cuánto se mueve la pantalla y los cambios de escena.
- **Palabras** – "madre mía", "qué locura", "headshot", "limpio", "bug", "nunca había visto"...
- **Chat** (si lo das) – avalanchas de mensajes y emotes (KEKW, LUL, Pog, GG, WTF...).
- **Claude** (si está activo) – lee toda la transcripción y entiende el contexto: qué es
  gracioso, qué es épico, qué es raro. Pone título y "gancho" a cada clip.

Luego clasifica cada momento en **gracioso / wow / profesional / raro / épico**, corta sin
partir frases y elige los mejores hasta llegar a la duración objetivo (por defecto 12 min,
nunca menos de 10 si el directo da para ello).

## 4. Ajustes útiles (`config.json` o el botón "Ajustes avanzados")

- `youtube.duracion_objetivo_min` – duración del vídeo de YouTube.
- `shorts.cantidad` – cuántos Shorts sacar.
- `shorts.layout` – `"blur"` (juego centrado con fondo desenfocado) o `"facecam"`
  (tu webcam arriba y el juego abajo). Para `facecam`, ajusta `shorts.facecam` con la
  posición de tu cámara en el directo, en proporciones de 0 a 1
  (ej. cámara abajo a la izquierda ocupando un 26 % de ancho: `x 0, y 0.62, w 0.26, h 0.38`).
- `pesos` – qué señales cuentan más (por ejemplo sube `risa` si quieres más momentos graciosos).
- `encoder` – `"auto"` usa la **NVENC de tu RTX 4060 Ti** (muy rápido); si falla, usa la CPU.

### ¿No te convence algún momento?
Abre `momentos.json`, borra o cambia los que quieras (tiempos en segundos, título, categoría,
`score`) y vuelve a lanzar con **"Solo montaje"** marcado: no vuelve a analizar, solo monta.

## 5. Línea de comandos

```bat
.venv\Scripts\activate
python -m src.main input\directo.mp4 --chat input\chat.json --minutos 12 --shorts 6
python -m src.main input\directo.mp4 --solo-montaje          :: tras editar momentos.json
python -m src.main input\directo.mp4 --layout facecam --sin-ia
```

## Tiempos aproximados (tu PC: Ryzen 5 + RTX 4060 Ti)

Directo de 3 h: transcripción 6–10 min (GPU), análisis 3–5 min, montaje YouTube 3–6 min,
cada Short ~20–40 s.
