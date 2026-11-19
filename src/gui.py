"""Ventana sencilla para usar el programa sin consola."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .utils import ROOT, load_config, set_logger, write_json

BG = "#18181b"
PANEL = "#26262c"
FG = "#efeff1"
ACCENT = "#9147ff"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Stream Highlights · Seergiolafuente")
        self.geometry("820x700")
        self.minsize(720, 600)
        self.configure(bg=BG)
        self.cfg = load_config()
        self.q: queue.Queue = queue.Queue()
        self.running = False
        self.last_out: Path | None = None
        self._style()
        self._build()
        self.after(100, self._poll)

    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL)
        st.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        st.configure("H.TLabel", font=("Segoe UI", 16, "bold"))
        st.configure("Sub.TLabel", foreground="#adadb8")
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG)])
        st.configure("TButton", background=PANEL, foreground=FG, padding=6)
        st.map("TButton", background=[("active", "#3a3a44")])
        st.configure("Go.TButton", background=ACCENT, foreground="white",
                     font=("Segoe UI", 12, "bold"), padding=10)
        st.map("Go.TButton", background=[("active", "#772ce8"), ("disabled", "#4b3a6e")])
        st.configure("TLabelframe", background=BG, foreground=FG)
        st.configure("TLabelframe.Label", background=BG, foreground="#bf94ff",
                     font=("Segoe UI", 10, "bold"))
        st.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=FG)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)],
               selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", FG)])
        st.configure("Horizontal.TProgressbar", troughcolor=PANEL, background=ACCENT)
        st.configure("TSpinbox", fieldbackground=PANEL, background=PANEL, foreground=FG)
        st.configure("TEntry", fieldbackground=PANEL, foreground=FG)

    def _build(self):
        pad = {"padx": 14, "pady": 6}
        ttk.Label(self, text="🎮 Stream Highlights", style="H.TLabel").pack(anchor="w", padx=14, pady=(14, 0))
        ttk.Label(self, text="Sube el vídeo del directo y te monto el resumen de YouTube y los Shorts.",
                  style="Sub.TLabel").pack(anchor="w", padx=14)

        f = ttk.LabelFrame(self, text="Archivos")
        f.pack(fill="x", **pad)
        self.video = tk.StringVar()
        self.chat = tk.StringVar()
        self._file_row(f, "Vídeo del directo:", self.video, self._pick_video, 0)
        self._file_row(f, "Chat (opcional):", self.chat, self._pick_chat, 1)
        f.columnconfigure(1, weight=1)

        o = ttk.LabelFrame(self, text="Opciones")
        o.pack(fill="x", **pad)
        self.minutes = tk.DoubleVar(value=self.cfg["youtube"]["duracion_objetivo_min"])
        self.nshorts = tk.IntVar(value=self.cfg["shorts"]["cantidad"])
        self.layout = tk.StringVar(value=self.cfg["shorts"]["layout"])
        self.use_ai = tk.BooleanVar(value=self.cfg["ia_claude"]["activar"])
        self.transcribe = tk.BooleanVar(value=self.cfg["transcripcion"]["activar"])
        self.do_long = tk.BooleanVar(value=True)
        self.do_shorts = tk.BooleanVar(value=True)
        self.only_render = tk.BooleanVar(value=False)

        ttk.Label(o, text="Minutos vídeo YouTube:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Spinbox(o, from_=10, to=40, increment=1, textvariable=self.minutes, width=6).grid(row=0, column=1, sticky="w")
        ttk.Label(o, text="Nº de Shorts:").grid(row=0, column=2, sticky="w", padx=8)
        ttk.Spinbox(o, from_=0, to=20, textvariable=self.nshorts, width=5).grid(row=0, column=3, sticky="w")
        ttk.Label(o, text="Diseño Shorts:").grid(row=0, column=4, sticky="w", padx=8)
        ttk.Combobox(o, values=["blur", "facecam"], textvariable=self.layout, width=9,
                     state="readonly").grid(row=0, column=5, sticky="w")
        ttk.Checkbutton(o, text="Vídeo YouTube", variable=self.do_long).grid(row=1, column=0, sticky="w", padx=8)
        ttk.Checkbutton(o, text="Shorts/Reels/TikTok", variable=self.do_shorts).grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Checkbutton(o, text="Transcribir voz (subtítulos)", variable=self.transcribe).grid(row=1, column=3, columnspan=3, sticky="w")
        ttk.Checkbutton(o, text="IA de Claude (mejor selección y títulos)", variable=self.use_ai).grid(row=2, column=0, columnspan=3, sticky="w", padx=8)
        ttk.Checkbutton(o, text="Solo montaje (usar momentos.json editado)", variable=self.only_render).grid(row=2, column=3, columnspan=3, sticky="w")

        b = tk.Frame(self, bg=BG)
        b.pack(fill="x", **pad)
        self.go = ttk.Button(b, text="▶  CREAR VÍDEOS", style="Go.TButton", command=self._start)
        self.go.pack(side="left")
        ttk.Button(b, text="📂 Abrir carpeta de resultados", command=self._open_out).pack(side="left", padx=10)
        ttk.Button(b, text="⚙ Ajustes avanzados", command=self._open_cfg).pack(side="left")
        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.pack(fill="x", padx=14)

        self.logbox = tk.Text(self, bg="#0e0e10", fg="#dedee3", insertbackground=FG,
                              font=("Consolas", 10), relief="flat", wrap="word")
        self.logbox.pack(fill="both", expand=True, padx=14, pady=(8, 14))
        self._log("Listo. Elige el vídeo del directo (puedes dejarlo en la carpeta 'input').")

    def _file_row(self, parent, label, var, cmd, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=5)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=4)
        ttk.Button(parent, text="Elegir…", command=cmd).grid(row=row, column=2, padx=8)

    def _pick_video(self):
        p = filedialog.askopenfilename(
            initialdir=ROOT / "input", title="Vídeo del directo",
            filetypes=[("Vídeo", "*.mp4 *.mkv *.mov *.flv *.ts *.webm"), ("Todos", "*.*")])
        if p:
            self.video.set(p)

    def _pick_chat(self):
        p = filedialog.askopenfilename(initialdir=ROOT / "input", title="Chat del directo",
                                       filetypes=[("Chat", "*.json *.txt *.log"), ("Todos", "*.*")])
        if p:
            self.chat.set(p)

    def _log(self, msg: str):
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "done":
                    self.running = False
                    self.go.state(["!disabled"])
                    self.bar.stop()
                    if payload:
                        self.last_out = payload
                        if messagebox.askyesno("¡Listo!", "Vídeos creados. ¿Abrir la carpeta?"):
                            self._open_out()
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _start(self):
        if self.running:
            return
        v = self.video.get().strip().strip('"')
        if not v or not Path(v).exists():
            messagebox.showwarning("Falta el vídeo", "Elige el archivo de vídeo del directo.")
            return
        cfg = load_config()
        cfg["youtube"]["duracion_objetivo_min"] = float(self.minutes.get())
        cfg["youtube"]["duracion_minima_min"] = min(cfg["youtube"]["duracion_minima_min"], float(self.minutes.get()))
        cfg["youtube"]["duracion_maxima_min"] = max(cfg["youtube"]["duracion_maxima_min"], float(self.minutes.get()) + 4)
        cfg["shorts"]["cantidad"] = int(self.nshorts.get())
        cfg["shorts"]["layout"] = self.layout.get()
        cfg["ia_claude"]["activar"] = bool(self.use_ai.get())
        cfg["transcripcion"]["activar"] = bool(self.transcribe.get())
        chat = self.chat.get().strip().strip('"') or None
        self.running = True
        self.go.state(["disabled"])
        self.bar.start(12)
        set_logger(lambda m: self.q.put(("log", m)))

        def work():
            from .main import process
            out = None
            try:
                out = process(v, chat, cfg, only_render=self.only_render.get(),
                              do_long=self.do_long.get(), do_shorts=self.do_shorts.get())
            except Exception as e:
                self.q.put(("log", f"❌ Error: {e}"))
            self.q.put(("done", out))

        threading.Thread(target=work, daemon=True).start()

    def _open_path(self, p: Path):
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])

    def _open_out(self):
        self._open_path(self.last_out or (ROOT / "output"))

    def _open_cfg(self):
        local = ROOT / "config.local.json"
        if not local.exists():
            write_json(local, {"_ayuda": "Aquí puedes sobrescribir cualquier ajuste de config.json",
                               "shorts": {"facecam": self.cfg["shorts"]["facecam"]}})
        self._open_path(local)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
