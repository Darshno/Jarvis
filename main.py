"""
Jarvis — Mark XLVII-style local assistant, running fully on your machine.

No GitHub, no cloud API. Powered by Ollama + a set of local action tools
(open apps, search the web, check weather, read system stats, manage
files, control volume/Bluetooth) plus optional offline text-to-speech.

Run:
    python main.py
    python main.py --model qwen2.5
    python main.py --list-models
"""
import argparse
import math
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime

from engine import JarvisEngine
from actions.open_app import open_app
from actions import spotify_control as spotify
from actions import voice_control as voice
from memory.config_manager import save_settings, load_settings

_OPEN_COMMAND_RE = re.compile(
    r"^(?:(?:can you|could you|please)\s+)?(?:open|launch|start|run)(?:\s+up)?\s+(?:the\s+)?(?:app\s+)?(.+?)\s*$",
    re.IGNORECASE,
)


def _try_direct_open(text: str) -> str | None:
    """Handle 'open spotify' style commands without waiting on the LLM."""
    match = _OPEN_COMMAND_RE.match(text.strip())
    if not match:
        return None
    app_name = match.group(1).strip().strip("\"'")
    if not app_name:
        return "Tell me which app to open, for example: open Spotify."
    return open_app(parameters={"app_name": app_name})

# --------------------------------------------------------------------------
# Theme (kept from the original HUD widget)
# --------------------------------------------------------------------------
BG = "#0b0c10"
PANEL = "#11141a"
GRID = "#1a2230"
RING_DIM = "#0d2b45"
RING_MID = "#005b94"
RING_BRIGHT = "#00d2ff"
RING_GLOW = "#1ae8ff"
TEXT_MAIN = "#ecf8ff"
TEXT_SUB = "#4ba3c3"
TEXT_DIM = "#25536a"
ACCENT = "#00d2ff"

WIDTH, HEIGHT = 460, 600
HUD_H = 460
HEX_FONT = ("Consolas", 8)
TOAST_FONT = ("Consolas", 9)
TOAST_ICON_FONT = ("Segoe UI Emoji", 16)

WELCOME_TEXT = (
    "All systems are go. I've synced with your setup and I'm "
    "standing by for whatever you'd like to tackle first."
)

FONT_TITLE = ("Consolas", 11, "bold")
FONT_CLOCK = ("Consolas", 26, "bold")
FONT_DATE = ("Segoe UI", 9)
FONT_STATUS = ("Consolas", 9, "italic")
FONT_LOG = ("Consolas", 9)
FONT_CENTER = ("Consolas", 16, "bold")

STARTUP_LINES = [
    "Loading local model...",
    "Wiring up action tools...",
    "Systems operational. Welcome back.",
]


class JarvisUI:
    def __init__(self, engine: JarvisEngine):
        self.engine = engine
        self.root = tk.Tk()
        self.root.title("JARVIS")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)

        self.win_w, self.win_h, self.hud_h = WIDTH, HEIGHT, HUD_H
        self.base_r = 85
        self.is_fullscreen = False
        self._prev_geom = None
        self._center(self.win_w, self.win_h)

        self.start_time = time.time()
        self._drag = {"x": 0, "y": 0}
        self.center_text = "Booting..."
        self._active_toasts = []

        self._build_ui()
        self._bind_drag()
        self.root.bind("<F11>", self._toggle_fullscreen)
        self.root.bind("<Escape>", self._exit_fullscreen)
        self.showing_settings = False
        self.root.after(200, self._boot_sequence)
        self._animate()

    # ---- window chrome -----------------------------------------------
    def _center(self, w, h):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _bind_drag(self):
        def start(e):
            self._drag["x"], self._drag["y"] = e.x, e.y

        def move(e):
            x = self.root.winfo_pointerx() - self._drag["x"]
            y = self.root.winfo_pointery() - self._drag["y"]
            self.root.geometry(f"+{x}+{y}")

        self.canvas.tag_bind("dragbar", "<ButtonPress-1>", start)
        self.canvas.tag_bind("dragbar", "<B1-Motion>", move)

    def _toggle_fullscreen(self, _e=None):
        if not self.is_fullscreen:
            self._prev_geom = self.root.geometry()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.win_w, self.win_h = sw, sh
            self.hud_h = max(int(sh * 0.65), HUD_H)
            scale = min(max(sw / WIDTH, sh / HEIGHT), 1.5)
            self.base_r = int(85 * scale)
            self.root.geometry(f"{sw}x{sh}+0+0")
            self.is_fullscreen = True
        else:
            self.win_w, self.win_h, self.hud_h = WIDTH, HEIGHT, HUD_H
            self.base_r = 85
            if self._prev_geom:
                self.root.geometry(self._prev_geom)
            else:
                self._center(WIDTH, HEIGHT)
            self.is_fullscreen = False
        self._apply_size()

    def _exit_fullscreen(self, _e=None):
        if self.is_fullscreen:
            self._toggle_fullscreen()

    def _apply_size(self):
        self.canvas.config(width=self.win_w, height=self.hud_h)
        self.cx = self.win_w // 2
        self.cy = int(self.hud_h * 0.48)


    def _close(self, _e=None):
        for toast in list(self._active_toasts):
            try:
                toast.destroy()
            except Exception:
                pass
        self._active_toasts.clear()
        if self._voice_recorder is not None:
            try:
                self._voice_recorder.stop()
            except Exception:
                pass
        try:
            self.now_playing_bar.destroy()
        except Exception:
            pass
        self.append_log("JARVIS: Powering down.")
        self.root.after(400, self.root.destroy)

    def _minimize(self, _e=None):
        self.root.overrideredirect(False)
        self.root.iconify()

        def restore(ev=None):
            if self.root.state() == "normal":
                self.root.overrideredirect(True)

        self.root.bind("<Map>", restore)

    # ---- layout --------------------------------------------------------
    def _build_ui(self):
        titlebar = tk.Frame(self.root, bg=PANEL, height=28)
        titlebar.pack(fill="x", side="top")
        titlebar.pack_propagate(False)
        titlebar.tag_name = "dragbar"  # just a marker, not functional on Frame
        
        tk.Label(titlebar, text="JARVIS", bg=PANEL, fg=TEXT_SUB, font=("Consolas", 8,         "bold")).pack(side="left", padx=10)
        
        def title_btn(parent, text, cmd, hover_fg=ACCENT):
            b = tk.Label(parent, text=text, bg=PANEL, fg=TEXT_SUB, font=("Segoe UI", 10),         padx=10, pady=4, cursor="hand2")
            b.bind("<Button-1>", lambda e: cmd())
            b.bind("<Enter>", lambda e: b.config(fg=hover_fg))
            b.bind("<Leave>", lambda e: b.config(fg=TEXT_SUB))
            return b
        
        btn_row = tk.Frame(titlebar, bg=PANEL)
        btn_row.pack(side="right")
        title_btn(btn_row, "—", self._minimize).pack(side="left")
        title_btn(btn_row, "⛶", self._toggle_fullscreen).pack(side="left")
        title_btn(btn_row, "✕", self._close, hover_fg="#ff5c5c").pack(side="left")
        
        # make titlebar itself draggable
        def _tb_start(e):
            self._drag["x"], self._drag["y"] = e.x, e.y
        def _tb_move(e):
            x = self.root.winfo_pointerx() - self._drag["x"]
            y = self.root.winfo_pointery() - self._drag["y"]
            self.root.geometry(f"+{x}+{y}")
        titlebar.bind("<ButtonPress-1>", _tb_start)
        titlebar.bind("<B1-Motion>", _tb_move)
        self.canvas = tk.Canvas(
            self.root, width=self.win_w, height=self.hud_h, bg=BG, highlightthickness=0
        )
        self.canvas.pack(fill="x")
        self.cx, self.cy = self.win_w // 2, int(self.hud_h * 0.48)

        # This frame sits right below the HUD circle and holds just the
        # input bar — the scrolling command-history log is kept around
        # (append_log still writes to it) but isn't displayed, since the
        # latest reply already surfaces in the HUD circle text above.
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x")

        input_row = tk.Frame(bottom, bg=BG)
        input_row.pack(side="bottom", fill="x", padx=14, pady=(0, 14))

        self.entry = tk.Entry(
            input_row, bg=PANEL, fg=TEXT_MAIN, insertbackground=ACCENT,
            font=FONT_LOG, bd=0, highlightthickness=1,
            highlightbackground=RING_DIM, highlightcolor=ACCENT,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 6))
        self.entry.bind("<Return>", self._on_submit)
        self.entry.focus_set()

        self.mic_btn = tk.Button(
            input_row, text="\U0001F3A4", command=self._on_mic_click, bg=PANEL,
            fg=TEXT_SUB, activebackground=PANEL, activeforeground=ACCENT,
            bd=0, font=("Segoe UI", 11), width=3,
        )
        self.mic_btn.pack(side="left", padx=(0, 6))
        self._is_recording = False
        self._voice_recorder = None

        send_btn = tk.Button(
            input_row, text="\u25b6", command=self._on_submit, bg=RING_MID, fg=TEXT_MAIN,
            activebackground=ACCENT, bd=0, font=("Segoe UI", 11, "bold"), width=3,
        )
        send_btn.pack(side="left")

        log_frame = tk.Frame(bottom, bg=PANEL)
        # Intentionally not packed — history stays off-screen. Flip this to
        # log_frame.pack(side="top", fill="both", expand=True, padx=14, pady=(5, 6))
        # to bring the visible scrollback log back.

        self.log = tk.Text(
            log_frame, bg=PANEL, fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
            font=FONT_LOG, bd=0, highlightthickness=0, wrap="word",
            padx=10, pady=6, state="disabled", height=4,
        )
        self.log.tag_configure("you", foreground="#8fd6ec")
        self.log.tag_configure("jarvis", foreground=TEXT_MAIN)
        self.log.tag_configure("tool", foreground=TEXT_DIM)
        scroll = tk.Scrollbar(log_frame, command=self.log.yview, bg=PANEL)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.status_text = "Booting..."

        self.bottom = bottom
        self._build_now_playing_widget()
        self._build_settings_panel()

    # ---- now-playing widget (Spotify) — floating, draggable, rounded -----
    NP_W, NP_H, NP_R = 260, 92, 16

    @staticmethod
    def _rounded_rect_points(x1, y1, x2, y2, r):
        return [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]

    def _build_now_playing_widget(self):
        w, h, r = self.NP_W, self.NP_H, self.NP_R
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)

        # True rounded corners on Windows via a transparent color key; the
        # window itself stays rectangular but everything outside the drawn
        # rounded shape becomes see-through. Falls back gracefully elsewhere.
        transparent_key = "#010203"
        self._np_transparent = False
        try:
            win.wm_attributes("-transparentcolor", transparent_key)
            bg_key = transparent_key
            self._np_transparent = True
        except tk.TclError:
            bg_key = BG
        win.configure(bg=bg_key)

        bg_canvas = tk.Canvas(win, width=w, height=h, bg=bg_key, highlightthickness=0)
        bg_canvas.pack(fill="both", expand=True)
        bg_canvas.create_polygon(
            self._rounded_rect_points(1, 1, w - 1, h - 1, r),
            smooth=True, fill=PANEL, outline=RING_MID, width=1.3,
        )

        content = tk.Frame(win, bg=PANEL)
        content.place(x=12, y=8, width=w - 24, height=h - 16)

        top_row = tk.Frame(content, bg=PANEL)
        top_row.pack(fill="x")

        self.np_title_label = tk.Label(
            top_row, text="", bg=PANEL, fg=TEXT_MAIN,
            font=("Segoe UI", 9, "bold"), anchor="w",
        )
        self.np_title_label.pack(side="left", fill="x", expand=True)

        ctrl_row = tk.Frame(top_row, bg=PANEL)
        ctrl_row.pack(side="right")

        def ctrl_btn(text, cmd):
            b = tk.Button(
                ctrl_row, text=text, command=cmd, bg=PANEL, fg=TEXT_SUB,
                activebackground=PANEL, activeforeground=ACCENT, bd=0,
                font=("Segoe UI", 11),
            )
            b.pack(side="left", padx=2)
            return b

        self.np_shuffle_btn = ctrl_btn("\U0001F500", self._np_toggle_shuffle)
        ctrl_btn("\u23ee", self._np_previous)
        self.np_play_btn = ctrl_btn("\u23f8", self._np_toggle_play)
        ctrl_btn("\u23ed", self._np_next)
        self.np_repeat_btn = ctrl_btn("\U0001F501", self._np_cycle_repeat)

        self.np_artist_label = tk.Label(
            content, text="", bg=PANEL, fg=TEXT_SUB, font=("Segoe UI", 8), anchor="w",
        )
        self.np_artist_label.pack(fill="x", pady=(0, 6))

        prog_row = tk.Frame(content, bg=PANEL)
        prog_row.pack(fill="x")

        self.np_elapsed_label = tk.Label(
            prog_row, text="0:00", bg=PANEL, fg=TEXT_DIM, font=("Consolas", 8),
        )
        self.np_elapsed_label.pack(side="left")

        self.np_progress_canvas = tk.Canvas(
            prog_row, height=6, bg=PANEL, highlightthickness=0,
        )
        self.np_progress_canvas.pack(side="left", fill="x", expand=True, padx=6)
        self.np_progress_canvas.bind("<Configure>", lambda e: self._np_redraw_progress())
        self.np_progress_canvas.bind("<Button-1>", self._np_seek_click)

        self.np_remaining_label = tk.Label(
            prog_row, text="-0:00", bg=PANEL, fg=TEXT_DIM, font=("Consolas", 8),
        )
        self.np_remaining_label.pack(side="left")

        # Drag anywhere on the card except the transport buttons themselves.
        self._np_drag = {"x": 0, "y": 0, "win_x": 0, "win_y": 0}
        for widget in (bg_canvas, content, top_row, self.np_title_label, self.np_artist_label, prog_row):
            widget.bind("<ButtonPress-1>", self._np_drag_start)
            widget.bind("<B1-Motion>", self._np_drag_move)

        self.now_playing_bar = win
        win.withdraw()

        self._np_visible = False
        self._np_positioned = False
        self._np_fraction = 0.0
        self._np_state = None
        self._np_poll_running = False

    def _np_drag_start(self, e):
        self._np_drag["x"], self._np_drag["y"] = e.x_root, e.y_root
        self._np_drag["win_x"] = self.now_playing_bar.winfo_x()
        self._np_drag["win_y"] = self.now_playing_bar.winfo_y()

    def _np_drag_move(self, e):
        dx = e.x_root - self._np_drag["x"]
        dy = e.y_root - self._np_drag["y"]
        x = self._np_drag["win_x"] + dx
        y = self._np_drag["win_y"] + dy
        self.now_playing_bar.geometry(f"+{x}+{y}")

    def _np_show(self):
        if self._np_visible:
            return
        if not self._np_positioned:
            # First time it ever appears: place it just outside the main
            # HUD window. After that, it stays wherever the user dragged it.
            self.root.update_idletasks()
            rx, ry = self.root.winfo_x(), self.root.winfo_y()
            rw = self.root.winfo_width()
            sw = self.root.winfo_screenwidth()
            x = rx + rw + 16
            if x + self.NP_W > sw:
                x = max(0, rx - self.NP_W - 16)
            y = ry + 60
            self.now_playing_bar.geometry(f"{self.NP_W}x{self.NP_H}+{x}+{y}")
            self._np_positioned = True
        self.now_playing_bar.deiconify()
        self._np_visible = True

    def _np_hide(self):
        if self._np_visible:
            self.now_playing_bar.withdraw()
            self._np_visible = False

    def _np_redraw_progress(self):
        c = self.np_progress_canvas
        c.delete("bar")
        w = c.winfo_width() or 140
        h = 6
        c.create_rectangle(0, 0, w, h, fill=RING_DIM, outline="", tags="bar")
        filled = int(w * max(0.0, min(1.0, self._np_fraction)))
        if filled > 0:
            c.create_rectangle(0, 0, filled, h, fill=ACCENT, outline="", tags="bar")

    def _np_update(self, info):
        self._np_show()
        self._np_state = info
        self.np_title_label.config(text=info["title"])
        self.np_artist_label.config(text=info["artist"])
        remaining_ms = max(0, info["duration_ms"] - info["progress_ms"])
        self.np_elapsed_label.config(text=spotify.format_ms(info["progress_ms"]))
        self.np_remaining_label.config(text=f"-{spotify.format_ms(remaining_ms)}")
        self.np_play_btn.config(text="\u23f8" if info["is_playing"] else "\u25b6")
        self._np_fraction = (
            info["progress_ms"] / info["duration_ms"] if info["duration_ms"] else 0.0
        )
        self._np_redraw_progress()

        self.np_shuffle_btn.config(fg=ACCENT if info.get("shuffle_state") else TEXT_SUB)
        repeat_state = info.get("repeat_state", "off")
        repeat_icon = "\U0001F501\u00b9" if repeat_state == "track" else "\U0001F501"
        self.np_repeat_btn.config(
            text=repeat_icon, fg=ACCENT if repeat_state != "off" else TEXT_SUB,
        )

    def _np_start_polling(self):
        if self._np_poll_running:
            return
        self._np_poll_running = True
        threading.Thread(target=self._np_poll_loop, daemon=True).start()

    def _np_poll_loop(self):
        misses = 0
        while True:
            info = spotify.get_now_playing()
            if info:
                misses = 0
                self.root.after(0, lambda i=info: self._np_update(i))
            else:
                misses += 1
                if misses >= 4:
                    self.root.after(0, self._np_hide)
                    self._np_poll_running = False
                    return
            time.sleep(1.0)

    def _np_toggle_play(self):
        def run():
            if self._np_state and self._np_state.get("is_playing"):
                spotify.pause()
            else:
                spotify.resume()
        threading.Thread(target=run, daemon=True).start()

    def _np_next(self):
        threading.Thread(target=spotify.next_track, daemon=True).start()

    def _np_previous(self):
        threading.Thread(target=spotify.previous_track, daemon=True).start()

    def _np_toggle_shuffle(self):
        current = bool(self._np_state.get("shuffle_state")) if self._np_state else False
        threading.Thread(target=spotify.set_shuffle, args=(not current,), daemon=True).start()

    def _np_cycle_repeat(self):
        threading.Thread(target=spotify.cycle_repeat, daemon=True).start()

    def _np_seek_click(self, event):
        if not self._np_state or not self._np_state.get("duration_ms"):
            return
        w = self.np_progress_canvas.winfo_width() or 1
        fraction = max(0.0, min(1.0, event.x / w))
        target_seconds = fraction * self._np_state["duration_ms"] / 1000.0
        threading.Thread(target=spotify.seek, args=(target_seconds,), daemon=True).start()

    # ---- settings tab --------------------------------------------------
    def _build_settings_panel(self):
        settings = load_settings()

        panel = tk.Frame(self.root, bg=BG)
        self.settings_panel = panel

        scroll_canvas = tk.Canvas(panel, bg=PANEL, highlightthickness=0)
        scrollbar = tk.Scrollbar(panel, orient="vertical", command=scroll_canvas.yview)
        scroll_canvas.configure(yscrollcommand=scrollbar.set)
        scroll_canvas.pack(side="left", fill="both", expand=True, padx=(14, 0), pady=(5, 14))
        scrollbar.pack(side="right", fill="y", pady=(5, 14))

        inner = tk.Frame(scroll_canvas, bg=PANEL)
        inner_window = scroll_canvas.create_window((0, 0), window=inner, anchor="nw")

        def _sync_scrollregion(_e=None):
            scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))

        def _sync_inner_width(e):
            scroll_canvas.itemconfig(inner_window, width=e.width)

        inner.bind("<Configure>", _sync_scrollregion)
        scroll_canvas.bind("<Configure>", _sync_inner_width)

        def _on_mousewheel(e):
            if e.num == 5 or e.delta < 0:
                scroll_canvas.yview_scroll(1, "units")
            elif e.num == 4 or e.delta > 0:
                scroll_canvas.yview_scroll(-1, "units")

        def _bind_wheel(_e=None):
            scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel)
            scroll_canvas.bind_all("<Button-4>", _on_mousewheel)
            scroll_canvas.bind_all("<Button-5>", _on_mousewheel)

        def _unbind_wheel(_e=None):
            scroll_canvas.unbind_all("<MouseWheel>")
            scroll_canvas.unbind_all("<Button-4>")
            scroll_canvas.unbind_all("<Button-5>")

        scroll_canvas.bind("<Enter>", _bind_wheel)
        scroll_canvas.bind("<Leave>", _unbind_wheel)

        def label(text, row, pady=(12, 2)):
            tk.Label(
                inner, text=text, bg=PANEL, fg=TEXT_SUB, font=FONT_STATUS, anchor="w",
            ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=pady)

        def entry_row(row, initial):
            e = tk.Entry(
                inner, bg=BG, fg=TEXT_MAIN, insertbackground=ACCENT, font=FONT_LOG,
                bd=0, highlightthickness=1, highlightbackground=RING_DIM,
                highlightcolor=ACCENT,
            )
            e.insert(0, initial)
            e.grid(row=row, column=0, columnspan=2, sticky="ew", padx=12, ipady=6)
            return e

        inner.grid_columnconfigure(0, weight=1)

        row = 0
        label("OLLAMA SERVER URL", row)
        row += 1
        self.settings_url_entry = entry_row(row, settings.get("ollama_url", ""))
        row += 1

        label("OLLAMA MODEL", row)
        row += 1
        self.settings_model_entry = entry_row(row, settings.get("ollama_model", ""))
        row += 1

        refresh_btn = tk.Button(
            inner, text="Refresh available models", command=self._refresh_models,
            bg=RING_MID, fg=TEXT_MAIN, activebackground=ACCENT, bd=0,
            font=("Segoe UI", 9),
        )
        refresh_btn.grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(6, 0))
        row += 1

        label("SPOTIFY CLIENT ID", row, pady=(20, 2))
        row += 1
        self.settings_spotify_id_entry = entry_row(row, settings.get("spotify_client_id", ""))
        row += 1

        label("SPOTIFY CLIENT SECRET", row)
        row += 1
        self.settings_spotify_secret_entry = entry_row(row, settings.get("spotify_client_secret", ""))
        row += 1

        label("SPOTIFY REDIRECT URI", row)
        row += 1
        self.settings_spotify_redirect_entry = entry_row(
            row, settings.get("spotify_redirect_uri", "http://127.0.0.1:8888/callback")
        )
        row += 1

        connect_btn = tk.Button(
            inner, text="Connect Spotify", command=self._connect_spotify,
            bg=RING_MID, fg=TEXT_MAIN, activebackground=ACCENT, bd=0,
            font=("Segoe UI", 9),
        )
        connect_btn.grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(6, 0))
        row += 1

        label("VOICE INPUT MODEL (tiny / base / small)", row, pady=(20, 2))
        row += 1
        self.settings_voice_model_entry = entry_row(row, settings.get("voice_model_size", "base"))
        row += 1

        label("VOICE LANGUAGE CODE (blank = auto-detect)", row)
        row += 1
        self.settings_voice_lang_entry = entry_row(row, settings.get("voice_language", ""))
        row += 1

        self.settings_tts_var = tk.BooleanVar(value=bool(settings.get("tts_enabled", True)))
        tts_check = tk.Checkbutton(
            inner, text="Speak replies aloud", variable=self.settings_tts_var,
            bg=PANEL, fg=TEXT_MAIN, selectcolor=BG, activebackground=PANEL,
            activeforeground=TEXT_MAIN, font=FONT_STATUS, anchor="w",
        )
        tts_check.grid(row=row, column=0, columnspan=2, sticky="w", padx=10, pady=(12, 2))
        row += 1

        label("ELEVENLABS API KEY", row)
        row += 1
        self.settings_elevenlabs_key_entry = entry_row(row, settings.get("elevenlabs_api_key", ""))
        self.settings_elevenlabs_key_entry.config(show="*")
        row += 1

        label("ELEVENLABS VOICE ID", row)
        row += 1
        self.settings_elevenlabs_voice_entry = entry_row(row, settings.get("elevenlabs_voice_id", ""))
        row += 1

        label("OFFLINE TTS SPEED (fallback if no ElevenLabs key, wpm)", row)
        row += 1
        self.settings_tts_rate_entry = entry_row(row, str(settings.get("tts_rate", 175)))
        row += 1

        btn_row = tk.Frame(inner, bg=PANEL)
        btn_row.grid(row=row, column=0, columnspan=2, sticky="ew", padx=12, pady=(18, 10))

        save_btn = tk.Button(
            btn_row, text="Save", command=self._save_settings_from_panel,
            bg=RING_MID, fg=TEXT_MAIN, activebackground=ACCENT, bd=0,
            font=("Segoe UI", 10, "bold"), width=10,
        )
        save_btn.pack(side="left")

        back_btn = tk.Button(
            btn_row, text="Back to chat", command=self._toggle_settings,
            bg=PANEL, fg=TEXT_SUB, activebackground=BG, bd=0, font=("Segoe UI", 10),
        )
        back_btn.pack(side="left", padx=(10, 0))

        self.settings_status = tk.Label(
            inner, text="", bg=PANEL, fg=TEXT_SUB, font=("Segoe UI", 8, "italic"), anchor="w",
        )
        self.settings_status.grid(row=row + 1, column=0, columnspan=2, sticky="w", padx=12)

    def _refresh_models(self):
        def run():
            try:
                models = self.engine.list_models()
            except Exception as e:
                models = None
                err = str(e)
            self.root.after(0, lambda: self._show_models_result(models, None if models else err))
        threading.Thread(target=run, daemon=True).start()
        self.settings_status.config(text="Checking Ollama for installed models...")

    def _show_models_result(self, models, err):
        if models:
            self.settings_status.config(text="Available: " + ", ".join(models))
        else:
            self.settings_status.config(text=f"Could not reach Ollama: {err}")

    def _save_settings_from_panel(self):
        url = self.settings_url_entry.get().strip() or None
        model = self.settings_model_entry.get().strip() or None
        spotify_id = self.settings_spotify_id_entry.get().strip() or None
        spotify_secret = self.settings_spotify_secret_entry.get().strip() or None
        spotify_redirect = self.settings_spotify_redirect_entry.get().strip() or None
        voice_model_size = self.settings_voice_model_entry.get().strip().lower() or "base"
        voice_language = self.settings_voice_lang_entry.get().strip()
        tts_enabled = bool(self.settings_tts_var.get())
        elevenlabs_key = self.settings_elevenlabs_key_entry.get().strip()
        elevenlabs_voice = self.settings_elevenlabs_voice_entry.get().strip()
        try:
            tts_rate = int(self.settings_tts_rate_entry.get().strip() or 175)
        except ValueError:
            tts_rate = 175

        save_settings(
            ollama_url=url, ollama_model=model,
            spotify_client_id=spotify_id, spotify_client_secret=spotify_secret,
            spotify_redirect_uri=spotify_redirect,
            voice_model_size=voice_model_size, voice_language=voice_language,
            tts_enabled=tts_enabled, tts_rate=tts_rate,
            elevenlabs_api_key=elevenlabs_key, elevenlabs_voice_id=elevenlabs_voice,
        )
        spotify.reset_connection()

        # Apply immediately, no restart needed.
        if url:
            self.engine.ollama_url = url.rstrip("/")
        if model:
            self.engine.model = model

        self.settings_status.config(text="Settings saved.")

    def _connect_spotify(self):
        # Save whatever's currently in the fields first, so Connect always
        # uses what's on screen even if the user hasn't hit Save yet.
        self._save_settings_from_panel()
        self.settings_status.config(text="Opening browser to connect Spotify...")

        def run():
            result = spotify.connect()
            self.root.after(0, lambda: self.settings_status.config(text=result))
        threading.Thread(target=run, daemon=True).start()

    def _toggle_settings(self, _e=None):
        if self.showing_settings:
            self.settings_panel.pack_forget()
            self.bottom.pack(fill="x")
        else:
            self.bottom.pack_forget()
            self.settings_panel.pack(fill="both", expand=True)
        self.showing_settings = not self.showing_settings


    # ---- animation -------------------------------------------------------
    def _animate(self):
        t = time.time() - self.start_time
        self._draw_hud(t)
        self._draw_clock()
        self.root.after(25, self._animate)

    def _draw_hud(self, t):
        self.canvas.delete("hud")
        cx, cy = self.cx, self.cy
        base_r = self.base_r

        n_ticks = 60
        for i in range(n_ticks):
            angle = math.radians(i * 360 / n_ticks)
            r1, r2 = base_r + 2, base_r + 9
            x1, y1 = cx + r1 * math.cos(angle), cy + r1 * math.sin(angle)
            x2, y2 = cx + r2 * math.cos(angle), cy + r2 * math.sin(angle)
            color = RING_BRIGHT if i % 5 == 0 else RING_MID
            w = 2 if i % 5 == 0 else 1
            self.canvas.create_line(x1, y1, x2, y2, fill=color, width=w, tags="hud")
    
        # main outer ring
        self.canvas.create_oval(
            cx - base_r, cy - base_r, cx + base_r, cy + base_r,
            outline=RING_BRIGHT, width=2, tags="hud"
        )
    
        # yellow accent arc (rotating slowly)
        accent_start = (t * 20) % 360
        self.canvas.create_arc(
            cx - base_r, cy - base_r, cx + base_r, cy + base_r,
            start=accent_start, extent=25, style="arc",
            outline="#ffb300", width=3, tags="hud"
        )
    
        # rotating bright arcs (kept from before, slightly thinner)
        rot_fast = (t * 60) % 360
        rot_slow = (-t * 35) % 360
        inner_r1 = base_r - 14
        self.canvas.create_arc(
            cx - inner_r1, cy - inner_r1, cx + inner_r1, cy + inner_r1,
            start=rot_fast, extent=140, style="arc", outline=RING_BRIGHT, width=3,     tags="hud"
        )
        self.canvas.create_arc(
            cx - inner_r1, cy - inner_r1, cx + inner_r1, cy + inner_r1,
            start=rot_slow, extent=100, style="arc", outline=RING_GLOW, width=2,     tags="hud"
        )
    
        # thin dashed inner ring
        inner_r2 = base_r - 26
        self.canvas.create_oval(
            cx - inner_r2, cy - inner_r2, cx + inner_r2, cy + inner_r2,
            outline=RING_DIM, width=1, dash=(4, 4), tags="hud"
        )
    
        # center label — fixed, never shows full replies
        center_font_size = max(15, int(15 * self.base_r / 85))
        self.canvas.create_text(cx, cy, text="J.A.R.V.I.S.", fill=TEXT_MAIN,
                        font=("Consolas", center_font_size, "bold"), tags="hud")
        # outer segmented tick ring
    
        # status line below ring
        self.canvas.create_text(
            cx, cy + base_r + 55,
            text=f"// SYSTEM STATUS : {self.status_text.upper()}",
            fill=TEXT_SUB, font=FONT_STATUS, tags="hud"
        )
    
    def _draw_clock(self):
        self.canvas.delete("clock")
        now = datetime.now()
        self.canvas.create_text(self.cx, self.hud_h - 55, text=now.strftime("%H:%M:%S"), fill=TEXT_MAIN, font=FONT_CLOCK, tags="clock")
        self.canvas.create_text(self.cx, self.hud_h - 25, text=now.strftime("%A, %d %B %Y").upper(), fill=TEXT_DIM, font=FONT_DATE, tags="clock")

    # ---- boot / status ---------------------------------------------------
    def _boot_sequence(self, i=0):
        if i < len(STARTUP_LINES):
            self.set_status(STARTUP_LINES[i])
            self.root.after(450, lambda: self._boot_sequence(i + 1))
        else:
            self.set_status("Online")
            self.append_log("JARVIS: Interface operational. Ask me anything, or tell me to open an app, search something, or manage a file.")
            self._set_center_text(WELCOME_TEXT)
            self._check_ollama_async()

    def set_status(self, text):
        self.status_text = text

    def _set_center_text(self, text, max_len=170):
        text = (text or "").strip()
        if len(text) > max_len:
            text = text[:max_len - 1].rstrip() + "\u2026"
        self.center_text = text

    def _check_ollama_async(self):
        def run():
            ok = self.engine.ping()
            self.root.after(0, lambda: self._ollama_status(ok))
        threading.Thread(target=run, daemon=True).start()

    def _ollama_status(self, ok):
        if not ok:
            self.append_log(
                "JARVIS: Can't reach Ollama at the configured URL. "
                "Make sure `ollama serve` is running and try again."
            )

    # ---- chat --------------------------------------------------------
    def append_log(self, line):
        who, _, msg = line.partition(": ")
        tag = "you" if who.lower() == "you" else "jarvis"
        self.log.configure(state="normal")
        self.log.insert("end", who + ": ", tag)
        self.log.insert("end", msg + "\n\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def append_tool_log(self, name, args, result):
        preview = str(result)
        if len(preview) > 160:
            preview = preview[:160] + "..."
        self.log.configure(state="normal")
        self.log.insert("end", f"[{name}] {preview}\n", "tool")
        self.log.see("end")
        self.log.configure(state="disabled")

        self._maybe_show_action_toast(name, args or {})

    def _maybe_show_action_toast(self, name, args):
        """Pop a small transient widget for actions worth surfacing visually
        (opening an app, playing something), instead of only logging text."""
        if name == "open_app":
            app_name = args.get("app_name", "that").strip() or "that"
            low = app_name.lower()
            if any(k in low for k in ("spotify", "music", "song")):
                icon, msg = "\U0001F3B5", f"Opening {app_name}"
                self._np_start_polling()
            else:
                icon, msg = "\U0001F680", f"Opening {app_name}"
            self._show_action_toast(icon, msg)
        elif name == "spotify_control":
            action = (args.get("action") or "").lower()
            if action in ("play", "resume", "pause", "next", "skip", "previous", "back"):
                self._np_start_polling()

    def _show_action_toast(self, icon, message, duration_ms=2800):
        """A small always-on-top popup widget near the HUD, auto-dismissing."""
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg=RING_MID)

        w, h = 260, 56
        outer = tk.Frame(toast, bg=RING_MID)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        inner = tk.Frame(outer, bg=PANEL)
        inner.pack(fill="both", expand=True)

        tk.Label(
            inner, text=icon, bg=PANEL, fg=ACCENT, font=TOAST_ICON_FONT,
        ).pack(side="left", padx=(14, 10))
        tk.Label(
            inner, text=message, bg=PANEL, fg=TEXT_MAIN, font=TOAST_FONT,
            wraplength=170, justify="left", anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(0, 12))

        self.root.update_idletasks()
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        rw = self.root.winfo_width()
        x = rx + (rw - w) // 2
        y = ry + self.hud_h - h - 10
        toast.geometry(f"{w}x{h}+{x}+{y}")

        self._active_toasts.append(toast)

        def dismiss():
            if toast in self._active_toasts:
                self._active_toasts.remove(toast)
            try:
                toast.destroy()
            except Exception:
                pass

        toast.after(duration_ms, dismiss)

    def write_log(self, line):
        # compatibility shim so action modules that call player.write_log(...) work
        self.append_log(line if ": " in line else f"JARVIS: {line}")

    def _on_mic_click(self):
        if self._is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        ok, reason = voice.mic_available()
        if not ok:
            self.set_status("Voice unavailable")
            self._show_action_toast("\U0001F3A4", reason)
            return
        self._voice_recorder = voice.Recorder()
        try:
            self._voice_recorder.start()
        except Exception as e:
            self._show_action_toast("\U0001F3A4", f"Couldn't start recording: {e}")
            return
        self._is_recording = True
        self.mic_btn.config(text="\u23f9", fg="#ff5c5c")
        self.set_status("Listening")

    def _stop_recording(self):
        self._is_recording = False
        self.mic_btn.config(text="\U0001F3A4", fg=TEXT_SUB)
        recorder, self._voice_recorder = self._voice_recorder, None
        if recorder is None:
            return
        self.set_status("Transcribing")

        def run():
            wav_path = recorder.stop()
            text = voice.transcribe(wav_path) if wav_path else ""
            self.root.after(0, lambda: self._on_transcribed(text))

        threading.Thread(target=run, daemon=True).start()

    def _on_transcribed(self, text):
        if text.startswith("__ERROR__:"):
            self.set_status("Online")
            self._show_action_toast("\U0001F3A4", f"Transcription failed: {text[10:]}")
            return
        if not text:
            self.set_status("Online")
            self._show_action_toast("\U0001F3A4", "Didn't catch that — try again.")
            return
        self.submit_text(text)

    def _on_submit(self, _e=None):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.submit_text(text)

    def submit_text(self, text):
        text = text.strip()
        if not text:
            return

        if text.lower() in {"exit", "quit", "bye", "shutdown"}:
            self.append_log(f"You: {text}")
            self.append_log("JARVIS: Shutting down. See you next time.")
            self.root.after(600, self.root.destroy)
            return

        self.append_log(f"You: {text}")
        self.set_status("Thinking")

        direct = _try_direct_open(text)
        if direct is not None:
            match = _OPEN_COMMAND_RE.match(text.strip())
            app_name = match.group(1).strip().strip("\"'") if match else text
            self.root.after(0, lambda: self.append_tool_log("open_app", {"app_name": app_name}, direct))
            self.root.after(0, lambda: self._finish(direct))
            return

        threading.Thread(target=self._ask_thread, args=(text,), daemon=True).start()

    def _ask_thread(self, text):
        def on_tool_call(name, args, result):
            self.root.after(0, lambda: self.append_tool_log(name, args, result))

        reply = self.engine.ask(text, on_tool_call=on_tool_call)
        self.root.after(0, lambda: self._finish(reply))

    def _finish(self, reply):
        self.append_log(f"JARVIS: {reply}")
        self._set_center_text(reply)
        self.set_status("Online")
        if load_settings().get("tts_enabled"):
            threading.Thread(target=voice.speak, args=(reply,), daemon=True).start()

    def run(self):
        self.root.mainloop()


def main():
    # Avoid Unicode print crashes on some Windows terminals.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Local Jarvis (Ollama-powered, Mark-XLVII-style)")
    parser.add_argument("--model", default=None, help="Ollama model name (overrides config/settings.json)")
    parser.add_argument("--ollama-url", default=None, help="Ollama server URL (default http://localhost:11434)")
    parser.add_argument("--list-models", action="store_true")
    args = parser.parse_args()

    if args.model or args.ollama_url:
        save_settings(ollama_model=args.model, ollama_url=args.ollama_url)

    engine = JarvisEngine(model=args.model, ollama_url=args.ollama_url)

    if args.list_models:
        if not engine.ping():
            print(f"Could not reach Ollama at {engine.ollama_url}. Is it running? (`ollama serve`)")
            sys.exit(1)
        print("Locally available models:")
        for m in engine.list_models():
            print(f"  - {m}")
        sys.exit(0)

    if not engine.ping():
        print(f"WARNING: could not reach Ollama at {engine.ollama_url} yet.")
        print("Start it with `ollama serve` in another terminal, then retry.")

    ui = JarvisUI(engine)
    ui.run()


if __name__ == "__main__":
    main()
