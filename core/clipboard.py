import logging
from pathlib import Path
import sys
import time
import tkinter as tk
from typing import Any, Optional

import pyperclip



def get_clipboard_text() -> str:
    """Safely retrieves text from system clipboard."""
    try:
        return pyperclip.paste() or ""
    except Exception:
        return ""


def setup_universal_clipboard_shortcuts(root_widget: Any) -> None:
    """Binds standard clipboard shortcuts (Ctrl+V, Ctrl+C, Ctrl+A, Ctrl+X) working across RU and EN layouts."""
    def on_control_key(event: Any) -> Optional[str]:
        # event.state & 4 indicates Control key held
        if not (event.state & 4):
            return None

        code = getattr(event, "keycode", 0)
        widget = event.widget

        # 86 = 'V' key on Windows keyboard
        if code == 86:
            try:
                text = pyperclip.paste()
                if text:
                    if isinstance(widget, (tk.Entry, tk.Text)):
                        try:
                            widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
                        except Exception:
                            pass
                        widget.insert(tk.INSERT, text)
                        return "break"
            except Exception:
                pass
        # 67 = 'C' key
        elif code == 67:
            try:
                sel = ""
                if hasattr(widget, "selection_get"):
                    sel = widget.selection_get()
                if sel:
                    pyperclip.copy(sel)
                    return "break"
            except Exception:
                pass
        # 65 = 'A' key (Select All)
        elif code == 65:
            try:
                if isinstance(widget, tk.Entry):
                    widget.select_range(0, tk.END)
                    widget.icursor(tk.END)
                    return "break"
                elif isinstance(widget, tk.Text):
                    widget.tag_add(tk.SEL, "1.0", tk.END)
                    widget.mark_set(tk.INSERT, tk.END)
                    return "break"
            except Exception:
                pass
        # 88 = 'X' key (Cut)
        elif code == 88:
            try:
                if hasattr(widget, "selection_get"):
                    sel = widget.selection_get()
                    if sel:
                        pyperclip.copy(sel)
                        if hasattr(widget, "delete"):
                            widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
                        return "break"
            except Exception:
                pass
        return None

    try:
        root_widget.bind_all("<Control-KeyPress>", on_control_key, add="+")
    except Exception:
        pass


def copy_image_to_clipboard(image_or_path: Any) -> bool:
    """Copies an image (PIL Image or file path) directly to Windows clipboard as CF_DIB.

    Guarantees pure RGB format (no transparent alpha artifacts) and standard Windows DIB compatibility.
    """
    if sys.platform != "win32":
        return False

    try:
        from PIL import Image

        if isinstance(image_or_path, (str, Path)):
            p = Path(image_or_path)
            if not p.is_file():
                return False
            with Image.open(p) as img_file:
                img_file.load()
                img = img_file.convert("RGB")
        elif hasattr(image_or_path, "convert"):
            img = image_or_path.convert("RGB")
        else:
            return False

        import io
        buf = io.BytesIO()
        img.save(buf, format="BMP")
        # Strip 14-byte BITMAPFILEHEADER to get standard CF_DIB (BITMAPINFOHEADER + pixels)
        bmp_data = buf.getvalue()[14:]
        buf.close()

        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32

        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalAlloc.restype = wintypes.HGLOBAL

        k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalLock.restype = ctypes.c_void_p

        k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalUnlock.restype = wintypes.BOOL

        u32.OpenClipboard.argtypes = [wintypes.HWND]
        u32.OpenClipboard.restype = wintypes.BOOL

        u32.EmptyClipboard.argtypes = []
        u32.EmptyClipboard.restype = wintypes.BOOL

        u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u32.SetClipboardData.restype = wintypes.HANDLE

        u32.CloseClipboard.argtypes = []
        u32.CloseClipboard.restype = wintypes.BOOL

        GMEM_MOVEABLE = 0x0002
        CF_DIB = 8

        h_mem = k32.GlobalAlloc(GMEM_MOVEABLE, len(bmp_data))
        if not h_mem:
            return False

        p_mem = k32.GlobalLock(h_mem)
        if not p_mem:
            return False

        ctypes.memmove(p_mem, bmp_data, len(bmp_data))
        k32.GlobalUnlock(h_mem)

        opened = False
        for _ in range(10):
            if u32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.03)
        if not opened:
            k32.GlobalFree(h_mem)
            return False

        try:
            u32.EmptyClipboard()
            res = u32.SetClipboardData(CF_DIB, h_mem)
            return bool(res)
        finally:
            u32.CloseClipboard()
    except Exception as exc:
        logging.getLogger(__name__).warning("Error copying image to clipboard: %s", exc)
        return False


def set_clipboard_text_win32(text: str) -> bool:
    """Sets Unicode text directly to Windows clipboard with retry backoff to avoid lock contention."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32

        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalAlloc.restype = wintypes.HGLOBAL
        k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalUnlock.restype = wintypes.BOOL
        k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        k32.GlobalFree.restype = wintypes.HGLOBAL

        u32.OpenClipboard.argtypes = [wintypes.HWND]
        u32.OpenClipboard.restype = wintypes.BOOL
        u32.EmptyClipboard.argtypes = []
        u32.EmptyClipboard.restype = wintypes.BOOL
        u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u32.SetClipboardData.restype = wintypes.HANDLE
        u32.CloseClipboard.argtypes = []
        u32.CloseClipboard.restype = wintypes.BOOL

        opened = False
        for _ in range(10):
            if u32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.03)
        if not opened:
            return False

        try:
            data = (text + "\0").encode("utf-16le")
            h_mem = k32.GlobalAlloc(0x0002, len(data))
            if not h_mem:
                return False
            p_mem = k32.GlobalLock(h_mem)
            if not p_mem:
                k32.GlobalFree(h_mem)
                return False
            ctypes.memmove(p_mem, data, len(data))
            k32.GlobalUnlock(h_mem)
            u32.EmptyClipboard()
            res = u32.SetClipboardData(13, h_mem)
            return bool(res)
        finally:
            u32.CloseClipboard()
    except Exception:
        return False


class ClipboardManager:
    """Handles interaction with system clipboard."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def copy(self, text: str) -> bool:
        """Copies text to system clipboard. Returns True on success."""
        if not text:
            return False
        if sys.platform == "win32":
            if set_clipboard_text_win32(text):
                self._logger.info("Текст успешно скопирован в буфер обмена (%d симв.)", len(text))
                return True
        for attempt in range(5):
            try:
                pyperclip.copy(text)
                self._logger.info("Текст успешно скопирован в буфер обмена (%d симв.)", len(text))
                return True
            except Exception as exc:
                if attempt == 4:
                    self._logger.error("Ошибка копирования в буфер обмена: %s", exc)
                    return False
                time.sleep(0.03)
        return False

    def copy_image(self, image_or_path: Any) -> bool:
        """Copies image to system clipboard as CF_DIB."""
        ok = copy_image_to_clipboard(image_or_path)
        if ok:
            self._logger.info("Изображение успешно скопировано в буфер обмена (CF_DIB)")
        else:
            self._logger.warning("Не удалось скопировать изображение в буфер обмена")
        return ok

    def paste(self) -> str:
        """Gets current clipboard text."""
        return get_clipboard_text()


