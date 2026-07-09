import pyperclip

def clipboard_control(parameters=None):
    params = parameters or {}
    action = (params.get("action") or "read").lower()
    if action == "read":
        text = pyperclip.paste()
        return f"Clipboard contains: {text[:200]}" if text else "Clipboard is empty."
    elif action == "write":
        text = params.get("text", "")
        pyperclip.copy(text)
        return f"Copied to clipboard: {text[:50]}"
    return "Unknown clipboard action. Use 'read' or 'write'."