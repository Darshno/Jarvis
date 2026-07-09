"""
spotify_control.py — real Spotify playback control via the official Web API.

Spotify's desktop/web player doesn't expose a local control API, so this
talks to Spotify's actual Web API instead. That means:

  1. You need a free Spotify Developer app (just a Client ID + Client
     Secret) from https://developer.spotify.com/dashboard.
  2. Add a Redirect URI to that app matching what's in Settings here
     (default: http://127.0.0.1:8888/callback).
  3. Paste the Client ID / Secret into JARVIS's Settings panel and hit
     "Connect Spotify" once — a browser window opens for you to log in
     and approve access, then JARVIS caches the token for next time.
  4. Playback control (play/pause/skip/volume) requires Spotify Premium —
     that's a Spotify restriction, not something this code can work around.
     Reading what's currently playing works on free accounts too.
  5. Spotify must be open and active on *some* device (desktop app, web
     player, or phone) for playback commands to have somewhere to go.
"""
from __future__ import annotations

from memory.config_manager import load_settings, CONFIG_DIR, ensure_config_dir

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    _SPOTIPY = True
except ImportError:
    _SPOTIPY = False

SCOPES = (
    "user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing playlist-modify-public "
    "playlist-modify-private playlist-read-private "
    "user-library-read user-read-recently-played"
)

_client = None
_client_key = None  # (client_id, client_secret, redirect_uri) the client was built with


def _cache_path() -> str:
    ensure_config_dir()
    return str(CONFIG_DIR / ".spotify_token_cache")


def _get_client():
    """Lazily builds (or rebuilds, if settings changed) the Spotipy client.
    Returns (client, error_message) — exactly one of which is truthy."""
    global _client, _client_key

    if not _SPOTIPY:
        return None, "The 'spotipy' package isn't installed. Run: pip install spotipy"

    settings = load_settings()
    client_id = (settings.get("spotify_client_id") or "").strip()
    client_secret = (settings.get("spotify_client_secret") or "").strip()
    redirect_uri = (settings.get("spotify_redirect_uri") or "http://127.0.0.1:8888/callback").strip()

    if not client_id or not client_secret:
        return None, (
            "Spotify isn't connected yet. Add your Client ID and Client "
            "Secret in Settings, then hit Connect Spotify."
        )

    key = (client_id, client_secret, redirect_uri)
    if _client is not None and _client_key == key:
        return _client, None

    try:
        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=SCOPES,
            cache_path=_cache_path(),
            open_browser=True,
        )
        client = spotipy.Spotify(auth_manager=auth_manager)
        _client, _client_key = client, key
        return client, None
    except Exception as e:
        return None, f"Spotify auth failed: {e}"


def reset_connection() -> None:
    """Forces the next call to rebuild the client (e.g. after Settings change)."""
    global _client, _client_key
    _client, _client_key = None, None


def connect() -> str:
    """Explicit connect step for a Settings-panel button — forces the OAuth
    browser flow to run right now instead of lazily on first tool use."""
    reset_connection()
    client, err = _get_client()
    if err:
        return err
    try:
        me = client.current_user()
        return f"Connected to Spotify as {me.get('display_name') or me.get('id')}."
    except Exception as e:
        return f"Connected, but couldn't verify the account: {e}"


def _active_device_id(client):
    try:
        devices = client.devices().get("devices", [])
    except Exception:
        return None
    if not devices:
        return None
    for d in devices:
        if d.get("is_active"):
            return d["id"]
    return devices[0]["id"]  # fall back to the first available device


def format_ms(ms: int) -> str:
    total_seconds = max(0, int(ms)) // 1000
    m, s = divmod(total_seconds, 60)
    return f"{m}:{s:02d}"


def get_now_playing() -> dict | None:
    """Small dict describing current playback, or None if nothing is
    playing / Spotify isn't connected. Used by both the tool and the UI's
    live progress bar."""
    client, err = _get_client()
    if err:
        return None
    try:
        pb = client.current_playback()
    except Exception:
        return None
    if not pb or not pb.get("item"):
        return None
    item = pb["item"]
    artists = ", ".join(a["name"] for a in item.get("artists", []))
    images = item.get("album", {}).get("images") or []
    return {
        "title": item.get("name", "Unknown"),
        "artist": artists or "Unknown artist",
        "progress_ms": pb.get("progress_ms") or 0,
        "duration_ms": item.get("duration_ms") or 0,
        "is_playing": bool(pb.get("is_playing")),
        "uri": item.get("uri"),
        "album_art_url": images[0].get("url") if images else None,
        "shuffle_state": bool(pb.get("shuffle_state")),
        "repeat_state": pb.get("repeat_state") or "off",  # off | track | context
    }


def play(query: str = "", uri: str = "") -> str:
    client, err = _get_client()
    if err:
        return err

    device_id = _active_device_id(client)
    if not device_id:
        return (
            "No active Spotify device found. Open Spotify (desktop, web, "
            "or mobile), make sure you're signed in, then try again."
        )

    try:
        if uri:
            client.start_playback(device_id=device_id, uris=[uri])
            return "Playing that track."

        if query:
            results = client.search(q=query, type="track", limit=1)
            items = results.get("tracks", {}).get("items", [])
            if not items:
                return f"Couldn't find a track matching '{query}'."
            track = items[0]
            client.start_playback(device_id=device_id, uris=[track["uri"]])
            artists = ", ".join(a["name"] for a in track.get("artists", []))
            return f"Playing '{track['name']}' by {artists}."

        client.start_playback(device_id=device_id)
        return "Resumed playback."
    except Exception as e:
        if "PREMIUM_REQUIRED" in str(e) or "403" in str(e):
            return "Playback control needs Spotify Premium."
        return f"Couldn't start playback: {e}"


def pause() -> str:
    client, err = _get_client()
    if err:
        return err
    try:
        client.pause_playback()
        return "Paused."
    except Exception as e:
        return f"Couldn't pause: {e}"


def resume() -> str:
    return play()


def next_track() -> str:
    client, err = _get_client()
    if err:
        return err
    try:
        client.next_track()
        return "Skipped to the next track."
    except Exception as e:
        return f"Couldn't skip track: {e}"


def previous_track() -> str:
    client, err = _get_client()
    if err:
        return err
    try:
        client.previous_track()
        return "Went back a track."
    except Exception as e:
        return f"Couldn't go back a track: {e}"


def set_volume(level) -> str:
    client, err = _get_client()
    if err:
        return err
    try:
        level = max(0, min(100, int(level)))
    except (TypeError, ValueError):
        return "Give me a volume level from 0 to 100."
    try:
        client.volume(level)
        return f"Spotify volume set to {level}%."
    except Exception as e:
        return f"Couldn't set volume: {e}"


def set_shuffle(state) -> str:
    client, err = _get_client()
    if err:
        return err
    if isinstance(state, str):
        state = state.strip().lower() not in ("off", "false", "0", "no")
    on = bool(state)
    try:
        client.shuffle(on)
        return f"Shuffle turned {'on' if on else 'off'}."
    except Exception as e:
        return f"Couldn't change shuffle: {e}"


def toggle_shuffle() -> str:
    info = get_now_playing()
    current = bool(info.get("shuffle_state")) if info else False
    return set_shuffle(not current)


def set_repeat(mode: str = "") -> str:
    client, err = _get_client()
    if err:
        return err
    mode = (mode or "").strip().lower()
    aliases = {
        "track": "track", "song": "track", "one": "track",
        "context": "context", "playlist": "context", "all": "context",
        "off": "off", "none": "off",
    }
    mode = aliases.get(mode, mode)
    if mode not in ("track", "context", "off"):
        return "Repeat mode should be one of: off, track, context (playlist/album)."
    try:
        client.repeat(mode)
        label = {"track": "Repeating this track", "context": "Repeating the playlist/album", "off": "Repeat off"}
        return f"{label[mode]}."
    except Exception as e:
        return f"Couldn't change repeat mode: {e}"


def cycle_repeat() -> str:
    """off -> context -> track -> off, used by the single widget button."""
    info = get_now_playing()
    current = (info or {}).get("repeat_state", "off")
    nxt = {"off": "context", "context": "track", "track": "off"}.get(current, "context")
    return set_repeat(nxt)


def seek(position_seconds) -> str:
    client, err = _get_client()
    if err:
        return err
    try:
        seconds = max(0, float(position_seconds))
    except (TypeError, ValueError):
        return "Give me a position in seconds to seek to."
    try:
        client.seek_track(int(seconds * 1000))
        return f"Jumped to {format_ms(int(seconds * 1000))}."
    except Exception as e:
        return f"Couldn't seek: {e}"


def list_playlists(limit: int = 15) -> str:
    client, err = _get_client()
    if err:
        return err
    try:
        results = client.current_user_playlists(limit=min(50, max(1, int(limit or 15))))
    except Exception as e:
        return f"Couldn't fetch playlists: {e}"
    items = results.get("items", [])
    if not items:
        return "You don't have any playlists."
    lines = [f"- {pl['name']} ({pl.get('tracks', {}).get('total', 0)} tracks)" for pl in items]
    return "Your playlists:\n" + "\n".join(lines)


def get_liked_songs(limit: int = 15) -> str:
    client, err = _get_client()
    if err:
        return err
    try:
        results = client.current_user_saved_tracks(limit=min(50, max(1, int(limit or 15))))
    except Exception as e:
        return f"Couldn't fetch liked songs: {e}"
    items = results.get("items", [])
    if not items:
        return "No liked songs found."
    lines = []
    for entry in items:
        track = entry.get("track") or {}
        artists = ", ".join(a["name"] for a in track.get("artists", []))
        lines.append(f"- {track.get('name', 'Unknown')} by {artists}")
    return "Your liked songs:\n" + "\n".join(lines)


def get_recently_played(limit: int = 15) -> str:
    client, err = _get_client()
    if err:
        return err
    try:
        results = client.current_user_recently_played(limit=min(50, max(1, int(limit or 15))))
    except Exception as e:
        return f"Couldn't fetch recently played tracks: {e}"
    items = results.get("items", [])
    if not items:
        return "No recent listening history found."
    seen, lines = set(), []
    for entry in items:
        track = entry.get("track") or {}
        key = track.get("id")
        if key in seen:
            continue
        seen.add(key)
        artists = ", ".join(a["name"] for a in track.get("artists", []))
        lines.append(f"- {track.get('name', 'Unknown')} by {artists}")
    return "Recently played:\n" + "\n".join(lines)


def search_tracks(query: str, limit: int = 5) -> str:
    client, err = _get_client()
    if err:
        return err
    if not query:
        return "Tell me what to search for."
    try:
        results = client.search(q=query, type="track", limit=min(10, max(1, int(limit or 5))))
    except Exception as e:
        return f"Search failed: {e}"
    items = results.get("tracks", {}).get("items", [])
    if not items:
        return f"No tracks found matching '{query}'."
    lines = []
    for t in items:
        artists = ", ".join(a["name"] for a in t.get("artists", []))
        lines.append(f"- {t['name']} by {artists}")
    return f"Top matches for '{query}':\n" + "\n".join(lines)


def get_queue() -> str:
    client, err = _get_client()
    if err:
        return err
    try:
        q = client.queue()
    except Exception as e:
        return f"Couldn't fetch the queue: {e}"
    current = q.get("currently_playing")
    upcoming = q.get("queue") or []
    lines = []
    if current:
        artists = ", ".join(a["name"] for a in current.get("artists", []))
        lines.append(f"Now playing: {current.get('name', 'Unknown')} by {artists}")
    if not upcoming:
        lines.append("Queue is empty.")
    else:
        lines.append("Up next:")
        for t in upcoming[:10]:
            artists = ", ".join(a["name"] for a in t.get("artists", []))
            lines.append(f"- {t.get('name', 'Unknown')} by {artists}")
    return "\n".join(lines)


def add_to_queue(query: str = "", uri: str = "") -> str:
    client, err = _get_client()
    if err:
        return err
    track_uri, label = uri, None
    if not track_uri and query:
        try:
            results = client.search(q=query, type="track", limit=1)
        except Exception as e:
            return f"Search failed: {e}"
        items = results.get("tracks", {}).get("items", [])
        if not items:
            return f"Couldn't find a track matching '{query}'."
        track_uri = items[0]["uri"]
        artists = ", ".join(a["name"] for a in items[0].get("artists", []))
        label = f"{items[0]['name']} by {artists}"
    if not track_uri:
        return "Tell me which song to add to the queue."
    try:
        client.add_to_queue(track_uri)
        return f"Added '{label}' to the queue." if label else "Added that track to the queue."
    except Exception as e:
        return f"Couldn't add to queue: {e}"


def now_playing_summary() -> str:
    info = get_now_playing()
    if not info:
        return "Nothing seems to be playing right now."
    state = "Playing" if info["is_playing"] else "Paused"
    elapsed = format_ms(info["progress_ms"])
    total = format_ms(info["duration_ms"])
    return f"{state}: '{info['title']}' by {info['artist']} ({elapsed}/{total})."


def _find_playlist(client, name: str):
    name_lower = name.lower().strip()
    results = client.current_user_playlists(limit=50)
    items = results.get("items", [])
    for pl in items:
        if pl["name"].lower() == name_lower:
            return pl
    for pl in items:
        if name_lower in pl["name"].lower():
            return pl
    return None


def add_to_playlist(playlist_name: str, query: str = "") -> str:
    client, err = _get_client()
    if err:
        return err

    if not playlist_name:
        return "Tell me which playlist to add it to."

    try:
        playlist = _find_playlist(client, playlist_name)
    except Exception as e:
        return f"Couldn't look up your playlists: {e}"

    if not playlist:
        return f"Couldn't find a playlist called '{playlist_name}'."

    if query:
        try:
            results = client.search(q=query, type="track", limit=1)
            items = results.get("tracks", {}).get("items", [])
        except Exception as e:
            return f"Search failed: {e}"
        if not items:
            return f"Couldn't find a track matching '{query}'."
        track_uri = items[0]["uri"]
        artists = ", ".join(a["name"] for a in items[0].get("artists", []))
        track_label = f"{items[0]['name']} by {artists}"
    else:
        now = get_now_playing()
        if not now:
            return "Nothing is currently playing to add — tell me a song name instead."
        track_uri = now["uri"]
        track_label = f"{now['title']} by {now['artist']}"

    try:
        client.playlist_add_items(playlist["id"], [track_uri])
        return f"Added '{track_label}' to '{playlist['name']}'."
    except Exception as e:
        return f"Couldn't add track to playlist: {e}"


def spotify_control(parameters: dict) -> str:
    parameters = parameters or {}
    action = (parameters.get("action") or "").strip().lower()

    if action in ("play", "resume"):
        return play(query=parameters.get("query", ""), uri=parameters.get("uri", ""))
    if action == "pause":
        return pause()
    if action in ("next", "skip"):
        return next_track()
    if action in ("previous", "back"):
        return previous_track()
    if action == "volume":
        return set_volume(parameters.get("level"))
    if action in ("now_playing", "status"):
        return now_playing_summary()
    if action == "add_to_playlist":
        return add_to_playlist(parameters.get("playlist", ""), parameters.get("query", ""))
    if action == "connect":
        return connect()
    if action == "shuffle":
        state = parameters.get("state")
        return set_shuffle(state if state is not None else True)
    if action == "repeat":
        return set_repeat(parameters.get("mode", ""))
    if action == "seek":
        return seek(parameters.get("position_seconds"))
    if action == "list_playlists":
        return list_playlists(parameters.get("limit", 15))
    if action == "liked_songs":
        return get_liked_songs(parameters.get("limit", 15))
    if action == "recently_played":
        return get_recently_played(parameters.get("limit", 15))
    if action == "search":
        return search_tracks(parameters.get("query", ""), parameters.get("limit", 5))
    if action == "queue":
        return get_queue()
    if action == "add_to_queue":
        return add_to_queue(parameters.get("query", ""), parameters.get("uri", ""))

    return (
        "Unknown Spotify action. Use one of: play, pause, resume, next, "
        "previous, volume, now_playing, add_to_playlist, connect, shuffle, "
        "repeat, seek, list_playlists, liked_songs, recently_played, "
        "search, queue, add_to_queue."
    )
