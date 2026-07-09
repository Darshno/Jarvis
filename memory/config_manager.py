import json
import platform
import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "settings.json"

_OS_MAP = {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}

DEFAULTS = {
    "os_system":   _OS_MAP.get(platform.system(), "windows"),
    "ollama_url":  "http://localhost:11434",
    "ollama_model": "llama3.2",
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "spotify_redirect_uri": "http://127.0.0.1:8888/callback",
    "voice_model_size": "base",   # tiny | base | small (faster-whisper model)
    "voice_language": "",         # empty = auto-detect
    "tts_enabled": True,          # speak replies aloud
    "tts_rate": 175,              # words per minute for the offline TTS fallback
    "elevenlabs_api_key": "",
    "elevenlabs_voice_id": "wBXNqKUATyqu0RtYt25i",
}


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def config_exists() -> bool:
    return CONFIG_FILE.exists()


def load_settings() -> dict:
    if not CONFIG_FILE.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        merged = dict(DEFAULTS)
        merged.update(data)
        return merged
    except Exception as e:
        print(f"Failed to load settings.json: {e}")
        return dict(DEFAULTS)


def save_settings(**updates) -> dict:
    ensure_config_dir()
    data = load_settings()
    data.update({k: v for k, v in updates.items() if v is not None})
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def get_os() -> str:
    return load_settings().get("os_system", "windows")


def get_ollama_url() -> str:
    return load_settings().get("ollama_url", "http://localhost:11434")


def get_ollama_model() -> str:
    return load_settings().get("ollama_model", "llama3.2")


def is_configured() -> bool:
    # Local build has no API key requirement — configured as soon as the
    # settings file exists (or defaults are fine to just run with).
    return True
