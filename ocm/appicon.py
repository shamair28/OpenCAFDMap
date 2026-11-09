"""OpenLauncher's icon on Tk windows (title bar + Windows taskbar)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICO = os.path.join(ROOT, "assets", "openlauncher.ico")
PNG = os.path.join(ROOT, "assets", "openlauncher.png")


def set_app_id(app_id="OpenCAFDMap.OpenLauncher"):
    """Call before creating the first window: lets Windows show our icon in the taskbar instead of
    python's (and groups OpenLauncher's windows together)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:  # noqa: BLE001 - cosmetic only
            pass


def apply(win):
    """Set the icon on a Tk / Toplevel window; silently skips if the files are missing."""
    try:
        if sys.platform == "win32" and os.path.exists(ICO):
            win.iconbitmap(default=ICO)
        elif os.path.exists(PNG):
            import tkinter as tk
            win._ocm_icon = tk.PhotoImage(file=PNG)  # keep a reference
            win.iconphoto(True, win._ocm_icon)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass
