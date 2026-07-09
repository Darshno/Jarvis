"""
engine.py — the "brain" of local Jarvis.

Talks to a local Ollama server with tool calling, wired to a subset of the
Mark XLVII action modules (ported to run fully offline / API-key-free):

  - open_app        : launch apps/websites on this machine
  - web_search       : DuckDuckGo search/news/research/price/compare
  - weather_report   : real weather via wttr.in (no key needed)
  - system_status    : CPU/RAM/GPU/temp snapshot
  - file_controller  : list/create/delete/move/copy/rename/read/write/find files
                        (sandboxed to your home folder for safety)

No cloud LLM, no GitHub, no API keys. Everything runs on your machine.
"""
import json
from pathlib import Path

import requests

from actions.open_app import open_app
from actions.web_search import web_search
from actions.weather_report import weather_action
from actions.system_monitor import get_system_status
from actions.file_controller import file_controller
from actions.system_control import volume_control, bluetooth_control
from actions.spotify_control import spotify_control
from actions.clipboard_control import clipboard_control
from actions.screenshot_control import screenshot_control
from actions.app_control import close_app, list_running_apps
from actions.reminder_control import reminder_control
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)
from memory.config_manager import get_ollama_url, get_ollama_model


SYSTEM_PROMPT_BASE = (
    "You are JARVIS, a personal AI assistant running entirely on the user's "
    "own computer via a local Ollama model. There is no cloud service, no "
    "GitHub, and no API key involved — everything happens locally.\n\n"
    "You have tools to open apps, search the web, check the weather, read "
    "system stats, manage files in the user's home folder, control the "
    "system volume, turn Bluetooth on/off, and control Spotify playback "
    "directly. Always use a tool when the user's request calls for one — "
    "never claim you did something you didn't actually call a tool for.\n\n"
    "When the user asks to open, launch, or start any application, website, "
    "or program, you MUST call the open_app tool with the app_name parameter. "
    "Examples: 'open Chrome' -> open_app(app_name='Chrome'), "
    "'launch Spotify' -> open_app(app_name='Spotify'), "
    "'start notepad' -> open_app(app_name='notepad').\n\n"
    "For anything about music playback — playing a specific song, pausing, "
    "skipping, going back, checking what's playing, changing Spotify's "
    "volume, or adding a track to a playlist — use the spotify_control "
    "tool instead of open_app. Examples: 'play Blinding Lights by The "
    "Weeknd' -> spotify_control(action='play', query='Blinding Lights The "
    "Weeknd'), 'pause the music' -> spotify_control(action='pause'), "
    "'skip this song' -> spotify_control(action='next'), 'add this to my "
    "workout playlist' -> spotify_control(action='add_to_playlist', "
    "playlist='workout'), 'shuffle my music' -> spotify_control(action="
    "'shuffle', state=true), 'repeat this song' -> spotify_control(action="
    "'repeat', mode='track'), 'jump to 1:30' -> spotify_control(action="
    "'seek', position_seconds=90), 'what's in my queue' -> spotify_control("
    "action='queue'), 'queue up Blinding Lights' -> spotify_control(action="
    "'add_to_queue', query='Blinding Lights'), 'what are my playlists' -> "
    "spotify_control(action='list_playlists'), 'play something from my "
    "liked songs' -> spotify_control(action='liked_songs'), 'what have I "
    "been listening to' -> spotify_control(action='recently_played'), "
    "'search Spotify for lofi beats' -> spotify_control(action='search', "
    "query='lofi beats').\n\n"
    "You can also read/write the clipboard, take screenshots, close a "
    "running app, list running apps, and set/list reminders. Use "
    "clipboard_control, screenshot_control, close_app, list_running_apps, "
    "and reminder_control for those.\n\n"
    "Keep replies conversational and concise. Everything happens as text "
    "in this build.\n"
)


# --------------------------------------------------------------------------
# Tool schema (Ollama / OpenAI-style function calling)
# --------------------------------------------------------------------------
TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "clipboard_control",
            "description": "Reads or writes the system clipboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "read | write"},
                    "text": {"type": "string", "description": "Text to copy (write only)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot_control",
            "description": "Takes a screenshot and saves it to disk.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_app",
            "description": "Closes/terminates a running application by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Name of the app to close"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_running_apps",
            "description": "Lists currently running applications/processes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reminder_control",
            "description": "Sets or lists reminders. For 'add', when must be 'YYYY-MM-DD HH:MM'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "add | list"},
                    "text": {"type": "string", "description": "Reminder text (add only)"},
                    "when": {"type": "string", "description": "YYYY-MM-DD HH:MM (add only)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": (
                "Opens any application, program, or website on the computer. "
                "Use whenever the user asks to open, launch, or start something."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Exact name of the app (e.g. 'Chrome', 'Spotify', 'VS Code')",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Searches the web via DuckDuckGo. Use for current facts, events, prices, "
                "or anything you're not certain about. Modes: search (default), news, "
                "research (deeper dive), price, compare (side-by-side of multiple items)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query or topic"},
                    "mode": {"type": "string", "description": "search | news | research | price | compare"},
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Items to compare (compare mode only)",
                    },
                    "aspect": {"type": "string", "description": "Comparison aspect: price | specs | reviews | features"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "weather_report",
            "description": "Gets the current real weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "City name"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_status",
            "description": (
                "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
                "uptime, process count. Use when asked about computer performance."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_controller",
            "description": (
                "Manages files/folders in the user's home directory: list, create_file, "
                "create_folder, delete, move, copy, rename, read, write, find, largest, "
                "disk_usage, organize_desktop, info."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info",
                    },
                    "path": {"type": "string", "description": "Path or shortcut: desktop, downloads, documents, home"},
                    "destination": {"type": "string", "description": "Destination path for move/copy"},
                    "new_name": {"type": "string", "description": "New name for rename"},
                    "content": {"type": "string", "description": "Content for create_file/write"},
                    "name": {"type": "string", "description": "File name to act on / search for"},
                    "extension": {"type": "string", "description": "Extension filter for find (e.g. .pdf)"},
                    "count": {"type": "integer", "description": "Number of results for 'largest'"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "volume_control",
            "description": (
                "Controls the system's audio volume. Use when the user asks to change, "
                "check, raise, lower, or mute/unmute the volume."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "get | set | up | down | mute | unmute | toggle_mute",
                    },
                    "level": {
                        "type": "integer",
                        "description": "Target volume 0-100 (only used with action=set)",
                    },
                    "amount": {
                        "type": "integer",
                        "description": (
                            "How many percentage points to move for action=up/down "
                            "(e.g. 50 for 'turn it down by 50'). Defaults to 10 if omitted."
                        ),
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bluetooth_control",
            "description": (
                "Turns the computer's Bluetooth radio on or off, toggles it, or checks "
                "whether it's currently on. Use whenever the user mentions Bluetooth."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "on | off | toggle | status",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spotify_control",
            "description": (
                "Controls Spotify playback and library directly: play a specific song, pause, "
                "resume, skip forward/back, seek to a position, toggle shuffle, change repeat "
                "mode, change Spotify's volume, check what's currently playing, view or add to "
                "the queue, search tracks, list playlists, view liked songs or recently played, "
                "add a track to a playlist, or connect the Spotify account. Use this instead of "
                "open_app for anything about music playback."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "play | pause | resume | next | previous | volume | now_playing | "
                            "add_to_playlist | connect | shuffle | repeat | seek | "
                            "list_playlists | liked_songs | recently_played | search | queue | "
                            "add_to_queue"
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": "Song name (and optionally artist) to search for/play/queue/add to a playlist",
                    },
                    "uri": {"type": "string", "description": "Exact Spotify track URI, if known"},
                    "level": {"type": "integer", "description": "Volume 0-100 (action=volume only)"},
                    "playlist": {"type": "string", "description": "Playlist name (action=add_to_playlist only)"},
                    "state": {
                        "type": "boolean",
                        "description": "true/false for action=shuffle (defaults to true if omitted)",
                    },
                    "mode": {
                        "type": "string",
                        "description": "Repeat mode for action=repeat: off | track | context (playlist/album)",
                    },
                    "position_seconds": {
                        "type": "number",
                        "description": "Absolute position in seconds to jump to (action=seek only)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "How many results to return for list_playlists/liked_songs/recently_played/search",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Saves a fact about the user for future sessions (preferences, projects, "
                "people, anything worth remembering long-term)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "identity | preferences | projects | relationships | wishes | notes"},
                    "key": {"type": "string", "description": "Short label for the memory"},
                    "value": {"type": "string", "description": "The fact to remember"},
                },
                "required": ["category", "key", "value"],
            },
        },
    },
]
def _tool_clipboard(args: dict) -> str:
    return clipboard_control(parameters=args)


def _tool_screenshot(args: dict) -> str:
    return screenshot_control(parameters=args)


def _tool_close_app(args: dict) -> str:
    return close_app(parameters=args)


def _tool_list_running_apps(args: dict) -> str:
    return list_running_apps(parameters=args)


def _tool_reminder(args: dict) -> str:
    return reminder_control(parameters=args)

def _tool_open_app(args: dict) -> str:
    return open_app(parameters=args)


def _tool_web_search(args: dict) -> str:
    return web_search(parameters=args)


def _tool_weather(args: dict) -> str:
    return weather_action(parameters=args)


def _tool_system_status(args: dict) -> str:
    return json.dumps(get_system_status())


def _tool_file_controller(args: dict) -> str:
    return file_controller(parameters=args)


def _tool_volume_control(args: dict) -> str:
    return volume_control(parameters=args)


def _tool_bluetooth_control(args: dict) -> str:
    return bluetooth_control(parameters=args)


def _tool_spotify_control(args: dict) -> str:
    return spotify_control(parameters=args)


def _tool_remember(args: dict) -> str:
    category = args.get("category", "notes")
    key = args.get("key", "note")
    value = args.get("value", "")
    memory = load_memory()
    memory.setdefault(category, {})
    memory[category][key] = value
    update_memory(memory)
    return f"Remembered ({category}): {key} = {value}"


TOOL_IMPL = {
    "open_app": _tool_open_app,
    "web_search": _tool_web_search,
    "weather_report": _tool_weather,
    "system_status": _tool_system_status,
    "file_controller": _tool_file_controller,
    "volume_control": _tool_volume_control,
    "bluetooth_control": _tool_bluetooth_control,
    "spotify_control": _tool_spotify_control,
    "remember": _tool_remember,
    "clipboard_control": _tool_clipboard,
    "screenshot_control": _tool_screenshot,
    "close_app": _tool_close_app,
    "list_running_apps": _tool_list_running_apps,
    "reminder_control": _tool_reminder,
}


class JarvisEngine:
    def __init__(self, model: str | None = None, ollama_url: str | None = None):
        self.model = model or get_ollama_model()
        self.ollama_url = (ollama_url or get_ollama_url()).rstrip("/")
        self.messages = [{"role": "system", "content": self._build_system_prompt()}]

    def _build_system_prompt(self) -> str:
        memory = load_memory()
        mem_text = format_memory_for_prompt(memory)
        prompt = SYSTEM_PROMPT_BASE
        if mem_text:
            prompt += f"\nWhat you remember about the user:\n{mem_text}\n"
        return prompt

    def list_models(self) -> list:
        r = requests.get(f"{self.ollama_url}/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    def ping(self) -> bool:
        try:
            requests.get(f"{self.ollama_url}/api/tags", timeout=3)
            return True
        except Exception:
            return False

    def _chat_once(self) -> dict:
        resp = requests.post(
            f"{self.ollama_url}/api/chat",
            json={
                "model": self.model,
                "messages": self.messages,
                "tools": TOOL_SCHEMA,
                "stream": False,
            },
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()

    def ask(self, user_text: str, on_tool_call=None) -> str:
        """
        Runs one user turn to completion, including any tool round trips.
        on_tool_call(name, args, result) is called for each tool invocation,
        useful for UI logging.
        """
        self.messages.append({"role": "user", "content": user_text})

        # keep context bounded
        if len(self.messages) > 40:
            self.messages = [self.messages[0]] + self.messages[-30:]

        while True:
            try:
                result = self._chat_once()
            except requests.exceptions.ConnectionError:
                return (
                    "I can't reach Ollama right now. Make sure it's running "
                    "(`ollama serve`) and the model is pulled."
                )
            except Exception as e:
                return f"Something went wrong talking to the local model: {e}"

            msg = result.get("message", {})
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                content = msg.get("content", "")
                self.messages.append({"role": "assistant", "content": content})
                return content

            self.messages.append(msg)
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                impl = TOOL_IMPL.get(name)
                if impl is None:
                    out = f"ERROR: unknown tool {name}"
                else:
                    try:
                        out = impl(args)
                    except Exception as e:
                        out = f"ERROR running {name}: {e}"
                if on_tool_call:
                    try:
                        on_tool_call(name, args, out)
                    except Exception:
                        pass
                self.messages.append({"role": "tool", "content": str(out)})
