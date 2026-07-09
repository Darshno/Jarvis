import subprocess
import psutil

def close_app(parameters=None):
    name = (parameters or {}).get("app_name", "").strip()
    if not name:
        return "Tell me which app to close."
    closed = []
    for proc in psutil.process_iter(["name"]):
        if name.lower() in (proc.info["name"] or "").lower():
            try:
                proc.terminate()
                closed.append(proc.info["name"])
            except Exception:
                pass
    return f"Closed: {', '.join(closed)}" if closed else f"No running process matching '{name}'."

def list_running_apps(parameters=None):
    names = sorted(set(p.info["name"] for p in psutil.process_iter(["name"]) if p.info["name"]))
    return "Running: " + ", ".join(names[:30])