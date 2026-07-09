"""
weather_report.py — local-only version.
Uses wttr.in (free, no API key) to return actual text weather data,
since this build is text-only (no voice/browser-first flow needed).
"""
import urllib.parse
import urllib.request


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    city = params.get("city")

    if not city or not isinstance(city, str) or not city.strip():
        msg = "I need a city name to check the weather."
        _log(msg, player)
        return msg

    city = city.strip()
    encoded = urllib.parse.quote(city)
    url = f"https://wttr.in/{encoded}?format=%l:+%C,+%t+(feels+%f),+wind+%w,+humidity+%h"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            text = resp.read().decode("utf-8", errors="ignore").strip()
        if not text or "Unknown location" in text:
            raise ValueError("No data returned for that location.")
        msg = text
    except Exception as e:
        msg = f"Couldn't fetch weather for {city}: {e}"

    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=f"weather in {city}", response=msg)
        except Exception:
            pass

    return msg


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass
