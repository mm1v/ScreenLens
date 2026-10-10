import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from PIL import Image
import pystray


class SystemTrayApp:
    """Windows System Tray integration using pystray."""

    def __init__(
        self,
        icon_path: Path,
        on_capture: Callable[[], None],
        on_show_history: Callable[[], None],
        on_show_settings: Callable[[], None],
        on_exit: Callable[[], None],
        on_toggle_ai: Optional[Callable[[], None]] = None,
        is_ai_enabled: Optional[Callable[[], bool]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._icon_path = icon_path
        self._on_capture = on_capture
        self._on_history = on_show_history
        self._on_settings = on_show_settings
        self._on_exit = on_exit
        self._on_toggle_ai = on_toggle_ai
        self._is_ai_enabled = is_ai_enabled
        self._logger = logger or logging.getLogger(__name__)
        self._icon: Optional[pystray.Icon] = None

    def _create_menu(self) -> pystray.Menu:
        items = [
            pystray.MenuItem("⚡ Захватить область (Ctrl+Alt+Space)", lambda: self._on_capture(), default=True),
        ]
        if self._on_toggle_ai and self._is_ai_enabled:
            items.append(
                pystray.MenuItem(
                    "🤖 Режим нейросети (Gemini ИИ)",
                    lambda: self._on_toggle_ai(),
                    checked=lambda item: self._is_ai_enabled(),
                )
            )
        items.extend([
            pystray.MenuItem("📜 История захватов...", lambda: self._on_history()),
            pystray.MenuItem("⚙️ Настройки...", lambda: self._on_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Выход", lambda: self.stop()),
        ])
        return pystray.Menu(*items)

    def start(self) -> None:
        """Launches the system tray icon in the background thread."""
        try:
            image = Image.open(self._icon_path)
        except Exception:
            # Fallback icon
            image = Image.new("RGBA", (64, 64), color=(0, 229, 255, 255))

        self._icon = pystray.Icon(
            name="ScreenLens",
            icon=image,
            title="ScreenLens — Google Lens для ПК",
            menu=self._create_menu(),
        )

        tray_thread = threading.Thread(target=self._icon.run, daemon=True)
        tray_thread.start()
        self._logger.info("Иконка в системном трее успешно запущена.")

    def notify(self, title: str, message: str) -> None:
        """Sends native Windows balloon notification."""
        if self._icon is not None:
            try:
                self._icon.notify(message=message, title=title)
            except Exception as exc:
                self._logger.warning("Не удалось отправить уведомление: %s", exc)

    def stop(self) -> None:
        """Stops and removes system tray icon and triggers exit callback."""
        if self._icon is not None:
            self._icon.stop()
            self._icon = None
        if self._on_exit:
            self._on_exit()
