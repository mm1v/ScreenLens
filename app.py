import ctypes
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def global_exception_handler(exc_type, exc_value, exc_traceback):
    """Logs uncaught exceptions to disk and displays Windows error dialog."""
    import traceback
    err_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    try:
        appdata_dir = Path(os.environ.get("APPDATA", ".")) / "ScreenLens"
        appdata_dir.mkdir(parents=True, exist_ok=True)
        crash_log = appdata_dir / "crash.log"
        with open(crash_log, "a", encoding="utf-8") as f:
            f.write(f"\n--- Crash at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            f.write(err_text)
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Критическая ошибка запуска ScreenLens:\n\n{err_text[:1000]}",
                "ScreenLens Error",
                0x10,  # MB_ICONERROR
            )
        except Exception:
            pass

    if hasattr(sys, "__excepthook__"):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)


sys.excepthook = global_exception_handler

from core.capture import ScreenAreaSelector
from core.clipboard import ClipboardManager
from core.hotkey import HotkeyListener
from core.models import CaptureArea
from core.service import CaptureService
from storage.db import AppSettings, DatabaseManager
from ui.floating_hud import FloatingHUD
from ui.history_window import HistoryWindow
from ui.settings_window import SettingsWindow
from ui.tray import SystemTrayApp
from utils.logger import setup_logger

MUTEX_NAME = "Local\\ScreenLens_SingleInstance_Mutex_v2"
EVENT_NAME = "Local\\ScreenLens_Trigger_Event_v2"


def play_sound(enabled: bool) -> None:
    """Plays subtle Windows notification beep if enabled."""
    if enabled and sys.platform == "win32":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass


class ScreenLensApplication:
    """Full-featured standalone desktop application with Tray, GUI, Hotkeys, and SQLite."""

    def __init__(self, h_mutex: Optional[int] = None, h_event: Optional[int] = None) -> None:
        self.logger = setup_logger("screenlens_app", level=logging.INFO)
        self.db = DatabaseManager()
        self.settings: AppSettings = self.db.get_settings()
        self.clipboard = ClipboardManager(logger=self.logger)
        self.is_running = True
        self.is_busy = False
        self._h_mutex = h_mutex
        self._h_event = h_event
        self._gui_queue: queue.Queue = queue.Queue()
        try:
            self._selector = ScreenAreaSelector(logger=self.logger)
        except Exception as exc:
            self.logger.warning("Could not pre-init ScreenAreaSelector: %s", exc)
            self._selector = None

        # Paths
        base_dir = Path(__file__).resolve().parent
        self.icon_path = base_dir / "assets" / "icon.png"

        # Initialize Tray
        self.tray = SystemTrayApp(
            icon_path=self.icon_path,
            on_capture=self.trigger_capture,
            on_show_history=self.show_history_window,
            on_show_settings=self.show_settings_window,
            on_exit=self.exit_app,
            on_toggle_ai=self.toggle_ai_mode,
            is_ai_enabled=lambda: self.settings.ai_enabled,
            logger=self.logger,
        )

        # Hotkey listener reference
        self.hotkey_listener: Optional[HotkeyListener] = None

    def toggle_ai_mode(self) -> None:
        """Toggles between AI mode (Gemini) and offline/no-AI mode."""
        self.settings.ai_enabled = not self.settings.ai_enabled
        self.db.save_settings(self.settings)
        status_text = "ВКЛЮЧЕН (Gemini)" if self.settings.ai_enabled else "ВЫКЛЮЧЕН (офлайн режим OCR)"
        self.logger.info("Режим нейросети: %s", status_text)
        self.tray.notify(
            title="ScreenLens",
            message=f"Режим нейросети {status_text}",
        )

    def start(self, auto_capture: bool = True) -> None:
        """Starts desktop application."""
        self.logger.info("Запуск ScreenLens Desktop Application...")
        self.tray.start()
        self._start_hotkey_listener()
        if self._h_event:
            self._start_ipc_listener()

        # Send welcome notification
        self.tray.notify(
            title="ScreenLens активен",
            message=f"Нажмите {self.settings.hotkey} для выделения области экрана.",
        )

        # Pre-initialize selector on the main thread so Tcl is created on thread 0
        if self._selector is None:
            try:
                self._selector = ScreenAreaSelector(logger=self.logger)
            except Exception as exc:
                self.logger.warning("Could not pre-init ScreenAreaSelector: %s", exc)

        # If user launched the app explicitly from desktop or explorer, open capture immediately!
        if auto_capture:
            self.trigger_capture()

        # Main thread GUI event loop: processes capture requests, settings, history
        try:
            while self.is_running:
                try:
                    task = self._gui_queue.get(timeout=0.1)
                    task()
                except queue.Empty:
                    pass
        except KeyboardInterrupt:
            self.exit_app()

    def _start_ipc_listener(self) -> None:
        """Background thread waiting on named event from secondary processes."""
        def ipc_worker():
            if sys.platform != "win32" or not self._h_event:
                return
            kernel32 = ctypes.windll.kernel32
            while self.is_running:
                res = kernel32.WaitForSingleObject(self._h_event, 500)
                if res == 0:  # WAIT_OBJECT_0
                    self.logger.info("Получен IPC-сигнал на захват экрана от нового запуска ScreenLens.")
                    self.trigger_capture()

        threading.Thread(target=ipc_worker, daemon=True).start()

    def _start_hotkey_listener(self) -> None:
        """Starts or restarts global hotkey listener with current settings."""
        if (
            self.hotkey_listener is not None
            and getattr(self.hotkey_listener, "_combo_str", None) == self.settings.hotkey
            and self.hotkey_listener.is_alive()
        ):
            self.logger.info("Хоткей %s уже активен, перезапуск не требуется.", self.settings.hotkey)
            return

        if self.hotkey_listener is not None:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass

        def on_press():
            self.trigger_capture()

        try:
            self.hotkey_listener = HotkeyListener(
                hotkey_combo=self.settings.hotkey,
                on_trigger=on_press,
                logger=self.logger,
            )
            hk_thread = threading.Thread(target=self.hotkey_listener.listen_loop, daemon=True)
            hk_thread.start()
            self.logger.info("Хоткей %s зарегистрирован в фоновом потоке.", self.settings.hotkey)
        except Exception as exc:
            self.logger.error("Ошибка регистрации хоткея: %s", exc)

    def trigger_capture(self) -> None:
        """Dispatches capture execution to the main thread GUI queue."""
        if not self.is_busy:
            self.logger.info("Запрос на захват экрана добавлен в очередь главного потока.")
            self._gui_queue.put(self._do_capture)
        else:
            self.logger.warning("Запрос на захват отклонен: приложение занято (is_busy=True).")

    def _do_capture(self) -> None:
        """Executes full capture workflow on the main thread."""
        if self.is_busy:
            self.logger.warning("_do_capture вызван повторно при is_busy=True, пропускаем.")
            return
        self.is_busy = True
        self.logger.info("Старт выполнения захвата экрана в главном потоке.")

        try:
            # Always reload latest settings from DB (API key, language, AI toggle)
            self.settings = self.db.get_settings()

            if self._selector is None:
                self._selector = ScreenAreaSelector(logger=self.logger)

            self._selector.select_area(
                inline_mode=True,
                settings=self.settings,
                db=self.db,
                clipboard=self.clipboard,
                sound_fn=play_sound,
            )
            self.logger.info("Захват экрана успешно завершен.")

        except Exception as exc:
            self.logger.exception("Ошибка в процессе захвата: %s", exc)
            self.tray.notify(title="Ошибка ScreenLens", message=str(exc))
        finally:
            self.is_busy = False
            self.logger.info("Состояние is_busy сброшено в False.")

    def show_settings_window(self) -> None:
        """Opens Settings GUI dispatched to the main thread."""
        self._gui_queue.put(self._run_settings_window)

    def _run_settings_window(self) -> None:
        if self.is_busy:
            return
        self.is_busy = True
        try:
            def on_saved(new_settings: AppSettings):
                self.settings = new_settings
                self._start_hotkey_listener()
                self.tray.notify(
                    title="ScreenLens",
                    message=f"Настройки обновлены. Хоткей: {self.settings.hotkey}",
                )

            master = self._selector.root if self._selector else None
            win = SettingsWindow(db=self.db, on_save_callback=on_saved)
            win.show(master=master)
        finally:
            self.is_busy = False

    def show_history_window(self) -> None:
        """Opens History GUI dispatched to the main thread."""
        self._gui_queue.put(self._run_history_window)

    def _run_history_window(self) -> None:
        if self.is_busy:
            return
        self.is_busy = True
        try:
            master = self._selector.root if self._selector else None
            win = HistoryWindow(db=self.db)
            win.show(master=master)
        finally:
            self.is_busy = False

    def exit_app(self) -> None:
        """Clean shutdown."""
        self.logger.info("Завершение работы ScreenLens...")
        self.is_running = False
        if self._selector:
            try:
                self._selector.close()
            except Exception:
                pass

        if self.hotkey_listener:
            self.hotkey_listener.stop()

        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            if self._h_event:
                try:
                    kernel32.CloseHandle(self._h_event)
                except Exception:
                    pass
            if self._h_mutex:
                try:
                    kernel32.CloseHandle(self._h_mutex)
                except Exception:
                    pass

        sys.exit(0)


def acquire_primary_instance(
    mutex_name: str = MUTEX_NAME,
    event_name: str = EVENT_NAME,
) -> tuple[Optional[int], Optional[int]]:
    """Tries to acquire primary instance mutex and creates IPC trigger event."""
    if sys.platform != "win32":
        return None, None
    try:
        kernel32 = ctypes.windll.kernel32
        h_mutex = kernel32.CreateMutexW(None, False, mutex_name)
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            if h_mutex:
                kernel32.CloseHandle(h_mutex)
            return None, None
        h_event = kernel32.CreateEventW(None, False, False, event_name)
        return h_mutex, h_event
    except Exception:
        return None, None


def signal_running_instance(event_name: str = EVENT_NAME) -> bool:
    """Signals running instance of ScreenLens to trigger capture overlay."""
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        EVENT_MODIFY_STATE = 0x0002
        h_event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, event_name)
        if h_event:
            kernel32.SetEvent(h_event)
            kernel32.CloseHandle(h_event)
            return True
    except Exception:
        pass
    return False


_APP_MUTEX = None
_APP_EVENT = None


def main() -> None:
    global _APP_MUTEX, _APP_EVENT

    flags = [arg.lower() for arg in sys.argv[1:]]
    is_silent = any(f in flags for f in ("--autostart", "--tray", "--silent", "-s"))

    h_mutex, h_event = acquire_primary_instance()
    if sys.platform == "win32" and h_mutex is None:
        # ScreenLens is already running: signal it to open capture overlay immediately!
        signal_running_instance()
        sys.exit(0)

    _APP_MUTEX = h_mutex
    _APP_EVENT = h_event

    app = ScreenLensApplication(h_mutex=h_mutex, h_event=h_event)
    app.start(auto_capture=False)


if __name__ == "__main__":
    main()
