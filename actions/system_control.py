"""
system_control.py — volume and Bluetooth control for local Jarvis.

Cross-platform, no cloud calls, no API keys:

  volume_control(action, level, amount)
      action : get | set | up | down | mute | unmute | toggle_mute
      level  : 0-100 (only used by action=set)
      amount : how many percentage points to move for up/down (default 10)

  bluetooth_control(action)
      action: on | off | toggle | status

Platform notes:
  Windows -> volume via pycaw (exact set/get) if installed, else falls
             back to simulated media keys for up/down/mute — in that
             fallback mode `amount` is only approximate (Windows exposes
             no reliable way to read the per-press step size without
             pycaw), so pycaw+comtypes are strongly recommended.
             Bluetooth via the built-in Windows.Devices.Radios WinRT API
             (same switch as the Action Center toggle) through PowerShell,
             using a proper async/await bridge (plain .GetAwaiter() calls
             don't resolve WinRT tasks correctly from PowerShell).
  macOS   -> volume via `osascript` (built in, no install needed).
             Bluetooth via the `blueutil` CLI (brew install blueutil).
  Linux   -> volume via `pactl` (PulseAudio/PipeWire, usually preinstalled).
             Bluetooth via `bluetoothctl`, falling back to `rfkill`.
"""
import platform
import shutil
import subprocess

_SYSTEM = platform.system()


def _run(cmd, timeout=8):
    """Run a command, return (ok, stdout+stderr as str)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=isinstance(cmd, str),
        )
        out = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, out.strip()
    except Exception as e:
        return False, str(e)


# ==========================================================================
# VOLUME
# ==========================================================================

def _clamp(v):
    try:
        v = int(round(float(v)))
    except (TypeError, ValueError):
        v = 50
    return max(0, min(100, v))


# ---- Windows ---------------------------------------------------------
def _win_volume_pycaw():
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def _win_volume(action, level, amount):
    try:
        vol = _win_volume_pycaw()
    except Exception:
        return _win_volume_keys(action, level, amount)

    try:
        if action == "get":
            pct = round(vol.GetMasterVolumeLevelScalar() * 100)
            muted = bool(vol.GetMute())
            return f"Volume is {pct}%{' (muted)' if muted else ''}."
        if action == "set":
            level = _clamp(level)
            vol.SetMasterVolumeLevelScalar(level / 100, None)
            vol.SetMute(0, None)
            return f"Volume set to {level}%."
        if action in ("up", "down"):
            pct = round(vol.GetMasterVolumeLevelScalar() * 100)
            step = amount if action == "up" else -amount
            new_pct = _clamp(pct + step)
            vol.SetMasterVolumeLevelScalar(new_pct / 100, None)
            return f"Volume {'up' if action == 'up' else 'down'} to {new_pct}%."
        if action == "mute":
            vol.SetMute(1, None)
            return "Muted."
        if action == "unmute":
            vol.SetMute(0, None)
            return "Unmuted."
        if action == "toggle_mute":
            new_state = 0 if vol.GetMute() else 1
            vol.SetMute(new_state, None)
            return "Muted." if new_state else "Unmuted."
        return f"Unknown volume action: {action}"
    except Exception as e:
        return f"Volume control error: {e}"


def _win_volume_keys(action, level, amount):
    # Fallback with no extra dependencies: simulate the multimedia keys.
    # Windows doesn't expose a public way to read the exact per-press step
    # without pycaw, so this assumes the common default of ~2% per press.
    # It will be approximate — install pycaw + comtypes for exact control.
    try:
        import pyautogui
    except ImportError:
        return (
            "Volume control needs the 'pycaw' and 'comtypes' packages for "
            "exact control (pip install pycaw comtypes), or 'pyautogui' as "
            "a fallback for volume up/down/mute (pip install pyautogui)."
        )

    ASSUMED_STEP_PCT = 2  # Windows' typical default per-keypress increment

    if action == "up":
        presses = max(1, round(amount / ASSUMED_STEP_PCT))
        for _ in range(presses):
            pyautogui.press("volumeup")
        return (
            f"Volume up (~{presses * ASSUMED_STEP_PCT}%, approximate — install "
            f"'pycaw' and 'comtypes' for exact control)."
        )
    if action == "down":
        presses = max(1, round(amount / ASSUMED_STEP_PCT))
        for _ in range(presses):
            pyautogui.press("volumedown")
        return (
            f"Volume down (~{presses * ASSUMED_STEP_PCT}%, approximate — install "
            f"'pycaw' and 'comtypes' for exact control)."
        )
    if action in ("mute", "unmute", "toggle_mute"):
        pyautogui.press("volumemute")
        return "Toggled mute."
    if action == "set":
        return (
            "Can't set an exact volume level without 'pycaw' installed "
            "(pip install pycaw comtypes). I can still turn it up/down or "
            "mute it."
        )
    if action == "get":
        return "Can't read the exact volume level without 'pycaw' installed (pip install pycaw comtypes)."
    return f"Unknown volume action: {action}"


# ---- macOS -------------------------------------------------------------
def _mac_volume(action, level, amount):
    if action == "get":
        ok, out = _run(["osascript", "-e", "output volume of (get volume settings)"])
        ok2, muted = _run(["osascript", "-e", "output muted of (get volume settings)"])
        if ok:
            return f"Volume is {out.strip()}%{' (muted)' if ok2 and muted.strip() == 'true' else ''}."
        return "Could not read volume."
    if action == "set":
        level = _clamp(level)
        ok, out = _run(["osascript", "-e", f"set volume output volume {level}"])
        ok2, _ = _run(["osascript", "-e", "set volume output muted false"])
        return f"Volume set to {level}%." if ok else f"Failed to set volume: {out}"
    if action in ("up", "down"):
        ok, out = _run(["osascript", "-e", "output volume of (get volume settings)"])
        try:
            current = int(out.strip())
        except Exception:
            current = 50
        step = amount if action == "up" else -amount
        new_level = _clamp(current + step)
        ok, out = _run(["osascript", "-e", f"set volume output volume {new_level}"])
        return f"Volume {'up' if action == 'up' else 'down'} to {new_level}%." if ok else f"Failed: {out}"
    if action == "mute":
        ok, out = _run(["osascript", "-e", "set volume output muted true"])
        return "Muted." if ok else f"Failed: {out}"
    if action == "unmute":
        ok, out = _run(["osascript", "-e", "set volume output muted false"])
        return "Unmuted." if ok else f"Failed: {out}"
    if action == "toggle_mute":
        ok, muted = _run(["osascript", "-e", "output muted of (get volume settings)"])
        new_state = "false" if muted.strip() == "true" else "true"
        ok, out = _run(["osascript", "-e", f"set volume output muted {new_state}"])
        return ("Unmuted." if new_state == "false" else "Muted.") if ok else f"Failed: {out}"
    return f"Unknown volume action: {action}"


# ---- Linux ---------------------------------------------------------------
def _linux_volume(action, level, amount):
    if not shutil.which("pactl"):
        return "Volume control needs 'pactl' (PulseAudio/PipeWire utils), which isn't installed."

    if action == "get":
        ok, out = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        ok2, mute_out = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        if not ok:
            return "Could not read volume."
        pct = "?"
        for token in out.split():
            if token.endswith("%"):
                pct = token
                break
        muted = ok2 and "yes" in mute_out.lower()
        return f"Volume is {pct}{' (muted)' if muted else ''}."
    if action == "set":
        level = _clamp(level)
        ok, out = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"])
        _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
        return f"Volume set to {level}%." if ok else f"Failed to set volume: {out}"
    if action in ("up", "down"):
        step = f"{amount}%+" if action == "up" else f"{amount}%-"
        ok, out = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", step])
        return f"Volume {'up' if action == 'up' else 'down'} by {amount}%." if ok else f"Failed: {out}"
    if action == "mute":
        ok, out = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"])
        return "Muted." if ok else f"Failed: {out}"
    if action == "unmute":
        ok, out = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
        return "Unmuted." if ok else f"Failed: {out}"
    if action == "toggle_mute":
        ok, out = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        return "Toggled mute." if ok else f"Failed: {out}"
    return f"Unknown volume action: {action}"


_VOLUME_BACKENDS = {
    "Windows": _win_volume,
    "Darwin": _mac_volume,
    "Linux": _linux_volume,
}


def volume_control(parameters=None, response=None, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "get").strip().lower()
    level = params.get("level")
    try:
        amount = int(round(float(params.get("amount", 10))))
    except (TypeError, ValueError):
        amount = 10
    amount = max(1, min(100, amount))

    backend = _VOLUME_BACKENDS.get(_SYSTEM)
    if backend is None:
        return f"Volume control isn't supported on {_SYSTEM}."

    if player:
        player.write_log(f"[volume_control] {action} {level if level is not None else amount}")

    try:
        return backend(action, level, amount)
    except Exception as e:
        return f"Volume control failed: {e}"


# ==========================================================================
# BLUETOOTH
# ==========================================================================

_PS_RADIO_SNIPPET = r"""
$ErrorActionPreference = 'Stop'
try {{
    Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    }})[0]

    Function Await($WinRtTask, $ResultType) {{
        $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
        $netTask = $asTask.Invoke($null, @($WinRtTask))
        $netTask.Wait(-1) | Out-Null
        $netTask.Result
    }}

    [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
    [Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null

    $access = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
    if ($access -ne [Windows.Devices.Radios.RadioAccessStatus]::Allowed) {{
        Write-Output "ACCESS_DENIED"
        exit 0
    }}

    $radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
    $bt = $radios | Where-Object {{ $_.Kind -eq 'Bluetooth' }} | Select-Object -First 1
    if (-not $bt) {{ Write-Output "NO_RADIO"; exit 0 }}

    {action_line}

    Write-Output $bt.State
}} catch {{
    Write-Output "ERROR: $($_.Exception.Message)"
}}
"""


def _win_bluetooth(action):
    ps = shutil.which("powershell") or shutil.which("powershell.exe")
    if not ps:
        return "Bluetooth control needs PowerShell, which wasn't found."

    if action == "status":
        action_line = ""
    elif action == "on":
        action_line = (
            "Await ($bt.SetStateAsync([Windows.Devices.Radios.RadioState]::On)) "
            "([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null"
        )
    elif action == "off":
        action_line = (
            "Await ($bt.SetStateAsync([Windows.Devices.Radios.RadioState]::Off)) "
            "([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null"
        )
    elif action == "toggle":
        action_line = (
            "$target = if ($bt.State -eq [Windows.Devices.Radios.RadioState]::On) "
            "{ [Windows.Devices.Radios.RadioState]::Off } else { [Windows.Devices.Radios.RadioState]::On }; "
            "Await ($bt.SetStateAsync($target)) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null"
        )
    else:
        return f"Unknown bluetooth action: {action}"

    script = _PS_RADIO_SNIPPET.format(action_line=action_line)

    ok, out = _run(
        [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        timeout=20,
    )
    out_last = out.strip().splitlines()[-1].strip() if out.strip() else ""

    if "ACCESS_DENIED" in out:
        return (
            "Windows denied access to the Bluetooth radio. Check Settings > "
            "Privacy & security > Bluetooth devices, and make sure apps are "
            "allowed to access Bluetooth."
        )
    if "NO_RADIO" in out:
        return "No Bluetooth radio was found on this PC (it may be disabled in Device Manager, or missing a driver)."
    if out_last.startswith("ERROR:"):
        return f"Bluetooth control failed: {out_last[len('ERROR:'):].strip()}"
    if not ok:
        return f"Bluetooth control failed: {out or 'unknown error'}"
    if out_last in ("On", "Off"):
        return f"Bluetooth is now {out_last.lower()}."
    return f"Bluetooth command sent. Status: {out_last or 'unknown'}"


def _mac_bluetooth(action):
    if not shutil.which("blueutil"):
        return (
            "Bluetooth control needs the 'blueutil' CLI on macOS "
            "(install it with `brew install blueutil`)."
        )

    if action == "status":
        ok, out = _run(["blueutil", "--power"])
        if not ok:
            return f"Could not read Bluetooth status: {out}"
        return f"Bluetooth is {'on' if out.strip() == '1' else 'off'}."
    if action == "on":
        ok, out = _run(["blueutil", "--power", "1"])
        return "Bluetooth turned on." if ok else f"Failed: {out}"
    if action == "off":
        ok, out = _run(["blueutil", "--power", "0"])
        return "Bluetooth turned off." if ok else f"Failed: {out}"
    if action == "toggle":
        ok, out = _run(["blueutil", "--power"])
        new_state = "0" if ok and out.strip() == "1" else "1"
        ok, out = _run(["blueutil", "--power", new_state])
        return ("Bluetooth turned on." if new_state == "1" else "Bluetooth turned off.") if ok else f"Failed: {out}"
    return f"Unknown bluetooth action: {action}"


def _linux_bluetooth(action):
    if shutil.which("bluetoothctl"):
        if action == "status":
            ok, out = _run("bluetoothctl show | grep -i 'Powered'")
            if ok and out:
                state = "on" if "yes" in out.lower() else "off"
                return f"Bluetooth is {state}."
        elif action in ("on", "off"):
            ok, out = _run(["bluetoothctl", "power", action])
            if ok:
                return f"Bluetooth turned {action}."
        elif action == "toggle":
            ok, out = _run("bluetoothctl show | grep -i 'Powered'")
            currently_on = ok and "yes" in out.lower()
            target = "off" if currently_on else "on"
            ok, out = _run(["bluetoothctl", "power", target])
            if ok:
                return f"Bluetooth turned {target}."

    # fall back to rfkill
    if shutil.which("rfkill"):
        if action == "status":
            ok, out = _run("rfkill list bluetooth")
            if ok:
                blocked = "Soft blocked: yes" in out
                return f"Bluetooth is {'off' if blocked else 'on'}."
        elif action == "on":
            ok, out = _run(["rfkill", "unblock", "bluetooth"])
            return "Bluetooth turned on." if ok else f"Failed: {out}"
        elif action == "off":
            ok, out = _run(["rfkill", "block", "bluetooth"])
            return "Bluetooth turned off." if ok else f"Failed: {out}"
        elif action == "toggle":
            ok, out = _run("rfkill list bluetooth")
            currently_blocked = ok and "Soft blocked: yes" in out
            if currently_blocked:
                ok, out = _run(["rfkill", "unblock", "bluetooth"])
                return "Bluetooth turned on." if ok else f"Failed: {out}"
            ok, out = _run(["rfkill", "block", "bluetooth"])
            return "Bluetooth turned off." if ok else f"Failed: {out}"

    return (
        "Bluetooth control needs 'bluetoothctl' (BlueZ) or 'rfkill' installed, "
        "and neither was found."
    )


_BLUETOOTH_BACKENDS = {
    "Windows": _win_bluetooth,
    "Darwin": _mac_bluetooth,
    "Linux": _linux_bluetooth,
}


def bluetooth_control(parameters=None, response=None, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "status").strip().lower()

    backend = _BLUETOOTH_BACKENDS.get(_SYSTEM)
    if backend is None:
        return f"Bluetooth control isn't supported on {_SYSTEM}."

    if player:
        player.write_log(f"[bluetooth_control] {action}")

    try:
        return backend(action)
    except Exception as e:
        return f"Bluetooth control failed: {e}"
