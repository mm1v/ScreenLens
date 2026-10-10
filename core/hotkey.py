import ctypes
import logging
import sys
import threading
import time
from typing import Callable, Optional

# Windows API constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# Windows Virtual Key Codes
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C

VK_F_KEYS = {f"f{i}": 0x70 + (i - 1) for i in range(1, 13)}
VK_SPECIAL = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "printscreen": 0x2C,
    "snapshot": 0x2C,
    "prtscn": 0x2C,
}


def _get_key_vk(key) -> Optional[int]:
    """Extracts Windows Virtual Key Code from a pynput key event."""
    vk = getattr(key, "vk", None)
    if vk is not None:
        return vk
    val = getattr(key, "value", None)
    if val is not None and hasattr(val, "vk"):
        return val.vk
    char = getattr(key, "char", None)
    if char and len(char) == 1 and char.isascii():
        return ord(char.upper())
    return None


def _is_modifier_physically_down(mod: str) -> bool:
    """Queries hardware keyboard state directly from Windows kernel via GetAsyncKeyState."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    if mod == "ctrl":
        return bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
    elif mod == "alt":
        return bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
    elif mod == "shift":
        return bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
    elif mod == "win":
        return bool((user32.GetAsyncKeyState(VK_LWIN) | user32.GetAsyncKeyState(VK_RWIN)) & 0x8000)
    return False


class HotkeyRegistrationError(Exception):
    """Raised when hotkey registration fails."""


class HotkeyListener:
    """Robust, layout-independent global hotkey listener.

    Uses low-level Windows keyboard hooks (WH_KEYBOARD_LL) via pynput combined with
    hardware GetAsyncKeyState state checks. Operates identically under Russian, English,
    or any other keyboard layout without thread message loop limitations.
    """

    def __init__(
        self,
        hotkey_combo: str = "<ctrl>+<alt>+space",
        on_trigger: Optional[Callable[[], None]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._combo_str = hotkey_combo
        self._callback = on_trigger
        self._logger = logger or logging.getLogger(__name__)
        self._is_running = False
        self._pynput_listener = None
        self._last_trigger_time = 0.0
        self._lock = threading.Lock()

        self._mods, self._target_vk = self._parse_combination(hotkey_combo)

    @staticmethod
    def _parse_combination(combo: str) -> tuple[int, int]:
        """Parses hotkey string (e.g. '<ctrl>+<alt>+s' or 'Ctrl+Alt+S') into modifiers mask and target VK."""
        cleaned = combo.replace("<", "").replace(">", "").lower()
        parts = [p.strip() for p in cleaned.split("+")]

        mods = MOD_NOREPEAT
        vk: Optional[int] = None

        for part in parts:
            if part in ("ctrl", "control"):
                mods |= MOD_CONTROL
            elif part in ("alt", "menu"):
                mods |= MOD_ALT
            elif part in ("shift",):
                mods |= MOD_SHIFT
            elif part in ("win", "windows", "super"):
                mods |= MOD_WIN
            elif part in VK_F_KEYS:
                vk = VK_F_KEYS[part]
            elif part in VK_SPECIAL:
                vk = VK_SPECIAL[part]
            elif len(part) == 1:
                # Letters and numbers map directly to their ASCII virtual key codes
                vk = ord(part.upper())
            else:
                raise ValueError(f"Неизвестная клавиша в комбинации: '{part}'")

        if vk is None:
            raise ValueError(f"Не указана основная клавиша в комбинации: '{combo}'")

        return mods, vk

    def _on_key_press(self, key) -> None:
        """Callback for low-level keyboard hook."""
        if not self._is_running:
            return

        vk = _get_key_vk(key)
        if vk == self._target_vk:
            # Check all physical modifiers against configured bitmask
            if bool(self._mods & MOD_CONTROL) != _is_modifier_physically_down("ctrl"):
                return
            if bool(self._mods & MOD_ALT) != _is_modifier_physically_down("alt"):
                return
            if bool(self._mods & MOD_SHIFT) != _is_modifier_physically_down("shift"):
                return
            if bool(self._mods & MOD_WIN) != _is_modifier_physically_down("win"):
                return

            # Debounce to prevent multiple fires when key is held down
            now = time.time()
            with self._lock:
                if now - self._last_trigger_time < 0.45:
                    return
                self._last_trigger_time = now

            self._logger.info("Глобальный хоткей %s сработал!", self._combo_str)
            if self._callback:
                try:
                    threading.Thread(target=self._callback, daemon=True).start()
                except Exception as exc:
                    self._logger.exception("Ошибка при запуске обработчика хоткея: %s", exc)

    def start(self) -> None:
        """Starts global keyboard listener."""
        if self._is_running:
            return

        self._is_running = True
        try:
            import pynput.keyboard as pk

            self._pynput_listener = pk.Listener(on_press=self._on_key_press)
            self._pynput_listener.daemon = True
            self._pynput_listener.start()
            self._logger.info("Глобальный хоткей-перехватчик %s запущен (pynput WH_KEYBOARD_LL).", self._combo_str)
        except Exception as exc:
            self._logger.warning("Не удалось запустить pynput listener (%s), используем резервный метод.", exc)
            self._start_win32_fallback()

    def _start_win32_fallback(self) -> None:
        """Fallback to Win32 RegisterHotKey if pynput is unavailable."""
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        hotkey_id = 101
        res = user32.RegisterHotKey(None, hotkey_id, self._mods, self._target_vk)
        if not res:
            self._logger.error("Win32 RegisterHotKey fallback также не удался.")
        else:
            self._logger.info("Win32 RegisterHotKey fallback успешно зарегистрирован.")

    def stop(self) -> None:
        """Stops listener and cleans up resources."""
        self._is_running = False
        if self._pynput_listener:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None

        if sys.platform == "win32":
            try:
                ctypes.windll.user32.UnregisterHotKey(None, 101)
            except Exception:
                pass
        self._logger.info("Глобальный хоткей деактивирован.")

    def is_alive(self) -> bool:
        """Returns True if listener is currently active and running."""
        if not self._is_running:
            return False
        if self._pynput_listener is not None:
            return self._pynput_listener.is_alive()
        return True

    def listen_loop(self) -> None:
        """Keeps calling thread active for background execution."""
        self.start()
        try:
            while self._is_running:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
