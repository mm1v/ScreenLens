import base64
import json
import logging
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional


def _is_tcp_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    """Checks if specified TCP port is listening."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def auto_detect_local_proxy(logger: Optional[logging.Logger] = None) -> Optional[str]:
    """Auto-detects active local proxy instances (v2RayTun, Xray, Clash/Mihomo)."""
    # 1. Check v2RayTun / Xray SOCKS5 port 10808
    if _is_tcp_port_open("127.0.0.1", 10808):
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            pref_path = Path(appdata) / "v2RayTun.net" / "v2RayTun" / "shared_preferences.json"
            if pref_path.is_file():
                try:
                    with open(pref_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    has_auth = data.get("flutter.settings_pref_socks5_auth", False)
                    login = data.get("flutter.settings_pref_socks5_auth_login", "")
                    pwd = data.get("flutter.settings_pref_socks5_auth_password", "")
                    if has_auth and login and pwd:
                        if logger:
                            logger.info("Auto-detected authenticated v2RayTun SOCKS5 proxy on 127.0.0.1:10808")
                        return f"socks5://{login}:{pwd}@127.0.0.1:10808"
                except Exception as exc:
                    if logger:
                        logger.debug("Failed parsing v2RayTun preferences: %s", exc)
        if logger:
            logger.info("Auto-detected local SOCKS5 proxy on 127.0.0.1:10808")
        return "socks5://127.0.0.1:10808"

    # 2. Check HTTP inbound on 127.0.0.1:10809 (v2RayTun HTTP)
    if _is_tcp_port_open("127.0.0.1", 10809):
        if logger:
            logger.info("Auto-detected local HTTP proxy on 127.0.0.1:10809")
        return "http://127.0.0.1:10809"

    # 3. Check HTTP inbound on 127.0.0.1:7890 (Clash / Mihomo)
    if _is_tcp_port_open("127.0.0.1", 7890):
        if logger:
            logger.info("Auto-detected local Clash HTTP proxy on 127.0.0.1:7890")
        return "http://127.0.0.1:7890"

    return None


def resolve_proxy(
    explicit_proxy: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Optional[str]:
    """Resolves proxy URL: explicit > DB setting > env vars > auto-detect local proxy."""
    if explicit_proxy and explicit_proxy.strip():
        return explicit_proxy.strip()

    try:
        from storage.db import DatabaseManager
        db_settings = DatabaseManager().get_settings()
        gemini_proxy = getattr(db_settings, "gemini_proxy", "")
        if gemini_proxy and gemini_proxy.strip():
            return gemini_proxy.strip()
    except Exception as exc:
        if logger:
            logger.debug("Could not read proxy from database: %s", exc)

    for env_var in ("GEMINI_PROXY", "ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        val = os.environ.get(env_var, "").strip()
        if val:
            return val

    return auto_detect_local_proxy(logger)


def build_proxy_opener(
    proxy_url: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> urllib.request.OpenerDirector:
    """Constructs an urllib OpenerDirector supporting HTTP and SOCKS4/5 proxies."""
    if not proxy_url or not proxy_url.strip():
        return urllib.request.build_opener()

    url_str = proxy_url.strip()
    parsed = urllib.parse.urlparse(url_str)
    scheme = parsed.scheme.lower()

    if scheme in ("socks5", "socks5h", "socks4"):
        try:
            import socks
            from sockshandler import SocksiPyHandler
            socks_type = socks.SOCKS5 if "5" in scheme else socks.SOCKS4
            rdns = scheme in ("socks5", "socks5h")
            handler = SocksiPyHandler(
                socks_type,
                parsed.hostname,
                parsed.port or 10808,
                rdns,
                parsed.username,
                parsed.password,
            )
            return urllib.request.build_opener(handler)
        except ImportError:
            if logger:
                logger.warning("PySocks is not installed; cannot route through SOCKS proxy %s", url_str)
            return urllib.request.build_opener()
    elif scheme in ("http", "https"):
        handler = urllib.request.ProxyHandler({"http": url_str, "https": url_str})
        return urllib.request.build_opener(handler)

    if logger:
        logger.warning("Unsupported proxy scheme '%s', falling back to direct connection", scheme)
    return urllib.request.build_opener()


class GeminiVisionClient:
    """Client for Google Gemini multimodal vision models (Gemini 2.0 Flash / 1.5 Flash)."""

    DEFAULT_MODEL = "gemini-3.5-flash-lite"
    MODEL_ALIASES = {
        "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
        "gemini-2.5-flash": "gemini-3.5-flash-lite",
        "gemini-2.0-flash": "gemini-3.5-flash-lite",
    }
    API_URL_TEMPLATE = (
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        proxy_url: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        if api_key is not None:
            self._api_key = api_key.strip()
        else:
            resolved_key = os.environ.get("GEMINI_API_KEY", "").strip()
            if not resolved_key:
                try:
                    from storage.db import DatabaseManager
                    db_settings = DatabaseManager().get_settings()
                    resolved_key = (db_settings.gemini_api_key or "").strip()
                except Exception as exc:
                    self._logger.warning("Could not read Gemini API key from database: %s", exc)
            self._api_key = resolved_key

        self._model = self.MODEL_ALIASES.get(model, model)
        self._proxy_url = resolve_proxy(proxy_url, self._logger)
        self._opener = build_proxy_opener(self._proxy_url, self._logger)

    @property
    def proxy_url(self) -> Optional[str]:
        """Returns the configured or auto-detected proxy URL."""
        return self._proxy_url

    @property
    def is_configured(self) -> bool:
        """Returns True if an API key is available."""
        return bool(self._api_key and self._api_key.strip())

    def analyze_image(
        self,
        image_path: Path,
        prompt: str = "Опиши подробно, что изображено на этом снимке экрана. Если здесь текст или ошибка кода, объясни суть и решение.",
    ) -> str:
        """Sends image to Gemini Vision API with specified prompt and returns textual response."""
        if not self.is_configured:
            raise ValueError(
                "GEMINI_API_KEY не задан! Задайте переменную окружения GEMINI_API_KEY "
                "или получите бесплатный ключ в Google AI Studio: https://aistudio.google.com/"
            )

        if not image_path.is_file():
            raise FileNotFoundError(f"Файл изображения не найден: {image_path}")

        # Read and encode image to base64
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": image_b64,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1024,
            },
        }

        url = self.API_URL_TEMPLATE.format(model=self._model, api_key=self._api_key)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with self._opener.open(req, timeout=30) as response:
                res_body = response.read().decode("utf-8")
                parsed = json.loads(res_body)

                candidates = parsed.get("candidates", [])
                if not candidates:
                    return "Модель не вернула ответа."

                parts = candidates[0].get("content", {}).get("parts", [])
                return "".join(part.get("text", "") for part in parts).strip()
        except urllib.error.HTTPError as exc:
            err_msg = exc.read().decode("utf-8", errors="replace")
            self._logger.error("Gemini API error (%d): %s", exc.code, err_msg)
            if exc.code == 400 and ("User location is not supported" in err_msg or "FAILED_PRECONDITION" in err_msg):
                raise RuntimeError(
                    "Ошибка Gemini API (400): Текущая локация IP-адреса не поддерживается Google Gemini. "
                    "В v2RayTun переключитесь на профиль 🇺🇸 США или 🇳🇱 Нидерланды (IP Timeweb блокируется Google)."
                ) from exc
            if exc.code == 404:
                raise RuntimeError(
                    f"Ошибка Gemini API (404): Модель '{self._model}' недоступна в Google API. "
                    f"Обновите название модели в настройках."
                ) from exc
            raise RuntimeError(f"Ошибка Gemini API ({exc.code}): {err_msg}") from exc
        except Exception as exc:
            self._logger.error("Network or parsing error in Gemini client: %s", exc)
            raise RuntimeError(f"Сбой соединения с Gemini API: {exc}") from exc

