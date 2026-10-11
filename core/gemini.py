import base64
import http.client
import json
import logging
import os
import socket
import ssl
import struct
import time
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


DOT_RESOLVERS = (
    ("dns.comss.one", ("212.109.195.93", "195.133.25.16", "83.220.169.155")),
    ("dns.dns-ai.ru", ("186.246.48.217", "192.144.59.14", "186.246.49.127")),
)
DOT_PORT = 853
DNS_AI_BOOTSTRAP_IPS = ("186.246.48.217", "192.144.59.14", "186.246.49.127")
DNS_AI_HOST = "dns.dns-ai.ru"
DNS_AI_PORT = 853
DEFAULT_FALLBACK_SNI_IPS = (
    "132.243.112.221",
    "157.22.178.170",
    "191.44.41.215",
    "191.44.41.79",
)
TARGET_HOST = "generativelanguage.googleapis.com"

_cached_dns_ai_ip: Optional[str] = None
_cached_dns_ai_time: float = 0.0
_cooldown_sni_ips: set[str] = set()


def _build_dns_query(domain: str) -> bytes:
    """Builds RFC 1035 wire format A-record query with 2-byte TCP length prefix."""
    txid = 0x5432
    flags = 0x0100  # standard query, recursion desired
    header = struct.pack("!HHHHHH", txid, flags, 1, 0, 0, 0)
    labels = domain.encode("ascii").split(b".")
    qname = b"".join(bytes([len(l)]) + l for l in labels) + b"\x00"
    qtype_qclass = struct.pack("!HH", 1, 1)  # A (1), IN (1)
    query = header + qname + qtype_qclass
    return struct.pack("!H", len(query)) + query


def _parse_dns_response(data: bytes) -> list[str]:
    """Parses RFC 1035 DNS wire response with pointer compression (0xC0) support."""
    if len(data) < 12:
        return []
    txid, flags, qdcount, ancount, nscount, arcount = struct.unpack("!HHHHHH", data[:12])
    if ancount == 0:
        return []

    offset = 12
    # Skip Question section (qdcount entries)
    for _ in range(qdcount):
        while offset < len(data):
            length = data[offset]
            if length == 0:
                offset += 1
                break
            elif (length & 0xC0) == 0xC0:
                offset += 2
                break
            else:
                offset += 1 + length
        offset += 4  # skip QTYPE and QCLASS

    # Parse Answer section
    ips = []
    for _ in range(ancount):
        if offset >= len(data):
            break
        # Skip NAME (may be compressed pointer 0xC0 or sequence of labels)
        if (data[offset] & 0xC0) == 0xC0:
            offset += 2
        else:
            while offset < len(data):
                l = data[offset]
                if l == 0:
                    offset += 1
                    break
                elif (l & 0xC0) == 0xC0:
                    offset += 2
                    break
                else:
                    offset += 1 + l
        if offset + 10 > len(data):
            break
        rtype, rclass, ttl, rdlength = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        if rtype == 1 and rclass == 1 and rdlength == 4 and offset + 4 <= len(data):
            ip = socket.inet_ntoa(data[offset : offset + 4])
            ips.append(ip)
        offset += rdlength
    return ips


def resolve_via_dot(
    domain: str = TARGET_HOST,
    timeout: float = 2.5,
    logger: Optional[logging.Logger] = None,
) -> list[str]:
    """Resolves domain via DNS-over-TLS (DoT port 853) through comss.one and dns-ai.ru bootstrap IPs."""
    query_msg = _build_dns_query(domain)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    results: list[str] = []
    for host, bootstrap_ips in DOT_RESOLVERS:
        for bootstrap_ip in bootstrap_ips:
            sock = None
            try:
                # Low-level direct socket without third-party hooks / deadlocks
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                tls_sock = ctx.wrap_socket(sock, server_hostname=host)
                tls_sock.connect((bootstrap_ip, DOT_PORT))
                tls_sock.sendall(query_msg)

                len_prefix = tls_sock.recv(2)
                if len(len_prefix) < 2:
                    tls_sock.close()
                    continue
                resp_len = struct.unpack("!H", len_prefix)[0]
                resp_data = b""
                while len(resp_data) < resp_len:
                    chunk = tls_sock.recv(resp_len - len(resp_data))
                    if not chunk:
                        break
                    resp_data += chunk
                tls_sock.close()

                found = _parse_dns_response(resp_data)
                for ip in found:
                    if ip not in results:
                        results.append(ip)
                if results:
                    if logger:
                        logger.debug("DoT (%s) resolved %s via %s: %s", host, domain, bootstrap_ip, found)
                    return results
            except Exception as exc:
                if logger:
                    logger.debug("DoT query to %s failed via %s: %s", host, bootstrap_ip, exc)
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
    return results


def resolve_via_dns_ai(
    domain: str = TARGET_HOST,
    timeout: float = 2.5,
    logger: Optional[logging.Logger] = None,
) -> list[str]:
    """Resolves domain via DoT (alias for backwards compatibility)."""
    return resolve_via_dot(domain, timeout, logger)


def verify_sni_proxy(ip: str, host: str = TARGET_HOST, timeout: float = 1.5) -> bool:
    """Verifies that the candidate IP completes TLS handshake with host SNI."""
    if ip in _cooldown_sni_ips:
        return False
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((ip, 443), timeout=timeout) as s:
            with ctx.wrap_socket(s, server_hostname=host):
                return True
    except Exception:
        return False


def get_active_sni_proxy_ip(
    logger: Optional[logging.Logger] = None,
    force_refresh: bool = False,
) -> Optional[str]:
    """Returns a verified active SNI proxy IP for Google Gemini."""
    global _cached_dns_ai_ip, _cached_dns_ai_time
    now = time.time()

    if (
        not force_refresh
        and _cached_dns_ai_ip
        and (now - _cached_dns_ai_time < 3600)
        and (_cached_dns_ai_ip not in _cooldown_sni_ips)
    ):
        return _cached_dns_ai_ip

    candidates: list[str] = []
    try:
        resolved = resolve_via_dot(domain=TARGET_HOST, logger=logger)
        for ip in resolved:
            if ip not in candidates and ip not in _cooldown_sni_ips:
                candidates.append(ip)
    except Exception as exc:
        if logger:
            logger.debug("DoT resolution error: %s", exc)

    for fallback_ip in DEFAULT_FALLBACK_SNI_IPS:
        if fallback_ip not in candidates and fallback_ip not in _cooldown_sni_ips:
            candidates.append(fallback_ip)

    for candidate in candidates:
        if verify_sni_proxy(candidate, timeout=1.5):
            _cached_dns_ai_ip = candidate
            _cached_dns_ai_time = now
            if logger:
                logger.info("Selected active SNI proxy: %s", candidate)
            return candidate

    if logger:
        logger.warning("No responsive SNI proxy found among candidates: %s", candidates)
    return None


class DNSAIHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection routing traffic to SNI proxy IP while sending host SNI."""

    def __init__(self, sni_proxy_ip: str, host: str, **kwargs):
        self._sni_proxy_ip = sni_proxy_ip
        super().__init__(host, **kwargs)

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._sni_proxy_ip, self.port),
            self.timeout,
            self.source_address,
        )
        ctx = self._context or ssl.create_default_context()
        self.sock = ctx.wrap_socket(self.sock, server_hostname=self.host)


class DNSAIHTTPSHandler(urllib.request.HTTPSHandler):
    """urllib HTTPS handler routing connections via an SNI proxy IP."""

    def __init__(self, sni_proxy_ip: str, **kwargs):
        self._sni_proxy_ip = sni_proxy_ip
        super().__init__(**kwargs)

    def https_open(self, req: urllib.request.Request):
        return self.do_open(
            lambda host, **kwargs: DNSAIHTTPSConnection(self._sni_proxy_ip, host, **kwargs),
            req,
        )


def build_gemini_opener(
    proxy_url: Optional[str] = None,
    use_dns_ai: bool = True,
    logger: Optional[logging.Logger] = None,
) -> urllib.request.OpenerDirector:
    """Builds opener: routes via SNI proxy if enabled, else falls back to proxy_url / direct."""
    if use_dns_ai:
        sni_ip = get_active_sni_proxy_ip(logger=logger)
        if sni_ip:
            if logger:
                logger.info("Routing Gemini API via SNI proxy (%s)", sni_ip)
            return urllib.request.build_opener(DNSAIHTTPSHandler(sni_ip))
        elif logger:
            logger.warning("DNS-AI/Comss SNI proxy unavailable, falling back to standard proxy / direct connection")

    return build_proxy_opener(proxy_url, logger)


class GeminiVisionClient:
    """Client for Google Gemini multimodal vision models (Gemini 3.5 Flash Lite)."""

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
        use_dns_ai: Optional[bool] = None,
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

        if use_dns_ai is None:
            if proxy_url and proxy_url.strip():
                use_dns_ai = False
            else:
                try:
                    from storage.db import DatabaseManager
                    db_settings = DatabaseManager().get_settings()
                    use_dns_ai = getattr(db_settings, "gemini_use_dns_ai", True)
                except Exception:
                    use_dns_ai = True

        self._model = self.MODEL_ALIASES.get(model, model)
        self._use_dns_ai = use_dns_ai
        self._proxy_url = resolve_proxy(proxy_url, self._logger)
        self._active_sni_ip = get_active_sni_proxy_ip(logger=self._logger) if self._use_dns_ai else None
        self._opener = build_gemini_opener(
            proxy_url=self._proxy_url,
            use_dns_ai=self._use_dns_ai,
            logger=self._logger,
        )

    @property
    def use_dns_ai(self) -> bool:
        """Returns whether DNS-AI SNI bypass mode is enabled."""
        return self._use_dns_ai

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
                if self._use_dns_ai and self._active_sni_ip:
                    _cooldown_sni_ips.add(self._active_sni_ip)
                    next_sni = get_active_sni_proxy_ip(logger=self._logger, force_refresh=True)
                    if next_sni and next_sni != self._active_sni_ip:
                        self._active_sni_ip = next_sni
                        self._opener = urllib.request.build_opener(DNSAIHTTPSHandler(next_sni))
                        try:
                            with self._opener.open(req, timeout=30) as retry_resp:
                                parsed = json.loads(retry_resp.read().decode("utf-8"))
                                candidates = parsed.get("candidates", [])
                                if not candidates:
                                    return "Модель не вернула ответа."
                                parts = candidates[0].get("content", {}).get("parts", [])
                                return "".join(part.get("text", "") for part in parts).strip()
                        except Exception as retry_exc:
                            self._logger.warning("Retry with alternate SNI proxy %s failed: %s", next_sni, retry_exc)
                raise RuntimeError(
                    "Ошибка Gemini API (400): Текущая локация IP-адреса не поддерживается Google Gemini. "
                    "В v2RayTun переключитесь на профиль США или Нидерланды (IP Timeweb блокируется Google)."
                ) from exc
            if exc.code == 404:
                raise RuntimeError(
                    f"Ошибка Gemini API (404): Модель '{self._model}' недоступна в Google API. "
                    f"Обновите название модели в настройках."
                ) from exc
            raise RuntimeError(f"Ошибка Gemini API ({exc.code}): {err_msg}") from exc
        except Exception as exc:
            if self._use_dns_ai:
                global _cached_dns_ai_ip
                if self._active_sni_ip:
                    _cooldown_sni_ips.add(self._active_sni_ip)
                _cached_dns_ai_ip = None
                # Try one automatic failover to next SNI proxy
                next_sni = get_active_sni_proxy_ip(logger=self._logger, force_refresh=True)
                if next_sni and next_sni != self._active_sni_ip:
                    self._active_sni_ip = next_sni
                    self._opener = urllib.request.build_opener(DNSAIHTTPSHandler(next_sni))
                    try:
                        with self._opener.open(req, timeout=30) as retry_resp:
                            parsed = json.loads(retry_resp.read().decode("utf-8"))
                            candidates = parsed.get("candidates", [])
                            if not candidates:
                                return "Модель не вернула ответа."
                            parts = candidates[0].get("content", {}).get("parts", [])
                            return "".join(part.get("text", "") for part in parts).strip()
                    except Exception as retry_exc:
                        self._logger.warning("Retry with alternate SNI proxy %s failed: %s", next_sni, retry_exc)
            self._logger.error("Network or parsing error in Gemini client: %s", exc)
            raise RuntimeError(f"Сбой соединения с Gemini API: {exc}") from exc

