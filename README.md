# Jarvis — Local, Ollama-Powered (Mark-XLVII-style)

A personal desktop AI assistant with the same HUD look as the Mark XLVII
project, running **entirely on your computer**: no GitHub, no cloud API,
no API keys, no subscriptions. Text-only in this build (no voice).

## What it can do

| Tool | What it does |
|---|---|
| `open_app` | Launches apps, programs, and websites |
| `web_search` | DuckDuckGo search — modes: search, news, research, price, compare |
| `weather_report` | Real weather via wttr.in (no key needed) |
| `system_status` | CPU / RAM / GPU / temp / uptime snapshot |
| `file_controller` | list, create, delete, move, copy, rename, read, write, find files — sandboxed to your home folder |
| `volume_control` | Get/set/raise/lower/mute the system volume |
| `bluetooth_control` | Turn Bluetooth on/off, toggle it, or check its status |
| `spotify_control` | Play, pause, skip, seek, shuffle, repeat, queue, playlists, liked songs, recently played, search |
| `remember` | Saves facts about you across sessions |
| `clipboard_control` | Read or write the system clipboard |
| `screenshot_control` | Captures and saves a screenshot |
| `close_app` | Terminates a running application by name |
| `list_running_apps` | Lists currently running processes |
| `reminder_control` | Set or list time-based reminders |

Not included in this build (available in the original Mark XLVII but tied
to Gemini's cloud APIs or too complex to port in one pass): live voice,
webcam/screen vision, browser automation, phone dashboard, flight search,
messaging automation. Ask if you want any of these added.

## Setup

1. **Install Ollama**: https://ollama.com/download
2. **Pull a tool-calling-capable model**:
   ```bash
   ollama pull qwen2.5
   ```
   (`llama3.1`, `mistral-nemo` also work. Bigger model = better tool-calling
   reliability, but slower — pick what your machine handles well.)
3. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Run it**:
   ```bash
   python main.py --model qwen2.5
   ```
   This saves your model choice to `config/settings.json` so you don't need
   the flag next time — just `python main.py`.

## Settings tab

Click the gear icon (⚙) in the top bar to open Settings, where you can
change the Ollama server URL and model (with a "Refresh available models"
button), plus Spotify credentials and voice/TTS options. Changes apply
immediately — no restart needed.

## Volume & Bluetooth control

These work out of the box on macOS and Linux with tools that already ship
with the OS (`osascript` / `pactl`). Notes per platform:

- **Windows** — exact volume get/set uses `pycaw` + `comtypes` (already in
  `requirements.txt`, Windows-only). If those aren't installed it falls
  back to simulated media keys (up/down/mute only, via `pyautogui`).
  Bluetooth on/off uses the same system API as the Action Center toggle,
  run through PowerShell — no extra install needed.
- **macOS** — volume works immediately via `osascript`. Bluetooth needs the
  `blueutil` CLI: `brew install blueutil`.
- **Linux** — volume needs `pactl` (PulseAudio/PipeWire, usually
  preinstalled). Bluetooth needs `bluetoothctl` (BlueZ) or falls back to
  `rfkill`.

Try things like: *"turn the volume down"*, *"set volume to 40"*, *"mute
it"*, *"turn on bluetooth"*, *"is bluetooth on?"*.

## New tools

- **Clipboard** — *"copy this to my clipboard"*, *"what's on my clipboard?"*
- **Screenshots** — *"take a screenshot"* → saved to `~/Pictures/jarvis_screenshots`
- **App management** — *"close Spotify"*, *"what's running right now?"*
- **Reminders** — *"remind me to submit the report at 2026-07-10 18:00"*,
  *"what are my reminders?"* (checked every 30s, surfaced as an on-screen toast)

Additional dependencies for these: `pip install pyperclip pyautogui`.

## How it works

- `main.py` — the HUD window: animated ring, clock, scrollable log, text
  entry, now-playing widget, and settings panel.
- `engine.py` — the brain: sends your messages to Ollama with a tool
  schema, executes any tool calls the model makes, and feeds results back
  until it produces a final reply.
- `actions/` — the action modules (open_app, web_search, weather, system
  monitor, file controller, volume/bluetooth, Spotify, clipboard,
  screenshot, app control, reminders).
- `memory/` — persistent memory (`memory/long_term.json`) and local config
  (`config/settings.json` — Ollama URL/model, Spotify credentials, voice
  settings — no cloud API keys).

Everything is local files + a local model. Nothing leaves your machine
unless a tool you add later does that explicitly.

## Safety notes

- `file_controller` only operates inside your home directory — it will
  refuse paths outside that, so a hallucinated tool call can't touch
  system files.
- `open_app` / `close_app` just launch or terminate things — they don't
  grant any broader access.
- No credentials, tokens, or accounts are involved anywhere in this build
  except the optional Spotify connection, which you authorize yourself.

## Customizing

- Change the personality: edit `SYSTEM_PROMPT_BASE` in `engine.py`.
- Change default model/URL: edit `config/settings.json` or pass `--model` / `--ollama-url`.
- Add more tools: add a schema entry to `TOOL_SCHEMA` in `engine.py`, a
  matching function in `actions/`, a `_tool_*` wrapper, and register it
  in `TOOL_IMPL`.


##Authors 
- zKubenzo
- Darshno
