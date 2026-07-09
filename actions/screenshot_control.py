import pyautogui
import os
from datetime import datetime

SAVE_DIR = os.path.expanduser("~/Pictures/jarvis_screenshots")

def screenshot_control(parameters=None):
    os.makedirs(SAVE_DIR, exist_ok=True)
    filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    path = os.path.join(SAVE_DIR, filename)
    pyautogui.screenshot().save(path)
    return f"Screenshot saved to {path}"