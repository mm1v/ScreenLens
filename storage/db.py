import os
import sqlite3
import sys
import base64
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def protect_secret(secret: str) -> str:
    """Encrypts sensitive string using Windows DPAPI (tied to current Windows user)."""
    if not secret or sys.platform != "win32":
        return secret
    try:
        data = secret.encode("utf-8")
        blob_in = _DATA_BLOB(
            len(data),
            ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)),
        )
        blob_out = _DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), "ScreenLens Secret", None, None, None, 0, ctypes.byref(blob_out)
        ):
            encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            return "DPAPI:" + base64.b64encode(encrypted).decode("ascii")
    except Exception:
        pass
    return secret


def unprotect_secret(cipher_text: str) -> str:
    """Decrypts DPAPI-encrypted string for current Windows user."""
    if not cipher_text or not cipher_text.startswith("DPAPI:") or sys.platform != "win32":
        return cipher_text
    try:
        raw = base64.b64decode(cipher_text[6:])
        blob_in = _DATA_BLOB(
            len(raw),
            ctypes.cast(ctypes.create_string_buffer(raw, len(raw)), ctypes.POINTER(ctypes.c_char)),
        )
        blob_out = _DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            return decrypted.decode("utf-8")
    except Exception:
        pass
    return cipher_text


def get_app_data_dir() -> Path:
    """Returns application data directory: %APPDATA%/ScreenLens or ~/.screenlens."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"

    app_dir = base / "ScreenLens"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


@dataclass
class AppSettings:
    """Application user configuration dataclass."""

    hotkey: str = "<ctrl>+<alt>+space"
    ocr_lang: str = "rus+eng"
    ocr_engine: str = "auto"
    translate_lang: str = "ru"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_proxy: str = ""
    ai_enabled: bool = True
    sound_enabled: bool = True
    autostart: bool = False
    show_hud: bool = True


@dataclass
class HistoryItem:
    """Represents a single capture entry in history."""

    id: Optional[int]
    timestamp: str
    image_path: str
    ocr_text: str
    translated_text: str = ""
    gemini_result: str = ""
    qr_data: str = ""


class DatabaseManager:
    """Manages SQLite storage for settings and history."""

    DEFAULT_SETTINGS = {
        "hotkey": "<ctrl>+<alt>+space",
        "ocr_lang": "rus+eng",
        "ocr_engine": "auto",
        "translate_lang": "ru",
        "gemini_api_key": "",
        "gemini_model": "gemini-3.5-flash-lite",
        "gemini_proxy": "",
        "ai_enabled": "1",
        "sound_enabled": "1",
        "autostart": "0",
        "show_hud": "1",
    }

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or (get_app_data_dir() / "screenlens.db")
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initializes SQLite schema if not exists."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    ocr_text TEXT,
                    translated_text TEXT,
                    gemini_result TEXT,
                    qr_data TEXT
                )
                """
            )
            # Insert default settings if missing
            for key, val in self.DEFAULT_SETTINGS.items():
                cursor.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, val),
                )
            # Migrate legacy default hotkey if present
            cursor.execute(
                "UPDATE settings SET value = '<ctrl>+<alt>+space' WHERE key = 'hotkey' AND value = '<ctrl>+<alt>+s'"
            )
            conn.commit()

    def get_settings(self) -> AppSettings:
        """Loads and returns current AppSettings."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            rows = dict(cursor.fetchall())

        return AppSettings(
            hotkey=rows.get("hotkey", "<ctrl>+<alt>+space"),
            ocr_lang=rows.get("ocr_lang", "rus+eng"),
            ocr_engine=rows.get("ocr_engine", "auto"),
            translate_lang=rows.get("translate_lang", "ru"),
            gemini_api_key=unprotect_secret(rows.get("gemini_api_key", "")),
            gemini_model=rows.get("gemini_model", "gemini-3.5-flash-lite"),
            gemini_proxy=rows.get("gemini_proxy", ""),
            ai_enabled=rows.get("ai_enabled", "1") == "1",
            sound_enabled=rows.get("sound_enabled", "1") == "1",
            autostart=rows.get("autostart", "0") == "1",
            show_hud=rows.get("show_hud", "1") == "1",
        )

    def save_settings(self, settings: AppSettings) -> None:
        """Persists AppSettings to database."""
        data = {
            "hotkey": settings.hotkey,
            "ocr_lang": settings.ocr_lang,
            "ocr_engine": settings.ocr_engine,
            "translate_lang": settings.translate_lang,
            "gemini_api_key": protect_secret(settings.gemini_api_key),
            "gemini_model": settings.gemini_model,
            "gemini_proxy": settings.gemini_proxy,
            "ai_enabled": "1" if settings.ai_enabled else "0",
            "sound_enabled": "1" if settings.sound_enabled else "0",
            "autostart": "1" if settings.autostart else "0",
            "show_hud": "1" if settings.show_hud else "0",
        }
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for key, val in data.items():
                cursor.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, val),
                )
            conn.commit()

    def add_history(
        self,
        image_path: str,
        ocr_text: str,
        translated_text: str = "",
        gemini_result: str = "",
        qr_data: str = "",
    ) -> int:
        """Inserts a new capture entry into history table."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO history (timestamp, image_path, ocr_text, translated_text, gemini_result, qr_data)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, image_path, ocr_text, translated_text, gemini_result, qr_data),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_history(self, limit: int = 50) -> list[HistoryItem]:
        """Retrieves history entries ordered by newest first."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, timestamp, image_path, ocr_text, translated_text, gemini_result, qr_data
                FROM history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()

        return [
            HistoryItem(
                id=r[0],
                timestamp=r[1],
                image_path=r[2],
                ocr_text=r[3] or "",
                translated_text=r[4] or "",
                gemini_result=r[5] or "",
                qr_data=r[6] or "",
            )
            for r in rows
        ]

    def clear_history(self) -> None:
        """Deletes all history records."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM history")
            conn.commit()


def set_windows_autostart(enabled: bool, app_path: Optional[str] = None) -> bool:
    """Configures Windows Run registry key for autostart."""
    if sys.platform != "win32":
        return False

    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "ScreenLens"

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        ) as key:
            if enabled:
                executable = app_path or sys.executable
                if not executable.endswith(".exe"):
                    executable = f'"{sys.executable}" "{Path(__file__).resolve().parent.parent / "app.py"}"'
                else:
                    executable = f'"{executable}"'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, executable)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False
