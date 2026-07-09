"""
voice_control.py — offline speech-to-text (mic -> text) and optional
text-to-speech (reply -> spoken aloud), so JARVIS can be driven by voice
with no cloud API involved.

  - Recording: `sounddevice` captures raw audio from the default
    microphone into memory.
  - Transcription: `faster-whisper` runs Whisper locally on CPU. The
    model (tiny/base/small — configurable in Settings) is downloaded
    once from Hugging Face the first time it's used, then cached and
    reused fully offline after that.
  - Speaking replies: `pyttsx3` is a fully offline TTS engine (uses the
    OS's built-in voices — SAPI5 on Windows, NSSpeechSynthesizer on
    macOS, espeak on Linux). No network calls, no API key.

Both pieces are optional extras — if the packages aren't installed, the
mic button / "speak replies" toggle just explain what to install instead
of crashing the app.
"""
from __future__ import annotations

import queue
import tempfile
import threading
import wave
from pathlib import Path

from memory.config_manager import load_settings

import requests

try:
    import sounddevice as sd
    import numpy as np
    _AUDIO_IO = True
except ImportError:
    _AUDIO_IO = False

try:
    from faster_whisper import WhisperModel
    _WHISPER = True
except ImportError:
    _WHISPER = False

try:
    import pyttsx3
    _TTS = True
except ImportError:
    _TTS = False


SAMPLE_RATE = 16000  # what Whisper expects

_model = None
_model_size = None
_tts_engine = None
_tts_lock = threading.Lock()


def mic_available() -> tuple[bool, str]:
    if not _AUDIO_IO:
        return False, "Mic recording needs: pip install sounddevice numpy"
    if not _WHISPER:
        return False, "Voice transcription needs: pip install faster-whisper"
    try:
        sd.query_devices(kind="input")
    except Exception as e:
        return False, f"No microphone found: {e}"
    return True, ""


def tts_available() -> tuple[bool, str]:
    if not _TTS:
        return False, "Speaking replies needs: pip install pyttsx3"
    return True, ""


def _get_model():
    """Lazily loads (or reloads, if the settings size changed) the
    faster-whisper model. Downloads it once on first use, then it's
    cached locally by huggingface_hub and reused offline."""
    global _model, _model_size
    size = (load_settings().get("voice_model_size") or "base").strip().lower()
    if size not in ("tiny", "base", "small"):
        size = "base"
    if _model is not None and _model_size == size:
        return _model
    _model = WhisperModel(size, device="cpu", compute_type="int8")
    _model_size = size
    return _model


class Recorder:
    """Push-to-talk style recorder: call start(), speak, call stop() to
    get back the path of a temporary WAV file with what was recorded."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._stream = None
        self._frames = []

    def start(self):
        self._frames = []

        def callback(indata, _frames, _time, status):
            self._q.put(indata.copy())

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback,
        )
        self._stream.start()
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while self._stream is not None:
            try:
                chunk = self._q.get(timeout=0.2)
                self._frames.append(chunk)
            except queue.Empty:
                continue

    def stop(self) -> str | None:
        stream, self._stream = self._stream, None
        if stream is None:
            return None
        stream.stop()
        stream.close()
        # Let the drain loop flush any last chunks already queued.
        import time as _time
        _time.sleep(0.25)
        while True:
            try:
                self._frames.append(self._q.get_nowait())
            except queue.Empty:
                break

        if not self._frames:
            return None
        audio = np.concatenate(self._frames, axis=0)
        if len(audio) < SAMPLE_RATE * 0.2:  # shorter than ~0.2s: nothing useful
            return None

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        path = tmp.name
        tmp.close()
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # int16
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        return path


def transcribe(wav_path: str) -> str:
    """Runs Whisper on a recorded WAV file and returns the recognized
    text (empty string if nothing intelligible was captured)."""
    if not wav_path:
        return ""
    try:
        model = _get_model()
        settings = load_settings()
        language = (settings.get("voice_language") or "").strip() or None
        segments, _info = model.transcribe(wav_path, language=language, beam_size=1)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text
    except Exception as e:
        return f"__ERROR__:{e}"
    finally:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except Exception:
            pass


def _get_tts_engine():
    global _tts_engine
    if _tts_engine is None:
        _tts_engine = pyttsx3.init()
    settings = load_settings()
    try:
        rate = int(settings.get("tts_rate", 175))
        _tts_engine.setProperty("rate", rate)
    except (TypeError, ValueError):
        pass
    return _tts_engine


def _elevenlabs_speak(text: str, api_key: str, voice_id: str) -> bool:
    """Streams speech from the ElevenLabs API and plays it back with
    sounddevice. Returns True if it actually played audio, so the caller
    can fall back to the offline engine when it didn't (no key, no
    network, bad voice id, etc)."""
    if not _AUDIO_IO:
        return False
    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/pcm",
            },
            params={"output_format": "pcm_24000"},
            json={
                "text": text,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=30,
        )
        if resp.status_code != 200 or not resp.content:
            return False
        audio = np.frombuffer(resp.content, dtype=np.int16)
        if audio.size == 0:
            return False
        sd.play(audio, samplerate=24000)
        sd.wait()
        return True
    except Exception:
        return False


def speak(text: str) -> None:
    """Speaks text aloud. Tries the configured ElevenLabs voice first
    (needs an API key + voice id in Settings); if that isn't set up or
    fails, falls back to the fully offline pyttsx3 engine. Safe to call
    from a background thread; blocks that thread (not the caller) until
    speech finishes."""
    if not text:
        return
    settings = load_settings()
    api_key = (settings.get("elevenlabs_api_key") or "").strip()
    voice_id = (settings.get("elevenlabs_voice_id") or "").strip()
    if api_key and voice_id:
        with _tts_lock:
            if _elevenlabs_speak(text, api_key, voice_id):
                return

    if not _TTS:
        return
    with _tts_lock:
        try:
            engine = _get_tts_engine()
            engine.say(text)
            engine.runAndWait()
        except Exception:
            pass
