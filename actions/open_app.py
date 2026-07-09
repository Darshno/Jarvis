import time
import subprocess
import platform
import shutil
import os
import json

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

# Records what each launch attempt tried and why it failed, so failures are
# actually debuggable instead of a bare "couldn't open it".
_DIAG: list[str] = []


def _diag(msg: str) -> None:
    _DIAG.append(msg)
    print(f"[open_app] {msg}")

_APP_ALIASES: dict[str, dict[str, str]] = {

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}

def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

# Some apps (Spotify chief among them) install per-user, are never on
# PATH, and don't always show up reliably via Get-StartApps depending on
# how they were installed (desktop installer vs Microsoft Store). For
# these we keep a short list of known install locations to check directly,
# plus registered URI schemes, which is how Spotify itself recommends
# launching the app externally.
_WIN_KNOWN_PATHS: dict[str, list[str]] = {
    "spotify": [
        r"%APPDATA%\Spotify\Spotify.exe",
        r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
        r"%PROGRAMFILES%\Spotify\Spotify.exe",
        r"%PROGRAMFILES(X86)%\Spotify\Spotify.exe",
    ],
}

_URI_SCHEMES: dict[str, str] = {
    "spotify": "spotify:",
}


def _win_launch_known_path(key: str) -> bool:
    for path_tpl in _WIN_KNOWN_PATHS.get(key, []):
        path = os.path.expandvars(path_tpl)
        if os.path.isfile(path):
            try:
                subprocess.Popen(
                    [path], cwd=os.path.dirname(path) or None,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                time.sleep(1.0)
                _diag(f"Known path: launched '{path}'.")
                return True
            except Exception as e:
                _diag(f"Known path: found '{path}' but launch failed — {e}")
        else:
            _diag(f"Known path: '{path}' does not exist.")
    return False


def _win_launch_uri_scheme(key: str) -> bool:
    scheme = _URI_SCHEMES.get(key)
    if not scheme:
        return False
    try:
        subprocess.Popen(f"start {scheme}", shell=True)
        time.sleep(1.0)
        _diag(f"URI scheme: launched '{scheme}'.")
        return True
    except Exception as e:
        _diag(f"URI scheme: failed — {e}")
        return False


def _win_get_start_apps():
    """Returns the same app list the Start Menu shows (name + AppID),
    including Store/UWP apps, via the Get-StartApps PowerShell cmdlet."""
    ps = shutil.which("powershell") or shutil.which("powershell.exe")
    if not ps:
        _diag("Get-StartApps: PowerShell not found on PATH.")
        return []
    try:
        result = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | ConvertTo-Json -Depth 2 -Compress"],
            capture_output=True, text=True, timeout=15,
        )
        out = (result.stdout or "").strip()
        if not out:
            err = (result.stderr or "").strip()
            _diag(f"Get-StartApps: no output (rc={result.returncode}){' — ' + err[:200] if err else ''}")
            return []
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        _diag(f"Get-StartApps: found {len(data)} entries.")
        return data
    except Exception as e:
        _diag(f"Get-StartApps: exception — {e}")
        return []


def _win_launch_via_start_apps(app_name: str) -> bool:
    """Launches an app the same way clicking its Start Menu tile would —
    no window, no typing, fully silent. Covers almost everything that
    shows up in the Start Menu, including Store apps."""
    apps = _win_get_start_apps()
    if not apps:
        return False

    name_lower = app_name.lower().strip()
    best = None

    for a in apps:
        if (a.get("Name") or "").lower() == name_lower:
            best = a
            break
    if not best:
        for a in apps:
            n = (a.get("Name") or "").lower()
            if n and (name_lower in n or n in name_lower):
                best = a
                break

    if not best or not best.get("AppID"):
        _diag(f"Start Menu list: no entry matched '{app_name}'.")
        return False

    app_id = best["AppID"]

    # Get-StartApps' AppID field means two different things depending on
    # the app type: for Store/UWP apps it's a real AppUserModelID (e.g.
    # "Package.Name_xyz!App"), and `shell:AppsFolder\<that>` launches it.
    # For ordinary desktop installs (Spotify, Discord, etc.) it's actually
    # just the full path to the app's Start Menu shortcut file — passing
    # that to shell:AppsFolder is invalid and silently does nothing
    # (explorer.exe opens with no error, so it looked like a success).
    is_aumid = "!" in app_id and not (":\\" in app_id or app_id.lower().endswith((".lnk", ".url")))

    if is_aumid:
        try:
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{app_id}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(1.0)
            _diag(f"Start Menu list: matched '{best.get('Name')}' (Store app), launched via shell:AppsFolder.")
            return True
        except Exception as e:
            _diag(f"Start Menu list: matched '{best.get('Name')}' (Store app) but launch failed — {e}")
            return False
    else:
        # AppID is a shortcut/exe path. A .lnk shortcut carries its own
        # "start in" working directory, but a bare .exe path doesn't —
        # some apps (Spotify included) can fail silently if launched with
        # the wrong working directory, so set it explicitly for .exe.
        try:
            if app_id.lower().endswith(".exe"):
                subprocess.Popen(
                    [app_id],
                    cwd=os.path.dirname(app_id) or None,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                os.startfile(app_id)
            time.sleep(1.0)
            _diag(f"Start Menu list: matched '{best.get('Name')}' (desktop app), launched '{app_id}'.")
            return True
        except Exception as e:
            _diag(f"Start Menu list: matched '{best.get('Name')}' (desktop app) but launch failed — {e}")
            return False


def _win_find_and_launch_shortcut(app_name: str) -> bool:
    """Scans the Start Menu's .lnk shortcuts and opens a matching one
    directly (os.startfile) — silent, no search UI involved."""
    name_lower = app_name.lower().replace(" ", "")
    search_dirs = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
    ]

    candidates = []
    for base in search_dirs:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for f in files:
                if not f.lower().endswith((".lnk", ".url")):
                    continue
                stem = f.rsplit(".", 1)[0].lower().replace(" ", "")
                if name_lower in stem or stem in name_lower:
                    candidates.append(os.path.join(root, f))

    if not candidates:
        _diag(f"Shortcut scan: no .lnk/.url matched '{app_name}'.")
        return False

    try:
        os.startfile(candidates[0])
        time.sleep(1.0)
        _diag(f"Shortcut scan: launched '{candidates[0]}'.")
        return True
    except Exception as e:
        _diag(f"Shortcut scan: found '{candidates[0]}' but launch failed — {e}")
        return False


def _launch_windows(app_name: str, raw_name: str = None) -> bool:
    raw_name = raw_name or app_name

    # Normalize to the alias key (e.g. "Spotify" / "spotify" -> "spotify")
    # so we can look it up in the known-paths / URI-scheme tables below.
    lookup_key = (raw_name or app_name).lower().strip()
    if lookup_key not in _APP_ALIASES:
        for alias_key in _APP_ALIASES:
            if alias_key in lookup_key or lookup_key in alias_key:
                lookup_key = alias_key
                break

    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            _diag(f"PATH lookup: launched '{app_name}' directly.")
            return True
        except Exception as e:
            _diag(f"PATH lookup: found '{app_name}' but launch failed — {e}")
    else:
        _diag(f"PATH lookup: '{app_name}' not found on PATH.")

    # Try known per-user install locations (e.g. Spotify under %APPDATA%)
    # before falling through to slower/less reliable methods.
    if _win_launch_known_path(lookup_key):
        return True

    # Try the app's registered URI scheme (Spotify supports this natively
    # and it's the officially recommended way to launch it externally).
    if _win_launch_uri_scheme(lookup_key):
        return True

    if ":" in app_name:
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            time.sleep(1.0)
            _diag(f"URI scheme: launched '{app_name}'.")
            return True
        except Exception as e:
            _diag(f"URI scheme: failed — {e}")

    # Search the same app list the Start Menu shows, and launch silently —
    # this is what handles apps not on PATH (Spotify, Discord, WhatsApp,
    # Store apps, etc.) without ever touching a search box.
    for candidate in dict.fromkeys([raw_name, app_name]):
        if _win_launch_via_start_apps(candidate):
            return True

    # Fall back to scanning Start Menu shortcuts directly.
    for candidate in dict.fromkeys([raw_name, app_name]):
        if _win_find_and_launch_shortcut(candidate):
            return True

    # Last resort only: simulate typing into the visible Start Menu search.
    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        _diag(f"Start Menu search (visible fallback): sent keystrokes for '{app_name}'.")
        return True
    except Exception as e:
        _diag(f"Start Menu search (visible fallback): failed — {e}")

    return False


def _launch_macos(app_name: str, raw_name: str = None) -> bool:

    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


def _launch_linux(app_name: str, raw_name: str = None) -> bool:

    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-")) or
        shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        subprocess.run(
            ["xdg-open", app_name],
            capture_output=True, timeout=5
        )
        return True
    except Exception:
        pass

    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "No application name provided."

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")
    _DIAG.clear()

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        if launcher(normalized, app_name):
            return f"Opened {app_name}."
        if normalized.lower() != app_name.lower():
            if launcher(app_name, app_name):
                return f"Opened {app_name}."
        detail = " / ".join(_DIAG) if _DIAG else "no diagnostic detail captured"
        return (
            f"Could not open {app_name}. Tried: {detail}"
        )
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"