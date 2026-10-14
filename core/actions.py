import json
import logging
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional


def _translate_chunk(chunk: str, target_lang: str, log: logging.Logger) -> str:
    """Translates a single chunk using resilient multi-tier fallbacks."""
    # Strategy 1: Google Translate Chrome Extension endpoint (clients5) - highly reliable, no 429
    try:
        url = f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl={target_lang}"
        post_data = urllib.parse.urlencode({"q": chunk}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=post_data,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=9) as res:
            parsed = json.loads(res.read().decode("utf-8"))
            if isinstance(parsed, list) and parsed:
                if isinstance(parsed[0], list):
                    res_txt = "".join(p[0] for p in parsed if isinstance(p, list) and p and isinstance(p[0], str))
                    if res_txt:
                        return res_txt
                elif isinstance(parsed[0], str):
                    return parsed[0]
            elif isinstance(parsed, str) and parsed:
                return parsed
    except Exception as exc1:
        log.debug("Primary translation endpoint (clients5) failed: %s", exc1)

    # Strategy 2: Google Translate GTX endpoint (translate.googleapis.com)
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={target_lang}&dt=t"
        post_data = urllib.parse.urlencode({"q": chunk}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=post_data,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=9) as res:
            parsed = json.loads(res.read().decode("utf-8"))
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
                res_txt = "".join([part[0] for part in parsed[0] if part and part[0]])
                if res_txt:
                    return res_txt
    except Exception as exc2:
        log.debug("Secondary translation endpoint (gtx) failed: %s", exc2)

    # Strategy 3: MyMemory free translation API fallback
    try:
        sub_chunks = [chunk[i : i + 450] for i in range(0, len(chunk), 450)]
        sub_res = []
        for sc in sub_chunks:
            mm_url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(sc)}&langpair=auto|{target_lang}"
            mm_req = urllib.request.Request(mm_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(mm_req, timeout=9) as mm_resp:
                mm_data = json.loads(mm_resp.read().decode("utf-8"))
                tr = mm_data.get("responseData", {}).get("translatedText", "")
                if tr:
                    sub_res.append(tr)
                else:
                    sub_res.append(sc)
        if sub_res:
            return "".join(sub_res)
    except Exception as exc3:
        log.warning("Tertiary translation endpoint (MyMemory) failed: %s", exc3)

    raise RuntimeError("Все доступные сервисы перевода временно недоступны или ограничены по лимиту (429)")


def translate_text(
    text: str,
    target_lang: str = "ru",
    logger: Optional[logging.Logger] = None,
) -> str:
    """Instantly translates text into target language using Google Translate API via POST.

    Supports arbitrarily large texts without HTTP 414 URL length limits.
    Features automatic fallback across multiple providers to prevent HTTP 429 rate limit errors.
    """
    log = logger or logging.getLogger(__name__)
    clean_text = text.strip()
    if not clean_text:
        return ""

    try:
        max_chunk = 2500
        if len(clean_text) <= max_chunk:
            chunks = [clean_text]
        else:
            chunks = []
            paragraphs = clean_text.splitlines(keepends=True)
            cur = ""
            for p in paragraphs:
                if len(cur) + len(p) > max_chunk:
                    if cur:
                        chunks.append(cur)
                    cur = p
                else:
                    cur += p
            if cur:
                chunks.append(cur)

        translated_parts = []
        for chunk in chunks:
            translated_parts.append(_translate_chunk(chunk, target_lang=target_lang, log=log))

        return "".join(translated_parts).strip()
    except Exception as exc:
        log.warning("Ошибка автоматического перевода: %s", exc)
        return f"[Ошибка перевода: {exc}]"


def _create_lens_upload_html(image_path: Path, log: logging.Logger) -> Path:
    """Creates a temporary HTML page that automatically submits the image to Google Lens via browser form POST."""
    import base64
    import io
    import tempfile
    from PIL import Image

    mime_type = "image/png"
    filename = "screenshot.png"
    w, h = 1000, 500

    try:
        with Image.open(image_path) as im:
            w, h = im.size
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            max_edge = 2560
            if max(w, h) > max_edge:
                scale = max_edge / max(w, h)
                w, h = int(w * scale), int(h * scale)
                im = im.resize((w, h), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=92)
            img_bytes = buf.getvalue()
            mime_type = "image/jpeg"
            filename = "screenshot.jpg"
    except Exception as exc:
        log.warning("Image optimization fallback to raw bytes: %s", exc)
        with open(image_path, "rb") as f:
            img_bytes = f.read()

    b64_img = base64.b64encode(img_bytes).decode("ascii")

    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <title>ScreenLens • Google Lens</title>
    <style>
        body {{
            background-color: #12151c;
            color: #00e5ff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            overflow: hidden;
        }}
        .spinner {{
            border: 4px solid rgba(0, 229, 255, 0.15);
            border-top: 4px solid #00e5ff;
            border-radius: 50%;
            width: 44px;
            height: 44px;
            animation: spin 0.8s linear infinite;
            margin-bottom: 16px;
        }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
        h2 {{ font-size: 17px; margin: 0 0 6px 0; color: #ffffff; font-weight: 600; }}
        p {{ font-size: 13px; color: #8892b0; margin: 0; }}
    </style>
</head>
<body>
    <div class="spinner"></div>
    <h2>Отправка в Google Lens...</h2>
    <p>Браузер выполняет поиск в контексте вашей сессии</p>

    <form id="lensForm" action="https://lens.google.com/v3/upload?hl=ru&ep=gisb&re=df&vpw=1920&vph=1080" method="POST" enctype="multipart/form-data">
        <input type="file" id="encoded_image" name="encoded_image" style="display:none;">
        <input type="hidden" name="processed_image_dimensions" value="{w},{h}">
        <input type="hidden" name="original_width" value="{w}">
        <input type="hidden" name="original_height" value="{h}">
        <input type="hidden" name="sbisrc" value="gisb">
    </form>

    <script>
        function submitLens() {{
            try {{
                const b64 = "{b64_img}";
                const byteChars = atob(b64);
                const byteArray = new Uint8Array(byteChars.length);
                for (let i = 0; i < byteChars.length; i++) {{
                    byteArray[i] = byteChars.charCodeAt(i);
                }}
                const blob = new Blob([byteArray], {{ type: "{mime_type}" }});
                const file = new File([blob], "{filename}", {{ type: "{mime_type}" }});

                const dt = new DataTransfer();
                dt.items.add(file);
                document.getElementById("encoded_image").files = dt.files;
                document.getElementById("lensForm").submit();
            }} catch (err) {{
                console.error("Auto-submit error:", err);
                window.location.href = "https://images.google.com";
            }}
        }}

        if (document.readyState === "loading") {{
            document.addEventListener("DOMContentLoaded", submitLens);
        }} else {{
            submitLens();
        }}
    </script>
</body>
</html>"""

    import time

    try:
        temp_dir = Path(tempfile.gettempdir())
        for old_html in temp_dir.glob("screenlens_lens_upload_*.html"):
            try:
                old_html.unlink()
            except OSError:
                pass
    except Exception:
        pass

    html_path = Path(tempfile.gettempdir()) / f"screenlens_lens_upload_{int(time.time() * 1000)}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return html_path


def safe_open_browser_url(url: str, logger: Optional[logging.Logger] = None) -> bool:
    """Safely opens URL in default web browser with correct working directory to prevent DLL crashes."""
    import sys
    import subprocess
    from pathlib import Path

    log = logger or logging.getLogger(__name__)

    clean_url = url.strip()
    if not clean_url.startswith(("http://", "https://", "file://")):
        p = Path(clean_url)
        if p.is_file():
            clean_url = p.resolve().as_uri()
        else:
            clean_url = f"https://{clean_url}"

    url = clean_url

    if sys.platform == "win32":
        try:
            import winreg
            import re

            browser_exe = None
            cmd = None
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
                ) as key:
                    prog_id, _ = winreg.QueryValueEx(key, "ProgId")

                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
                    cmd, _ = winreg.QueryValueEx(key, "")

                match = re.search(r'"([^"]+)"', cmd)
                if match:
                    browser_exe = match.group(1)
                else:
                    parts = cmd.split()
                    if parts:
                        browser_exe = parts[0]
            except Exception:
                pass

            if browser_exe and Path(browser_exe).is_file():
                browser_dir = str(Path(browser_exe).parent)
                if cmd and "%1" in cmd:
                    launch_cmd = cmd.replace("%1", url)
                    subprocess.Popen(launch_cmd, cwd=browser_dir)
                else:
                    subprocess.Popen([browser_exe, url], cwd=browser_dir)
                log.info("Safely opened URL with %s (cwd=%s): %s", Path(browser_exe).name, browser_dir, url[:60])
                return True
        except Exception as exc:
            log.warning("Safe browser launch failed, falling back to webbrowser: %s", exc)

    return webbrowser.open(url)


def _trigger_auto_paste(log: logging.Logger) -> None:
    import sys
    if sys.platform != "win32":
        return

    def _worker():
        try:
            import time
            import ctypes

            user32 = ctypes.windll.user32

            def send_key(vk: int, up: bool = False):
                flags = 2 if up else 0
                user32.keybd_event(vk, 0, flags, 0)

            time.sleep(1.8)
            # Ctrl + V directly (Google Images intercepts paste globally)
            send_key(0x11, False)
            time.sleep(0.04)
            send_key(0x56, False)
            time.sleep(0.04)
            send_key(0x56, True)
            time.sleep(0.04)
            send_key(0x11, True)
        except Exception as exc:
            log.warning("Auto-paste error: %s", exc)

    import threading
    threading.Thread(target=_worker, daemon=True).start()


def open_in_google_lens(
    image_path: Optional[Path] = None,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Opens screenshot in Google Lens with automatic image search.

    Uploads image directly to Google Lens via browser form POST in a temporary HTML page
    so session cookies, credentials, and redirect are handled directly in user's browser.
    Falls back to clipboard copy + auto-paste into Google Images.
    """
    import tempfile
    log = logger or logging.getLogger(__name__)

    if not image_path or not image_path.is_file():
        for candidate in [
            Path(tempfile.gettempdir()) / "screenlens_current.png",
            Path(tempfile.gettempdir()) / "screenlens_current.jpg",
        ]:
            if candidate.is_file():
                image_path = candidate
                break

    if image_path and image_path.is_file():
        # 1. Guarantee screenshot is in Windows clipboard (CF_DIB)
        try:
            from core.clipboard import copy_image_to_clipboard
            copy_image_to_clipboard(image_path)
            log.info("Screenshot copied to clipboard for Google Lens: %s", image_path)
        except Exception as exc:
            log.warning("Could not copy screenshot to clipboard: %s", exc)

        # 2. Browser-driven upload via auto-submitting temporary HTML form
        try:
            html_path = _create_lens_upload_html(image_path, log)
            if html_path and html_path.is_file():
                log.info("Created Google Lens browser upload form: %s", html_path)
                ok = safe_open_browser_url(html_path.as_uri(), log)
                if ok:
                    return True
        except Exception as exc:
            log.warning("Browser Google Lens upload failed, falling back: %s", exc)

    # 3. Fallback: Open Google Images and trigger auto-paste
    target_url = "https://images.google.com"
    ok = safe_open_browser_url(target_url, log)
    if ok:
        log.info("Opened Google Images fallback: %s", target_url)
        _trigger_auto_paste(log)

    return ok


def open_browser_url(url: str) -> bool:
    """Opens specified URL in default web browser."""
    return safe_open_browser_url(url)
