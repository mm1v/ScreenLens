import os
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from PIL import Image, ImageDraw, ImageTk

from core.models import CaptureArea, ScreenshotResult, OCRResult
from core.screenshot import ScreenGrabber
from core.ocr import TesseractNotFoundError, TesseractOCREngine


class TestModels(unittest.TestCase):
    def test_capture_area_properties(self):
        area = CaptureArea(x1=100, y1=200, x2=300, y2=450)
        self.assertEqual(area.width, 200)
        self.assertEqual(area.height, 250)
        self.assertEqual(area.bbox, (100, 200, 300, 450))
        self.assertTrue(area.is_valid)

    def test_capture_area_inverted_coordinates(self):
        area = CaptureArea(x1=300, y1=450, x2=100, y2=200)
        self.assertEqual(area.width, 200)
        self.assertEqual(area.height, 250)
        self.assertEqual(area.bbox, (100, 200, 300, 450))
        self.assertTrue(area.is_valid)

    def test_capture_area_too_small(self):
        area = CaptureArea(x1=10, y1=10, x2=11, y2=11)
        self.assertFalse(area.is_valid)

    def test_ocr_result_empty(self):
        res_empty = OCRResult(
            raw_text="   \n  \n",
            cleaned_text="",
            image_path=Path("dummy.png"),
        )
        self.assertTrue(res_empty.is_empty)

        res_text = OCRResult(
            raw_text="Hello World\n",
            cleaned_text="Hello World",
            image_path=Path("dummy.png"),
        )
        self.assertFalse(res_text.is_empty)


class TestScreenshot(unittest.TestCase):
    def test_capture_invalid_area_raises(self):
        grabber = ScreenGrabber()
        with self.assertRaises(ValueError):
            grabber.capture(CaptureArea(10, 10, 11, 11))

    def test_capture_valid_area(self):
        grabber = ScreenGrabber()
        area = CaptureArea(50, 50, 150, 150)
        result = grabber.capture(area)

        self.assertIsInstance(result, ScreenshotResult)
        self.assertTrue(result.image_path.exists())
        self.assertEqual(result.width, 100)
        self.assertEqual(result.height, 100)

        # Cleanup
        try:
            result.image_path.unlink()
        except OSError:
            pass


class TestOCREngine(unittest.TestCase):
    def test_tesseract_missing_raises_custom_exception(self):
        fake_path = "C:/non_existent_path_to_tesseract/tesseract.exe"
        with self.assertRaises(TesseractNotFoundError) as ctx:
            TesseractOCREngine(tesseract_cmd=fake_path)
        self.assertIn("не найден", str(ctx.exception))

    def test_factory_creates_engine(self):
        from core.ocr import create_ocr_engine
        engine = create_ocr_engine(engine_name="auto")
        self.assertIsNotNone(engine)

    def test_normalize_intra_word_homoglyphs(self):
        from core.ocr import _normalize_intra_word_homoglyphs
        # Cyrillic word with Latin homoglyph 'p'
        self.assertEqual(_normalize_intra_word_homoglyphs("Пpивет"), "Привет")
        # Latin word with Cyrillic homoglyph 'е'
        self.assertEqual(_normalize_intra_word_homoglyphs("hеllo"), "hello")
        # Pure words remain untouched
        self.assertEqual(_normalize_intra_word_homoglyphs("ScreenLens"), "ScreenLens")
        self.assertEqual(_normalize_intra_word_homoglyphs("Ошибка"), "Ошибка")

    def test_dual_language_merging(self):
        from core.ocr import WindowsMediaOCREngine
        engine = WindowsMediaOCREngine()

        # Simulated ru and en structures
        res_ru = {
            "lines": [
                {
                    "text": "ScreenLens APl: Ошибка входа",
                    "words": [
                        {"text": "ScreenLens", "bounding_rect": {"x": 20.0, "y": 20.0, "width": 80.0, "height": 20.0}},
                        {"text": "APl:", "bounding_rect": {"x": 105.0, "y": 20.0, "width": 35.0, "height": 20.0}},
                        {"text": "Ошибка", "bounding_rect": {"x": 145.0, "y": 20.0, "width": 60.0, "height": 20.0}},
                        {"text": "входа", "bounding_rect": {"x": 210.0, "y": 20.0, "width": 50.0, "height": 20.0}},
                    ],
                }
            ]
        }
        res_en = {
            "lines": [
                {
                    "text": "ScreenLens API: OUJV16Ka Bxoga",
                    "words": [
                        {"text": "ScreenLens", "bounding_rect": {"x": 20.0, "y": 20.0, "width": 80.0, "height": 20.0}},
                        {"text": "API:", "bounding_rect": {"x": 105.0, "y": 20.0, "width": 35.0, "height": 20.0}},
                        {"text": "OUJV16Ka", "bounding_rect": {"x": 145.0, "y": 20.0, "width": 60.0, "height": 20.0}},
                        {"text": "Bxoga", "bounding_rect": {"x": 210.0, "y": 20.0, "width": 50.0, "height": 20.0}},
                    ],
                }
            ]
        }

        merged = engine._merge_dual_language_results(res_ru, res_en)
        # Should preserve 'ScreenLens', take clean 'API:' from en, and clean Russian 'Ошибка входа' from ru
        self.assertIn("ScreenLens", merged)
        self.assertIn("API:", merged)
        self.assertIn("Ошибка", merged)
        self.assertIn("входа", merged)
        self.assertNotIn("OUJV16Ka", merged)

    def test_minimal_separated_boxes_no_duplicate(self):
        """Verifies that when OCR splits a line into hash and message columns, message is not duplicated."""
        from core.ocr import WindowsMediaOCREngine
        engine = WindowsMediaOCREngine()

        res_ru = {
            "lines": [
                {"text": "2b1785e", "words": [{"text": "2b1785e", "bounding_rect": {"x": 20.0, "y": 50.0, "width": 40.0, "height": 10.0}}]},
                {"text": "сообщение коммита", "words": [
                    {"text": "сообщение", "bounding_rect": {"x": 100.0, "y": 50.0, "width": 80.0, "height": 10.0}},
                    {"text": "коммита", "bounding_rect": {"x": 190.0, "y": 50.0, "width": 70.0, "height": 10.0}},
                ]},
            ]
        }
        res_en = {
            "lines": [
                {"text": "2b1785e", "words": [{"text": "2b1785e", "bounding_rect": {"x": 20.0, "y": 50.0, "width": 40.0, "height": 10.0}}]},
                {"text": "commit message", "words": [
                    {"text": "commit", "bounding_rect": {"x": 100.0, "y": 50.0, "width": 50.0, "height": 10.0}},
                    {"text": "message", "bounding_rect": {"x": 160.0, "y": 50.0, "width": 60.0, "height": 10.0}},
                ]},
            ]
        }

        merged = engine._merge_dual_language_results(res_ru, res_en)
        lines = [l.strip() for l in merged.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("2b1785e"))
        self.assertIn("сообщение", lines[0])

    def test_commits_list_regression_4_5_and_8_items(self):
        """Verifies that git commit lists with separated hash and message columns maintain correct line-by-line pairing."""
        from core.ocr import WindowsMediaOCREngine
        engine = WindowsMediaOCREngine()

        for count in (4, 5, 8):
            ru_hash_boxes = []
            ru_msg_boxes = []
            en_hash_boxes = []
            en_msg_boxes = []

            for i in range(count):
                y = 20.0 + i * 30.0
                h_val = f"hash{i:03d}"
                m_ru = f"сообщение номер {i}"
                m_en = f"commit message {i}"

                ru_hash_boxes.append({
                    "text": h_val,
                    "words": [{"text": h_val, "bounding_rect": {"x": 10.0, "y": y, "width": 40.0, "height": 12.0}}],
                })
                ru_msg_boxes.append({
                    "text": m_ru,
                    "words": [{"text": m_ru, "bounding_rect": {"x": 80.0, "y": y, "width": 150.0, "height": 12.0}}],
                })
                en_hash_boxes.append({
                    "text": h_val,
                    "words": [{"text": h_val, "bounding_rect": {"x": 10.0, "y": y, "width": 40.0, "height": 12.0}}],
                })
                en_msg_boxes.append({
                    "text": m_en,
                    "words": [{"text": m_en, "bounding_rect": {"x": 80.0, "y": y, "width": 150.0, "height": 12.0}}],
                })

            # Simulate Windows Media OCR multi-column return: all hashes first, then all messages
            res_ru = {"lines": ru_hash_boxes + ru_msg_boxes}
            res_en = {"lines": en_hash_boxes + en_msg_boxes}

            merged = engine._merge_dual_language_results(res_ru, res_en)
            lines = [l.strip() for l in merged.splitlines() if l.strip()]

            self.assertEqual(len(lines), count, f"Expected {count} lines, got {len(lines)}: {lines}")
            for i, line in enumerate(lines):
                expected_hash = f"hash{i:03d}"
                self.assertTrue(line.startswith(expected_hash), f"Line {i} does not start with {expected_hash}: {line}")
                self.assertIn(f"номер {i}", line, f"Line {i} missing message {i}: {line}")

    def test_incomplete_ocr_pass_preserves_vertical_order(self):
        """Verifies that if one language pass misses a middle line, it remains at its proper vertical position."""
        from core.ocr import WindowsMediaOCREngine
        engine = WindowsMediaOCREngine()

        # RU only detects line 1 and line 3
        res_ru = {
            "lines": [
                {"text": "первая строка", "words": [{"text": "первая", "bounding_rect": {"x": 10.0, "y": 10.0, "width": 60.0, "height": 12.0}}]},
                {"text": "третья строка", "words": [{"text": "третья", "bounding_rect": {"x": 10.0, "y": 70.0, "width": 60.0, "height": 12.0}}]},
            ]
        }
        # EN detects all three lines (including line 2 at y=40)
        res_en = {
            "lines": [
                {"text": "first line", "words": [
                    {"text": "first", "bounding_rect": {"x": 10.0, "y": 10.0, "width": 30.0, "height": 12.0}},
                    {"text": "line", "bounding_rect": {"x": 45.0, "y": 10.0, "width": 25.0, "height": 12.0}},
                ]},
                {"text": "second line en", "words": [
                    {"text": "second", "bounding_rect": {"x": 10.0, "y": 40.0, "width": 30.0, "height": 12.0}},
                    {"text": "line", "bounding_rect": {"x": 45.0, "y": 40.0, "width": 25.0, "height": 12.0}},
                    {"text": "en", "bounding_rect": {"x": 75.0, "y": 40.0, "width": 15.0, "height": 12.0}},
                ]},
                {"text": "third line", "words": [
                    {"text": "third", "bounding_rect": {"x": 10.0, "y": 70.0, "width": 30.0, "height": 12.0}},
                    {"text": "line", "bounding_rect": {"x": 45.0, "y": 70.0, "width": 25.0, "height": 12.0}},
                ]},
            ]
        }

        merged = engine._merge_dual_language_results(res_ru, res_en)
        lines = [l.strip() for l in merged.splitlines() if l.strip()]

        self.assertEqual(len(lines), 3)
        self.assertIn("первая", lines[0])
        self.assertIn("second line en", lines[1])
        self.assertIn("третья", lines[2])



class TestClipboard(unittest.TestCase):
    def test_clipboard_copy(self):
        from core.clipboard import ClipboardManager
        import pyperclip
        cm = ClipboardManager()
        success = cm.copy("ScreenLens Unit Test")
        self.assertTrue(success)
        self.assertEqual(pyperclip.paste(), "ScreenLens Unit Test")

    def test_clipboard_empty_returns_false(self):
        from core.clipboard import ClipboardManager
        cm = ClipboardManager()
        self.assertFalse(cm.copy(""))

    def test_clipboard_copy_image(self):
        from core.clipboard import ClipboardManager
        from PIL import Image
        cm = ClipboardManager()
        img = Image.new("RGB", (50, 50), color="blue")
        ok = cm.copy_image(img)
        self.assertTrue(ok)



class TestHotkey(unittest.TestCase):
    def test_hotkey_listener_initialization(self):
        from core.hotkey import HotkeyListener
        called = False

        def trigger():
            nonlocal called
            called = True

        listener = HotkeyListener(hotkey_combo="<ctrl>+<alt>+t", on_trigger=trigger)
        self.assertIsNotNone(listener)

    def test_parse_combination(self):
        from core.hotkey import HotkeyListener, MOD_CONTROL, MOD_ALT, MOD_NOREPEAT
        mods, vk = HotkeyListener._parse_combination("<ctrl>+<alt>+s")
        self.assertEqual(mods, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT)
        self.assertEqual(vk, ord("S"))


class TestQRScanner(unittest.TestCase):
    def test_qr_scanner_no_file(self):
        from core.qr import QRCodeScanner
        scanner = QRCodeScanner()
        res = scanner.scan(Path("non_existent_qr.png"))
        self.assertIsNone(res)


class TestGeminiClient(unittest.TestCase):
    def test_gemini_not_configured_raises(self):
        from core.gemini import GeminiVisionClient
        client = GeminiVisionClient(api_key="")
        self.assertFalse(client.is_configured)
        with self.assertRaises(ValueError):
            client.analyze_image(Path("dummy.png"))

    def test_gemini_proxy_resolution(self):
        from core.gemini import resolve_proxy, build_proxy_opener, GeminiVisionClient
        # Explicit proxy
        resolved = resolve_proxy("http://127.0.0.1:8080")
        self.assertEqual(resolved, "http://127.0.0.1:8080")

        # Opener builds successfully
        opener_http = build_proxy_opener("http://127.0.0.1:8080")
        self.assertIsNotNone(opener_http)

        opener_socks = build_proxy_opener("socks5://user:pass@127.0.0.1:10808")
        self.assertIsNotNone(opener_socks)

        # Client proxy_url property
        client = GeminiVisionClient(api_key="dummy_key", proxy_url="socks5://127.0.0.1:10808")
        self.assertEqual(client.proxy_url, "socks5://127.0.0.1:10808")

    def test_dns_ai_builder_and_parser(self):
        from core.gemini import _build_dns_query, _parse_dns_response
        # Test query packing
        q = _build_dns_query("generativelanguage.googleapis.com")
        self.assertTrue(len(q) > 2)

        # Test empty / malformed packet
        self.assertEqual(_parse_dns_response(b""), [])

        # Test valid RFC 1035 packet with 0xC0 compression pointer
        mock_resp = bytes.fromhex(
            "123481800001000100000000"
            "1267656e657261746976656c616e67756167650a676f6f676c656170697303636f6d00"
            "00010001"
            "c00c"
            "00010001000000780004"
            "bf2c294f"
        )
        parsed = _parse_dns_response(mock_resp)
        self.assertEqual(parsed, ["191.44.41.79"])

    def test_gemini_client_lazy_opener_initialization(self):
        from core.gemini import GeminiVisionClient
        # When use_dns_ai is True, __init__ must NOT perform network queries or initialize opener synchronously
        client = GeminiVisionClient(api_key="test_key", use_dns_ai=True)
        self.assertIsNone(client._opener)
        self.assertIsNone(client._active_sni_ip)
        # Calling _get_opener initializes it
        opener = client._get_opener()
        self.assertIsNotNone(opener)
        self.assertIs(client._opener, opener)

    def test_gemini_client_dynamic_url_and_custom_model(self):
        from core.gemini import GeminiVisionClient
        # 1. Base URL with default model
        client = GeminiVisionClient(
            api_key="my_custom_key",
            model="gemini-3.5-flash-lite",
            api_url="https://generativelanguage.googleapis.com/v1beta",
        )
        url1 = client.build_request_url()
        self.assertEqual(url1, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent")
        self.assertEqual(client._model, "gemini-3.5-flash-lite")

        # 2. Base URL without /models appends /models/{model}:generateContent
        client2 = GeminiVisionClient(
            api_key="key_abc",
            model="custom-vision-v1",
            api_url="https://proxy.example.com/v1beta",
        )
        url2 = client2.build_request_url()
        self.assertEqual(url2, "https://proxy.example.com/v1beta/models/custom-vision-v1:generateContent")

        # 3. Base URL with /models appends /{model}:generateContent
        client3 = GeminiVisionClient(
            api_key="key_xyz",
            model="gemini-1.5-pro",
            api_url="https://gateway.example.com/v1beta/models",
        )
        url3 = client3.build_request_url()
        self.assertEqual(url3, "https://gateway.example.com/v1beta/models/gemini-1.5-pro:generateContent")


class TestActions(unittest.TestCase):
    def test_translate_empty_text(self):
        from core.actions import translate_text
        self.assertEqual(translate_text(""), "")

    def test_translate_english_to_russian(self):
        from core.actions import translate_text
        res = translate_text("Hello World", target_lang="ru")
        self.assertTrue(len(res) > 0)
        if "429" not in res and "Ошибка" not in res:
            self.assertIn("Привет", res)


class TestDatabaseManager(unittest.TestCase):
    def setUp(self):
        self.temp_db_path = Path("test_temp_db.db")
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()

    def tearDown(self):
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()

    def test_settings_save_and_retrieve(self):
        from storage.db import DatabaseManager, AppSettings
        db = DatabaseManager(db_path=self.temp_db_path)
        settings = db.get_settings()
        self.assertEqual(settings.hotkey, "<ctrl>+<alt>+space")

        settings.hotkey = "<ctrl>+<alt>+z"
        settings.gemini_api_key = "test_key_123"
        db.save_settings(settings)

        reloaded = db.get_settings()
        self.assertEqual(reloaded.hotkey, "<ctrl>+<alt>+z")
        self.assertEqual(reloaded.gemini_api_key, "test_key_123")

    def test_history_crud(self):
        from storage.db import DatabaseManager
        db = DatabaseManager(db_path=self.temp_db_path)
        id1 = db.add_history(image_path="test1.png", ocr_text="Text 1", qr_data="https://example.com")
        self.assertGreater(id1, 0)

        history = db.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].ocr_text, "Text 1")
        self.assertEqual(history[0].qr_data, "https://example.com")

        db.clear_history()
        self.assertEqual(len(db.get_history()), 0)


class TestCleanup(unittest.TestCase):
    def test_cleanup_temp_screenshots(self):
        from core.screenshot import cleanup_temp_screenshots
        temp_dir = Path(tempfile.gettempdir())
        old_file = temp_dir / "screenlens_test_cleanup_old.png"
        old_file.write_text("dummy")
        # Set modification time to 48 hours ago
        past_time = time.time() - 48 * 3600
        os.utime(old_file, (past_time, past_time))

        deleted = cleanup_temp_screenshots(max_age_hours=24)
        self.assertGreaterEqual(deleted, 1)
        self.assertFalse(old_file.exists())


class TestCaptureService(unittest.TestCase):
    def test_service_initialization(self):
        from core.service import CaptureService
        service = CaptureService()
        self.assertIsNotNone(service.grabber)
        self.assertIsNotNone(service.qr_scanner)
        self.assertIsNotNone(service.clipboard)


class TestIPC(unittest.TestCase):
    def test_single_instance_and_ipc(self):
        import sys
        import ctypes
        if sys.platform != "win32":
            return
        from app import acquire_primary_instance, signal_running_instance

        test_mutex = "Local\\ScreenLens_Test_Mutex_12345"
        test_event = "Local\\ScreenLens_Test_Event_12345"

        h_mutex, h_event = acquire_primary_instance(mutex_name=test_mutex, event_name=test_event)
        self.assertIsNotNone(h_mutex)
        self.assertIsNotNone(h_event)

        try:
            # Trying to acquire a second primary instance should return (None, None)
            h_m2, h_e2 = acquire_primary_instance(mutex_name=test_mutex, event_name=test_event)
            self.assertIsNone(h_m2)
            self.assertIsNone(h_e2)

            # Signaling the running instance should succeed
            signaled = signal_running_instance(event_name=test_event)
            self.assertTrue(signaled)
        finally:
            kernel32 = ctypes.windll.kernel32
            if h_mutex:
                kernel32.CloseHandle(h_mutex)
            if h_event:
                kernel32.CloseHandle(h_event)

class TestOverlayThreadSafety(unittest.TestCase):
    def test_safe_destruction_with_concurrent_worker(self):
        import tkinter as tk
        import threading
        import queue
        import time

        root = tk.Tk()
        is_destroyed = threading.Event()
        ui_q = queue.Queue()

        def process_q():
            if is_destroyed.is_set():
                return
            while not ui_q.empty():
                try:
                    fn = ui_q.get_nowait()
                    if not is_destroyed.is_set():
                        fn()
                except Exception:
                    break
            if not is_destroyed.is_set():
                try:
                    root.after(20, process_q)
                except Exception:
                    pass

        root.after(20, process_q)

        def worker():
            for _ in range(30):
                if is_destroyed.is_set():
                    break
                ui_q.put(lambda: None)
                time.sleep(0.005)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        def close():
            is_destroyed.set()
            root.destroy()

        root.after(60, close)
        root.mainloop()
        t.join(timeout=1.0)
        self.assertTrue(is_destroyed.is_set())


class TestGoogleLens(unittest.TestCase):
    def test_open_in_google_lens_fallback_and_handling(self):
        from core.actions import open_in_google_lens
        from unittest.mock import patch

        with patch("core.actions.safe_open_browser_url", return_value=True) as mock_open:
            result = open_in_google_lens(image_path=None)
            self.assertTrue(result)
            self.assertTrue(mock_open.called)

    def test_create_lens_upload_html(self):
        import logging
        from core.actions import _create_lens_upload_html

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            test_img_path = Path(tf.name)

        try:
            img = Image.new("RGB", (200, 100), color="blue")
            img.save(test_img_path, format="PNG")

            html_path = _create_lens_upload_html(test_img_path, logging.getLogger("test"))
            self.assertTrue(html_path.is_file())
            content = html_path.read_text(encoding="utf-8")

            self.assertIn("https://lens.google.com/v3/upload", content)
            self.assertIn('name="encoded_image"', content)
            self.assertIn('name="processed_image_dimensions"', content)
            self.assertIn("lensForm", content)
            self.assertIn("DataTransfer", content)
        finally:
            try:
                test_img_path.unlink()
            except OSError:
                pass

    def test_open_in_google_lens_browser_driven(self):
        import logging
        from core.actions import open_in_google_lens
        from unittest.mock import patch

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            test_img_path = Path(tf.name)

        try:
            img = Image.new("RGB", (300, 150), color="red")
            img.save(test_img_path, format="PNG")

            with patch("core.actions.safe_open_browser_url", return_value=True) as mock_open, \
                 patch("core.clipboard.copy_image_to_clipboard", return_value=True):
                result = open_in_google_lens(image_path=test_img_path, logger=logging.getLogger("test"))
                self.assertTrue(result)
                self.assertTrue(mock_open.called)
                opened_arg = mock_open.call_args[0][0]
                self.assertTrue(opened_arg.startswith("file://") or opened_arg.endswith(".html"))
        finally:
            try:
                test_img_path.unlink()
            except OSError:
                pass


class TestGoogleLensSelection(unittest.TestCase):
    def test_4_corners_calculation(self):
        from core.capture import (
            HANDLE_NW, HANDLE_NE, HANDLE_SE, HANDLE_SW,
            get_corner_positions,
        )
        x1, y1, x2, y2 = 100, 200, 300, 400
        corners = get_corner_positions(x1, y1, x2, y2)
        self.assertEqual(len(corners), 4)
        self.assertEqual(corners[HANDLE_NW], (100, 200))
        self.assertEqual(corners[HANDLE_NE], (300, 200))
        self.assertEqual(corners[HANDLE_SE], (300, 400))
        self.assertEqual(corners[HANDLE_SW], (100, 400))

    def test_hit_test_corners_and_move(self):
        from core.capture import (
            HANDLE_NW, HANDLE_SE, hit_test_lens
        )
        box = (100, 100, 300, 300)
        # 1. Corner hit test (NW, SE)
        self.assertEqual(hit_test_lens(105, 105, box, corner_hitbox=28), HANDLE_NW)
        self.assertEqual(hit_test_lens(295, 295, box, corner_hitbox=28), HANDLE_SE)
        # 2. Inside area test
        self.assertEqual(hit_test_lens(200, 200, box, corner_hitbox=28), "move")
        # 3. Outside test
        self.assertEqual(hit_test_lens(40, 40, box, corner_hitbox=28), "none")

    def test_apply_drag_resize(self):
        from core.capture import HANDLE_SE, HANDLE_NW, apply_drag_lens
        box = (100, 100, 300, 300)
        # Resize SE corner by +50, +50
        n_box = apply_drag_lens(HANDLE_SE, box, dx=50, dy=50, max_w=1920, max_h=1080)
        self.assertEqual(n_box, (100, 100, 350, 350))
        # Resize NW corner by +20, +30
        n_box2 = apply_drag_lens(HANDLE_NW, box, dx=20, dy=30, max_w=1920, max_h=1080)
        self.assertEqual(n_box2, (120, 130, 300, 300))

    def test_apply_drag_move_preserves_dimensions(self):
        from core.capture import apply_drag_lens
        box = (100, 100, 300, 250)
        w, h = 200, 150
        # Move by dx=40, dy=60
        moved = apply_drag_lens("move", box, dx=40, dy=60, max_w=1920, max_h=1080)
        self.assertEqual(moved, (140, 160, 340, 310))
        self.assertEqual(moved[2] - moved[0], w)
        self.assertEqual(moved[3] - moved[1], h)

    def test_corner_sprites_antialiasing(self):
        from ui.floating_hud import render_lens_corners
        corners = render_lens_corners(size=44, radius=16, leg=34, thickness=3)
        self.assertEqual(len(corners), 4)
        for key in ("nw", "ne", "sw", "se"):
            self.assertIn(key, corners)
            self.assertEqual(corners[key].size, (44, 44))

    def test_get_hud_placement(self):
        from ui.floating_hud import get_hud_placement
        screen_w, screen_h = 1920, 1080
        hud_w, hud_h = 394, 58
        # Normal central box: fits below
        hx, hy = get_hud_placement(500, 300, 900, 600, hud_w, hud_h, screen_w, screen_h, margin=14)
        self.assertEqual(hx, 500 + (400 - hud_w) // 2)
        self.assertEqual(hy, 600 + 14)

        # Box near bottom: places above
        hx2, hy2 = get_hud_placement(500, 800, 900, 1050, hud_w, hud_h, screen_w, screen_h, margin=14)
        self.assertEqual(hy2, 800 - hud_h - 14)

        # Box near right edge: clamped horizontally
        hx3, hy3 = get_hud_placement(1800, 300, 1910, 500, hud_w, hud_h, screen_w, screen_h, margin=14)
        self.assertEqual(hx3, screen_w - hud_w - 14)

        # Huge selection occupying screen (full screen): places inside selection with safe clearance from bottom
        hx_full, hy_full = get_hud_placement(0, 0, screen_w, screen_h, hud_w, hud_h, screen_w, screen_h, margin=14)
        self.assertEqual(hx_full, (screen_w - hud_w) // 2)
        self.assertLessEqual(hy_full + hud_h, screen_h - 36)
        self.assertGreaterEqual(hy_full, 0)

    def test_get_hud_placement_multimonitor(self):
        from ui.floating_hud import get_hud_placement
        vw, vh = 3680, 1080
        hud_w, hud_h = 394, 58
        mon1_bounds = (1920, 0, 3680, 942)  # Secondary monitor (1760x990 with 48px taskbar)

        # 1. Full-screen selection on secondary monitor
        hx, hy = get_hud_placement(
            1920, 0, 3680, 942, hud_w, hud_h, vw, vh, margin=14, monitor_bounds=mon1_bounds
        )
        # Horizontally centered within Monitor 1
        self.assertEqual(hx, 1920 + (1760 - hud_w) // 2)
        # Vertically inside selection with safe clearance above taskbar at 942
        self.assertLessEqual(hy + hud_h, 942 - 36)
        self.assertGreaterEqual(hy, 0)

        # 2. Near bottom of secondary monitor: must place above, never into taskbar or off-screen
        hx_bot, hy_bot = get_hud_placement(
            2000, 500, 2800, 930, hud_w, hud_h, vw, vh, margin=14, monitor_bounds=mon1_bounds
        )
        self.assertEqual(hy_bot, 500 - hud_h - 14)
        self.assertLessEqual(hy_bot + hud_h, 942)

    def test_get_card_placement_multimonitor(self):
        from ui.floating_hud import get_card_placement
        vw, vh = 3680, 1080
        mon0_bounds = (0, 0, 1920, 1032)
        # Selection near right edge of Primary Monitor: card would bleed onto Monitor 1 if virtual width was used
        box = (1500, 100, 1850, 600)
        card_w, card_h = 380, 300
        cx, cy = get_card_placement(
            box=box,
            card_w=card_w,
            card_h=card_h,
            hud_x=1500,
            hud_y=614,
            screen_w=vw,
            screen_h=vh,
            placement_mode="side_right",
            monitor_bounds=mon0_bounds,
        )
        # Must stay inside Primary Monitor bounds (not bleed to x >= 1920)
        self.assertLessEqual(cx + card_w, 1920)
        self.assertGreaterEqual(cx, 0)

    def test_cursor_map_valid_in_tkinter(self):
        import tkinter as tk
        from core.capture import CURSOR_MAP
        root = tk.Tk()
        try:
            cv = tk.Canvas(root, width=50, height=50)
            cv.pack()
            for handle, cursor_name in CURSOR_MAP.items():
                # Must not raise _tkinter.TclError: bad cursor spec
                cv.config(cursor=cursor_name)
                root.config(cursor=cursor_name)
            self.assertEqual(CURSOR_MAP["move"], "fleur")
        finally:
            root.destroy()

    def test_render_pill_badge(self):
        import tkinter as tk
        from ui.floating_hud import render_pill_badge
        root = tk.Tk()
        try:
            photo, w, h = render_pill_badge("1920×1080")
            self.assertIsNotNone(photo)
            self.assertGreater(w, 50)
            self.assertEqual(h, 22)
        finally:
            root.destroy()

    def test_capsule_button_and_rounded_card(self):
        import tkinter as tk
        from ui.floating_hud import CapsuleButton, RoundedCard
        root = tk.Tk()
        try:
            card = RoundedCard(root, width=400, height=200)
            clicked = [False]
            btn = CapsuleButton(
                card.content_frame,
                text="Test",
                style="danger",
                command=lambda: clicked.__setitem__(0, True),
            )
            btn.pack()
            root.update()
            btn._on_click(None)
            self.assertTrue(clicked[0])
            btn.set_text("Updated")
            self.assertEqual(btn.itemcget(btn.txt_id, "text"), "Updated")
            btn.config(state="disabled")
            self.assertEqual(btn._state, "disabled")
            btn.config(state="normal")
            self.assertEqual(btn._state, "normal")
        finally:
            root.destroy()

    def test_ai_input_placeholder_and_submit_guard(self):
        import tkinter as tk
        from unittest.mock import MagicMock
        from ui.floating_hud import RoundedCardController
        from ui.ai_panel import AIPanelController

        root = tk.Tk()
        try:
            cv = tk.Canvas(root, width=800, height=600)
            cv.pack()
            box = (100, 100, 400, 300)
            controller = RoundedCardController(
                canvas=cv,
                box=box,
                hud_pos=(100, 314, 442, 58),
                screen_size=(1920, 1080),
            )
            mock_session = MagicMock()
            mock_session.get_last_response.return_value = None

            card = AIPanelController(
                controller=controller,
                session=mock_session,
            )

            # Initially placeholder is active, text is muted gray, send button is disabled
            self.assertTrue(card._placeholder_active)
            self.assertEqual(card.ent.get(), "Вопрос по снимку...")
            self.assertEqual(card.btn_send._state, "disabled")

            # Calling submit with placeholder does not invoke session
            card._submit_query()
            mock_session.send_message.assert_not_called()

            # Typing actual text activates send button
            card.ent.delete(0, tk.END)
            card.ent.insert(0, "Что на фото?")
            card._placeholder_active = False
            card._update_send_state()
            self.assertEqual(card.btn_send._state, "normal")

            # Clearing text disables send button
            card.ent.delete(0, tk.END)
            card._update_send_state()
            self.assertEqual(card.btn_send._state, "disabled")

            controller.destroy()
        finally:
            root.destroy()

    def test_google_lens_hud_lifecycle(self):
        import tkinter as tk
        from ui.floating_hud import GoogleLensHUD
        root = tk.Tk()
        try:
            cv = tk.Canvas(root, width=800, height=600, bg="#101010")
            cv.pack()
            actions = []
            hud = GoogleLensHUD(
                parent=cv,
                on_text=lambda: actions.append("text"),
                on_translate=lambda: actions.append("translate"),
                on_lens=lambda: actions.append("lens"),
                on_ai=lambda: actions.append("ai"),
                on_copy=lambda: actions.append("copy"),
                on_close=lambda: actions.append("close"),
            )
            hw, hh = hud.get_dimensions()
            self.assertEqual(hw, 442)
            self.assertEqual(hh, 58)

            # Initially, active_mode MUST be None (neutral state, no accented button)
            self.assertIsNone(hud.active_mode)

            hud.draw(100, 100)
            self.assertTrue(hud.is_visible)
            self.assertIsNone(hud.active_mode)
            self.assertTrue(hud.is_inside(140, 120))
            self.assertTrue(hud.is_inside(420, 120))  # Block 2
            self.assertFalse(hud.is_inside(10, 10))

            # Test hit testing with swapped AI and Lens order
            self.assertEqual(hud.hit_test(140, 120), "text")
            self.assertEqual(hud.hit_test(268, 120), "ai")
            self.assertEqual(hud.hit_test(332, 120), "lens")
            self.assertEqual(hud.hit_test(498, 120), "close")

            # Test clicking buttons activates mode
            handled_text = hud.handle_click(140, 120)
            self.assertTrue(handled_text)
            self.assertIn("text", actions)
            self.assertEqual(hud.active_mode, "text")

            # Switching to ai transfers active mode
            hud.set_active_mode("ai")
            self.assertEqual(hud.active_mode, "ai")

            # Resetting active mode restores neutral state
            hud.reset_active_mode()
            self.assertIsNone(hud.active_mode)

            handled_close = hud.handle_click(498, 120)
            self.assertTrue(handled_close)
            self.assertIn("close", actions)

            # Test motion / hover
            hud.handle_motion(140, 120)
            hud.clear_hover()

            # Test motion / hover over copy and close buttons (Block 2)
            bg_items = cv.find_withtag("lens_hud_bg")
            self.assertEqual(len(bg_items), 2)

            hud.handle_motion(430, 120)  # copy
            self.assertEqual(hud._hovered_btn, "copy")
            hover_items = cv.find_withtag("lens_hud_hover")
            self.assertGreater(len(hover_items), 0)
            for bg_id in bg_items:
                self.assertGreater(hover_items[0], bg_id)

            hud.handle_motion(498, 120)  # close
            self.assertEqual(hud._hovered_btn, "close")
            hover_items = cv.find_withtag("lens_hud_hover")
            self.assertGreater(len(hover_items), 0)
            for bg_id in bg_items:
                self.assertGreater(hover_items[0], bg_id)

            hud.clear_hover()
            self.assertEqual(len(cv.find_withtag("lens_hud_hover")), 0)

            # Test hide resets active_mode
            hud.set_active_mode("ai")
            hud.hide()
            self.assertFalse(hud.is_visible)
            self.assertIsNone(hud.active_mode)
        finally:
            root.destroy()

    def test_rounded_card_controller_dynamic_height_prevents_clipping(self):
        import tkinter as tk
        from ui.floating_hud import RoundedCardController, CapsuleButton
        root = tk.Tk()
        try:
            cv = tk.Canvas(root, width=800, height=600, bg="#101010")
            cv.pack()
            box = (100, 100, 400, 300)
            controller = RoundedCardController(
                canvas=cv,
                box=box,
                hud_pos=(100, 314, 484, 58),
                screen_size=(1920, 1080),
                min_w=480,
                pad_x=18,
                pad_y=14,
            )
            # Verify controller initializes at calculated placement, NOT (0, 0), and in hidden state
            self.assertNotEqual((controller.cx, controller.cy), (0, 0))
            self.assertEqual(cv.itemcget(controller.win_id, "state"), "hidden")

            cf = controller.content_frame

            # Pack title
            title = tk.Label(cf, text="Перевод Google Lens", font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#222428")
            title.pack(anchor=tk.W)

            # Pack text with multiple lines
            txt = tk.Text(cf, height=6, width=50)
            txt.pack(fill=tk.X, pady=8)
            txt.insert(tk.END, "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6\n")

            # Pack button row at bottom
            btn_box = tk.Frame(cf, bg="#222428")
            btn_box.pack(fill=tk.X)
            b1 = CapsuleButton(btn_box, text="Скопировать", style="primary")
            b1.pack(side=tk.LEFT)
            b2 = CapsuleButton(btn_box, text="Закрыть", style="danger")
            b2.pack(side=tk.RIGHT)

            controller.update_geometry()
            root.update()

            # Verify revealed with normal state at final placement
            self.assertEqual(cv.itemcget(controller.win_id, "state"), "normal")

            # Controller card_h must accommodate all children plus padding without clipping
            req_h = cf.winfo_reqheight()
            self.assertEqual(controller.card_h, req_h + 2 * controller.pad_y)
            self.assertGreater(controller.card_h, 150)

            # Verify window configuration on canvas matches required height
            win_h = cv.itemcget(controller.win_id, "height")
            self.assertEqual(int(float(win_h)), req_h)

            controller.destroy()
        finally:
            root.destroy()

    def test_calc_pill_width_compact(self):
        from ui.floating_hud import calc_pill_width
        w_copy = calc_pill_width("Скопировать")
        w_close = calc_pill_width("Закрыть")
        w_ask = calc_pill_width("Спросить")

        self.assertGreaterEqual(w_copy, 76)
        self.assertGreaterEqual(w_close, 76)
        self.assertLess(w_copy, 120)  # Much more compact than old 160px button
        self.assertLess(w_close, 90)
        self.assertGreater(w_copy, w_close)

    def test_get_card_placement_no_overlap(self):
        from ui.floating_hud import get_card_placement
        box = (500, 200, 900, 500)
        hud_x, hud_y, hud_w, hud_h = 500, 514, 484, 58
        card_w, card_h = 480, 220
        cx, cy = get_card_placement(box, card_w, card_h, hud_x, hud_y, hud_w, hud_h, screen_w=1920, screen_h=1080)
        # Must be below HUD with gap (no overlap)
        self.assertGreaterEqual(cy, hud_y + hud_h + 14)

        # When near bottom screen, card must be placed above selection (no overlap)
        box_near_bottom = (500, 700, 900, 940)
        cx2, cy2 = get_card_placement(box_near_bottom, card_w, card_h, 500, 954, hud_w, hud_h, screen_w=1920, screen_h=1080)
        self.assertLessEqual(cy2 + card_h, box_near_bottom[1])

    def test_capsule_button_item_click_binding(self):
        from ui.floating_hud import CapsuleButton
        root = tk.Tk()
        try:
            clicked = []
            btn = CapsuleButton(root, text="Спросить", command=lambda: clicked.append(True))
            btn.pack()
            root.update()

            # Test direct canvas click
            btn._on_click(None)
            self.assertEqual(len(clicked), 1)

            # Test item tags have bindings
            self.assertIn("<Button-1>", btn.tag_bind(btn.txt_id))
            self.assertIn("<Button-1>", btn.tag_bind(btn.img_id))
        finally:
            root.destroy()

    def test_ai_prompt_submission_on_ui_thread(self):
        """Verifies prompt is retrieved synchronously without background thread deadlock."""
        root = tk.Tk()
        try:
            ent = tk.Entry(root)
            ent.insert(0, "Объясни эту ошибку")
            ent.pack()
            txt_res = tk.Text(root, height=4)
            txt_res.pack()

            events_dispatched = []

            def on_ask():
                p = ent.get().strip()
                events_dispatched.append(f"prompt:{p}")
                txt_res.delete("1.0", tk.END)
                txt_res.insert(tk.END, "Запрос к Gemini Vision...\n")

            on_ask()
            self.assertIn("prompt:Объясни эту ошибку", events_dispatched)
            self.assertIn("Запрос к Gemini Vision...", txt_res.get("1.0", tk.END))
        finally:
            root.destroy()

    def test_ai_worker_exception_display_without_closure_leak(self):
        """Verifies that an exception in background worker successfully displays in txt_res without closure NameError."""
        import queue
        root = tk.Tk()
        try:
            txt_res = tk.Text(root, height=4)
            txt_res.pack()
            txt_res.insert(tk.END, "Запрос к Gemini Vision...\n")

            ui_queue = queue.Queue()
            def worker():
                try:
                    raise RuntimeError("Тестовая ошибка Gemini API (400)")
                except Exception as exc:
                    err_msg = str(exc)
                    def show_err(msg=err_msg):
                        txt_res.delete("1.0", tk.END)
                        txt_res.insert(tk.END, f"Ошибка: {msg}")
                    ui_queue.put(show_err)

            worker()
            # Drain queue on main thread
            while not ui_queue.empty():
                fn = ui_queue.get_nowait()
                fn()

            content = txt_res.get("1.0", tk.END)
            self.assertIn("Ошибка: Тестовая ошибка Gemini API (400)", content)
            self.assertNotIn("Запрос к Gemini Vision", content)
        finally:
            root.destroy()

    def test_ocr_result_lines_structure(self):
        """Verifies that OCRResult accepts and exposes structured lines tuple."""
        from core.models import OCRResult
        res = OCRResult(
            raw_text="hello world",
            cleaned_text="hello world",
            image_path=Path("dummy.png"),
            lines=({"text": "hello world", "words": [{"text": "hello"}, {"text": "world"}]},),
        )
        self.assertEqual(len(res.lines), 1)
        self.assertEqual(res.lines[0]["text"], "hello world")
        self.assertFalse(res.is_empty)

    def test_merge_dual_language_structured_preserves_words_and_boxes(self):
        """Verifies that _merge_dual_language_structured outputs both text and structured line/word boxes."""
        from core.ocr import WindowsMediaOCREngine
        engine = WindowsMediaOCREngine()

        res_ru = {
            "lines": [
                {
                    "text": "тест коммита",
                    "words": [
                        {"text": "тест", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 40.0, "height": 14.0}},
                        {"text": "коммита", "bounding_rect": {"x": 60.0, "y": 20.0, "width": 60.0, "height": 14.0}},
                    ],
                }
            ]
        }
        res_en = {
            "lines": [
                {
                    "text": "test commit",
                    "words": [
                        {"text": "test", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 30.0, "height": 14.0}},
                        {"text": "commit", "bounding_rect": {"x": 60.0, "y": 20.0, "width": 50.0, "height": 14.0}},
                    ],
                }
            ]
        }

        text, lines = engine._merge_dual_language_structured(res_ru, res_en)
        self.assertIn("тест", text)
        self.assertIn("коммита", text)
        self.assertEqual(len(lines), 1)
        self.assertIn("bounding_rect", lines[0])
        words = lines[0]["words"]
        self.assertEqual(len(words), 2)
        self.assertEqual(words[0]["text"], "тест")
        self.assertEqual(words[0]["bounding_rect"]["x"], 10.0)

    def test_coordinate_transformation_and_dpi_scaling(self):
        """Verifies that SelectableTextLayer converts OCR crop-relative coordinates to Canvas screen coordinates accurately."""
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=800, height=600)
            layer = SelectableTextLayer(canvas=canvas, root=root)

            # OCR result relative to crop image (0, 0)
            ocr_res = OCRResult(
                raw_text="Commit 1a2b3c",
                cleaned_text="Commit 1a2b3c",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Commit 1a2b3c",
                        "bounding_rect": {"x": 10.0, "y": 15.0, "width": 120.0, "height": 20.0},
                        "words": [
                            {"text": "Commit", "bounding_rect": {"x": 10.0, "y": 15.0, "width": 50.0, "height": 20.0}},
                            {"text": "1a2b3c", "bounding_rect": {"x": 70.0, "y": 15.0, "width": 60.0, "height": 20.0}},
                        ],
                    },
                ),
            )

            # Test across various selection offsets and DPI scales
            for scale_name, (bx1, by1, bx2, by2) in [
                ("100% scale", (50, 100, 350, 300)),
                ("125% scale", (100, 150, 450, 400)),
                ("150% scale", (150, 200, 550, 500)),
                ("200% scale", (200, 300, 800, 700)),
            ]:
                count = layer.load_from_ocr(ocr_res, (bx1, by1, bx2, by2))
                self.assertEqual(count, 2, f"Failed for {scale_name}")

                # First word "Commit": offset is bx1 + 10, by1 + 15
                w1 = layer.words[0]
                self.assertEqual(w1.text, "Commit")
                self.assertEqual(w1.x1, bx1 + 10.0)
                self.assertEqual(w1.y1, by1 + 15.0)
                self.assertEqual(w1.x2, bx1 + 10.0 + 50.0)
                self.assertEqual(w1.y2, by1 + 15.0 + 20.0)

                # Second word "1a2b3c": offset is bx1 + 70, by1 + 15
                w2 = layer.words[1]
                self.assertEqual(w2.text, "1a2b3c")
                self.assertEqual(w2.x1, bx1 + 70.0)
                self.assertEqual(w2.y1, by1 + 15.0)
        finally:
            root.destroy()

    def test_selectable_text_layer_selection_mechanics_and_copy(self):
        """Verifies word range, multi-line selection, and copy behavior in SelectableTextLayer."""
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=800, height=600)
            copied_values = []
            layer = SelectableTextLayer(
                canvas=canvas,
                root=root,
                on_copy_callback=lambda txt: copied_values.append(txt),
            )

            ocr_res = OCRResult(
                raw_text="Line One\nLine Two Extra",
                cleaned_text="Line One\nLine Two Extra",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Line One",
                        "words": [
                            {"text": "Line", "bounding_rect": {"x": 10.0, "y": 10.0, "width": 30.0, "height": 15.0}},
                            {"text": "One", "bounding_rect": {"x": 50.0, "y": 10.0, "width": 25.0, "height": 15.0}},
                        ],
                    },
                    {
                        "text": "Line Two Extra",
                        "words": [
                            {"text": "Line", "bounding_rect": {"x": 10.0, "y": 35.0, "width": 30.0, "height": 15.0}},
                            {"text": "Two", "bounding_rect": {"x": 50.0, "y": 35.0, "width": 25.0, "height": 15.0}},
                            {"text": "Extra", "bounding_rect": {"x": 85.0, "y": 35.0, "width": 35.0, "height": 15.0}},
                        ],
                    },
                ),
            )

            layer.load_from_ocr(ocr_res, (0, 0, 400, 200))
            self.assertEqual(len(layer.words), 5)

            # 1. Single word selection
            layer.select_range(0, 0)
            self.assertEqual(layer.get_selected_text(), "Line")

            # 2. Phrase selection on same line
            layer.select_range(0, 1)
            self.assertEqual(layer.get_selected_text(), "Line One")

            # 3. Multi-line selection spanning line 1 and line 2
            layer.select_range(1, 3)  # "One" on line 0, "Line Two" on line 1
            self.assertEqual(layer.get_selected_text(), "One\nLine Two")

            # 4. Copy selected fragment via execute_copy()
            res = layer.execute_copy()
            self.assertEqual(res, "One\nLine Two")
            self.assertEqual(copied_values[-1], "One\nLine Two")

            # 5. When nothing is selected: execute_copy() copies ALL text
            layer.clear_selection()
            self.assertEqual(layer.get_selected_text(), "")
            res_all = layer.execute_copy()
            self.assertEqual(res_all, "Line One\nLine Two Extra")
            self.assertEqual(copied_values[-1], "Line One\nLine Two Extra")

            # 6. Hit testing
            # Word 0: (10, 10) to (40, 25)
            self.assertIsNotNone(layer.hit_test_word(20, 15))
            self.assertEqual(layer.hit_test_word(20, 15).text, "Line")
            self.assertIsNone(layer.hit_test_word(300, 300))
        finally:
            root.destroy()

    def test_dual_language_spatial_deduplication(self):
        """Verifies spatial IoU deduplication removes duplicate words across RU and EN passes without deleting distinct words."""
        from core.ocr import WindowsMediaOCREngine
        engine = WindowsMediaOCREngine()

        # Both passes recognized the commit hash at the exact same location,
        # but RU pass recognized Russian description while EN pass missed or mangled it
        res_ru = {
            "lines": [
                {
                    "text": "f4a1b2c Исправление ошибки",
                    "words": [
                        {"text": "f4a1b2c", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 55.0, "height": 16.0}},
                        {"text": "Исправление", "bounding_rect": {"x": 75.0, "y": 20.0, "width": 90.0, "height": 16.0}},
                        {"text": "ошибки", "bounding_rect": {"x": 170.0, "y": 20.0, "width": 55.0, "height": 16.0}},
                    ],
                }
            ]
        }
        res_en = {
            "lines": [
                {
                    "text": "f4a1b2c Иcпpaвлeниe oшибки feature_branch",
                    "words": [
                        # Overlapping commit hash
                        {"text": "f4a1b2c", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 55.0, "height": 16.0}},
                        # Homoglyph-mangled Cyrillic with high IoU
                        {"text": "Иcпpaвлeниe", "bounding_rect": {"x": 75.0, "y": 20.0, "width": 90.0, "height": 16.0}},
                        {"text": "oшибки", "bounding_rect": {"x": 170.0, "y": 20.0, "width": 55.0, "height": 16.0}},
                        # Distinct English token at separate location
                        {"text": "feature_branch", "bounding_rect": {"x": 235.0, "y": 20.0, "width": 100.0, "height": 16.0}},
                    ],
                }
            ]
        }

        merged_text, lines = engine._merge_dual_language_structured(res_ru, res_en)
        # Deduplication must eliminate duplicate hash and duplicate words
        words = lines[0]["words"]
        word_texts = [w["text"] for w in words]
        self.assertEqual(word_texts.count("f4a1b2c"), 1)
        self.assertIn("Исправление", word_texts)
        self.assertIn("ошибки", word_texts)
        self.assertIn("feature_branch", word_texts)
        self.assertEqual(len(words), 4)

    def test_cohesive_segments_grouping_and_gap_splitting(self):
        """Verifies adjacent words form cohesive segments, while large horizontal column gaps form separate segments."""
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=800, height=600)
            layer = SelectableTextLayer(canvas=canvas, root=root)

            # Line with a hash on left, large 150px gap, and message on right
            ocr_res = OCRResult(
                raw_text="3d8e9f    Fix user profile",
                cleaned_text="3d8e9f    Fix user profile",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "3d8e9f Fix user profile",
                        "words": [
                            {"text": "3d8e9f", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 60.0, "height": 16.0}},
                            {"text": "Fix", "bounding_rect": {"x": 220.0, "y": 20.0, "width": 30.0, "height": 16.0}},
                            {"text": "user", "bounding_rect": {"x": 255.0, "y": 20.0, "width": 35.0, "height": 16.0}},
                            {"text": "profile", "bounding_rect": {"x": 295.0, "y": 20.0, "width": 50.0, "height": 16.0}},
                        ],
                    },
                ),
            )

            layer.load_from_ocr(ocr_res, (0, 0, 400, 200))
            # Should have split into 2 cohesive segments due to large gap between 3d8e9f (x2=70) and Fix (x1=220)
            self.assertEqual(len(layer.segments), 2)
            self.assertEqual([w.text for w in layer.segments[0].words], ["3d8e9f"])
            self.assertEqual([w.text for w in layer.segments[1].words], ["Fix", "user", "profile"])
        finally:
            root.destroy()

    def test_variant_a_single_line_cohesive_region(self):
        """1. Слова одной строки объединяются в единую область, включающую промежутки."""
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=600, height=300)
            layer = SelectableTextLayer(canvas=canvas, root=root)
            ocr_res = OCRResult(
                raw_text="Hello beautiful world",
                cleaned_text="Hello beautiful world",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Hello beautiful world",
                        "words": [
                            {"text": "Hello", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 40.0, "height": 16.0}},
                            {"text": "beautiful", "bounding_rect": {"x": 55.0, "y": 20.0, "width": 70.0, "height": 16.0}},
                            {"text": "world", "bounding_rect": {"x": 130.0, "y": 20.0, "width": 45.0, "height": 16.0}},
                        ],
                    },
                ),
            )
            layer.load_from_ocr(ocr_res, (0, 0, 400, 200))
            # Exactly 1 cohesive region for the 3 closely spaced words
            self.assertEqual(len(layer.regions), 1)
            reg = layer.regions[0]
            self.assertEqual(len(reg.words), 3)
            # Starts near first word (<= 10.0) and ends near last word (>= 175.0)
            self.assertLessEqual(reg.x1, 10.0)
            self.assertGreaterEqual(reg.x2, 175.0)
        finally:
            root.destroy()

    def test_variant_a_horizontal_gap_splits_regions(self):
        """2. Большой горизонтальный разрыв создаёт отдельные области (колонки / хеши)."""
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=600, height=300)
            layer = SelectableTextLayer(canvas=canvas, root=root)
            ocr_res = OCRResult(
                raw_text="a1b2c3d    Commit message description",
                cleaned_text="a1b2c3d    Commit message description",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "a1b2c3d Commit message description",
                        "words": [
                            {"text": "a1b2c3d", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 55.0, "height": 16.0}},
                            # Large gap of 115px between hash (x2=65) and text (x1=180)
                            {"text": "Commit", "bounding_rect": {"x": 180.0, "y": 20.0, "width": 50.0, "height": 16.0}},
                            {"text": "message", "bounding_rect": {"x": 235.0, "y": 20.0, "width": 60.0, "height": 16.0}},
                            {"text": "description", "bounding_rect": {"x": 300.0, "y": 20.0, "width": 80.0, "height": 16.0}},
                        ],
                    },
                ),
            )
            layer.load_from_ocr(ocr_res, (0, 0, 500, 200))
            self.assertEqual(len(layer.regions), 2)
            self.assertEqual([w.text for w in layer.regions[0].words], ["a1b2c3d"])
            self.assertEqual([w.text for w in layer.regions[1].words], ["Commit", "message", "description"])
        finally:
            root.destroy()

    def test_variant_a_stable_height_across_jittery_word_boxes(self):
        """3. Высота областей остаётся стабильной при разных высотах OCR-рамок отдельных слов."""
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=600, height=300)
            layer = SelectableTextLayer(canvas=canvas, root=root)
            ocr_res = OCRResult(
                raw_text="Word1 Word2 Word3",
                cleaned_text="Word1 Word2 Word3",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Word1 Word2 Word3",
                        "words": [
                            # Jittery heights and y positions from OCR
                            {"text": "Word1", "bounding_rect": {"x": 10.0, "y": 18.0, "width": 40.0, "height": 14.0}},
                            {"text": "Word2", "bounding_rect": {"x": 55.0, "y": 23.0, "width": 40.0, "height": 22.0}},
                            {"text": "Word3", "bounding_rect": {"x": 100.0, "y": 19.0, "width": 40.0, "height": 16.0}},
                        ],
                    },
                ),
            )
            layer.load_from_ocr(ocr_res, (0, 0, 400, 200))
            # All words on the line must have unified stable y1 and y2
            w_y1_set = {w.y1 for w in layer.words}
            w_y2_set = {w.y2 for w in layer.words}
            self.assertEqual(len(w_y1_set), 1, "Words have jittery y1")
            self.assertEqual(len(w_y2_set), 1, "Words have jittery y2")
            reg = layer.regions[0]
            self.assertEqual(reg.y1, list(w_y1_set)[0])
            self.assertEqual(reg.y2, list(w_y2_set)[0])
        finally:
            root.destroy()

    def test_variant_a_neighboring_lines_do_not_merge_or_overlap(self):
        """4. Соседние строки не объединяются и не пересекаются по вертикали."""
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=600, height=300)
            layer = SelectableTextLayer(canvas=canvas, root=root)
            ocr_res = OCRResult(
                raw_text="Top Line\nBottom Line",
                cleaned_text="Top Line\nBottom Line",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Top Line",
                        "words": [
                            {"text": "Top", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 30.0, "height": 18.0}},
                            {"text": "Line", "bounding_rect": {"x": 45.0, "y": 20.0, "width": 30.0, "height": 18.0}},
                        ],
                    },
                    {
                        "text": "Bottom Line",
                        "words": [
                            {"text": "Bottom", "bounding_rect": {"x": 10.0, "y": 42.0, "width": 45.0, "height": 18.0}},
                            {"text": "Line", "bounding_rect": {"x": 60.0, "y": 42.0, "width": 30.0, "height": 18.0}},
                        ],
                    },
                ),
            )
            layer.load_from_ocr(ocr_res, (0, 0, 400, 200))
            self.assertEqual(len(layer.regions), 2)
            reg_top, reg_bot = layer.regions[0], layer.regions[1]
            self.assertEqual(reg_top.line_index, 0)
            self.assertEqual(reg_bot.line_index, 1)
            # Must strictly not overlap vertically
            self.assertLess(reg_top.y2, reg_bot.y1)
        finally:
            root.destroy()

    def test_variant_a_copying_single_line_preserves_normal_spaces(self):
        """5. Копирование выделенной строки сохраняет нормальные пробелы без артефактов."""
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=600, height=300)
            layer = SelectableTextLayer(canvas=canvas, root=root)
            ocr_res = OCRResult(
                raw_text="git commit -m initial",
                cleaned_text="git commit -m initial",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "git commit -m initial",
                        "words": [
                            {"text": "git", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 20.0, "height": 15.0}},
                            {"text": "commit", "bounding_rect": {"x": 35.0, "y": 20.0, "width": 45.0, "height": 15.0}},
                            {"text": "-m", "bounding_rect": {"x": 85.0, "y": 20.0, "width": 20.0, "height": 15.0}},
                            {"text": "initial", "bounding_rect": {"x": 110.0, "y": 20.0, "width": 40.0, "height": 15.0}},
                        ],
                    },
                ),
            )
            layer.load_from_ocr(ocr_res, (0, 0, 400, 200))
            # Select words "commit -m"
            layer.select_range(1, 2)
            self.assertEqual(layer.get_selected_text(), "commit -m")

            # Select entire line
            layer.select_all()
            self.assertEqual(layer.get_selected_text(), "git commit -m initial")
        finally:
            root.destroy()

    def test_variant_a_copying_multiple_lines_preserves_order_and_newlines(self):
        """6. Копирование нескольких строк сохраняет порядок и переводы строк."""
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=600, height=300)
            layer = SelectableTextLayer(canvas=canvas, root=root)
            ocr_res = OCRResult(
                raw_text="First line text\nSecond line text\nThird line text",
                cleaned_text="First line text\nSecond line text\nThird line text",
                image_path=Path("dummy.png"),
                lines=(
                    {"text": "First line text", "words": [
                        {"text": "First", "bounding_rect": {"x": 10.0, "y": 10.0, "width": 30.0, "height": 15.0}},
                        {"text": "line", "bounding_rect": {"x": 45.0, "y": 10.0, "width": 25.0, "height": 15.0}},
                        {"text": "text", "bounding_rect": {"x": 75.0, "y": 10.0, "width": 25.0, "height": 15.0}},
                    ]},
                    {"text": "Second line text", "words": [
                        {"text": "Second", "bounding_rect": {"x": 10.0, "y": 35.0, "width": 40.0, "height": 15.0}},
                        {"text": "line", "bounding_rect": {"x": 55.0, "y": 35.0, "width": 25.0, "height": 15.0}},
                        {"text": "text", "bounding_rect": {"x": 85.0, "y": 35.0, "width": 25.0, "height": 15.0}},
                    ]},
                    {"text": "Third line text", "words": [
                        {"text": "Third", "bounding_rect": {"x": 10.0, "y": 60.0, "width": 35.0, "height": 15.0}},
                        {"text": "line", "bounding_rect": {"x": 50.0, "y": 60.0, "width": 25.0, "height": 15.0}},
                        {"text": "text", "bounding_rect": {"x": 80.0, "y": 60.0, "width": 25.0, "height": 15.0}},
                    ]},
                ),
            )
            layer.load_from_ocr(ocr_res, (0, 0, 400, 200))
            # Select words spanning line 1 and line 2 (words index 4 "line" to index 7 "line")
            layer.select_range(4, 7)
            expected = "line text\nThird line"
            self.assertEqual(layer.get_selected_text(), expected)
        finally:
            root.destroy()

    def test_variant_a_dual_language_duplicates_excluded(self):
        """7. Дубликаты RU/EN не попадают в результат при объединении проходов."""
        from core.ocr import WindowsMediaOCREngine
        engine = WindowsMediaOCREngine()

        res_ru = {
            "lines": [
                {
                    "text": "b8a9c0d Исправление бага",
                    "words": [
                        {"text": "b8a9c0d", "bounding_rect": {"x": 10.0, "y": 15.0, "width": 55.0, "height": 15.0}},
                        {"text": "Исправление", "bounding_rect": {"x": 70.0, "y": 15.0, "width": 80.0, "height": 15.0}},
                        {"text": "бага", "bounding_rect": {"x": 155.0, "y": 15.0, "width": 35.0, "height": 15.0}},
                    ],
                }
            ]
        }
        res_en = {
            "lines": [
                {
                    "text": "b8a9c0d Иcпpaвлeниe бaгa",
                    "words": [
                        {"text": "b8a9c0d", "bounding_rect": {"x": 10.0, "y": 15.0, "width": 55.0, "height": 15.0}},
                        {"text": "Иcпpaвлeниe", "bounding_rect": {"x": 70.0, "y": 15.0, "width": 80.0, "height": 15.0}},
                        {"text": "бaгa", "bounding_rect": {"x": 155.0, "y": 15.0, "width": 35.0, "height": 15.0}},
                    ],
                }
            ]
        }

        merged_text, structured_lines = engine._merge_dual_language_structured(res_ru, res_en)
        words = structured_lines[0]["words"]
        word_texts = [w["text"] for w in words]
        self.assertEqual(word_texts.count("b8a9c0d"), 1)
        self.assertEqual(len(words), 3)
        self.assertEqual(merged_text, "b8a9c0d Исправление бага")

    def test_variant_a_dpi_and_offset_alignment(self):
        """8. DPI и масштаб/смещение не сдвигают области относительно изображения на Canvas."""
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=800, height=600)
            layer = SelectableTextLayer(canvas=canvas, root=root)
            ocr_res = OCRResult(
                raw_text="Test",
                cleaned_text="Test",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Test",
                        "words": [{"text": "Test", "bounding_rect": {"x": 25.0, "y": 30.0, "width": 50.0, "height": 20.0}}],
                    },
                ),
            )
            # Box with offset (bx1=150, by1=220)
            box_offset = (150, 220, 500, 450)
            layer.load_from_ocr(ocr_res, box_offset)
            w = layer.words[0]
            self.assertEqual(w.x1, 150 + 25.0)
            self.assertEqual(w.x2, 150 + 75.0)
            self.assertEqual(w.y1, 220 + 30.0)
            # Hit-test exactly at offset coordinates (x in [175, 225], y in [250, 270])
            self.assertIsNotNone(layer.hit_test_word(180, 255))
            self.assertIsNone(layer.hit_test_word(25, 30))  # not at zero-origin
        finally:
            root.destroy()

    def test_variant_a_original_image_remains_unmodified(self):
        """9. Исходное изображение остаётся визуально неизменным при показе, выделении и копировании."""
        from PIL import Image
        import numpy as np
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        # Create original screenshot sample with arbitrary pixels
        orig_img = Image.new("RGB", (200, 100), color=(128, 64, 32))
        orig_arr_before = np.array(orig_img).copy()

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=400, height=300)
            copied = []
            layer = SelectableTextLayer(canvas=canvas, root=root, on_copy_callback=lambda t: copied.append(t))
            ocr_res = OCRResult(
                raw_text="Keep Original",
                cleaned_text="Keep Original",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Keep Original",
                        "words": [
                            {"text": "Keep", "bounding_rect": {"x": 20.0, "y": 20.0, "width": 40.0, "height": 16.0}},
                            {"text": "Original", "bounding_rect": {"x": 65.0, "y": 20.0, "width": 60.0, "height": 16.0}},
                        ],
                    },
                ),
            )
            cutout_id = canvas.create_image(0, 0)
            orig_photo = tk.PhotoImage(width=10, height=10)

            layer.load_from_ocr(
                ocr_res,
                (0, 0, 200, 100),
                crop_img=orig_img,
                cutout_item_id=cutout_id,
                original_crop_photo=orig_photo,
            )

            # 1. Activate layer
            layer.show()
            self.assertTrue(layer.is_active)
            # NO redrawn OCR text or opaque plates must exist
            self.assertEqual(len(canvas.find_withtag(SelectableTextLayer.TAG_UNDERLAY)), 0)
            self.assertEqual(len(canvas.find_withtag(SelectableTextLayer.TAG_WORD)), 0)

            # 2. Select text
            layer.select_all()
            # Highlights are translucent selection items, not modifying orig_img
            self.assertGreaterEqual(len(canvas.find_withtag(SelectableTextLayer.TAG_SELECTION)), 1)

            # 3. Copy text
            res = layer.execute_copy()
            self.assertEqual(res, "Keep Original")

            # 4. Deactivate
            layer.hide()
            self.assertFalse(layer.is_active)

            # Verify original image was never mutated
            orig_arr_after = np.array(orig_img)
            self.assertTrue(np.array_equal(orig_arr_before, orig_arr_after))
        finally:
            root.destroy()

    def test_variant_a_hud_buttons_and_functions_continue_working(self):
        """10. Остальные функции и кнопки HUD продолжают работать без конфликтов."""
        from ui.floating_hud import GoogleLensHUD
        root = tk.Tk()
        try:
            clicked_actions = []
            canvas = tk.Canvas(root, width=800, height=600)
            hud = GoogleLensHUD(
                parent=canvas,
                on_text=lambda: clicked_actions.append("text"),
                on_translate=lambda: clicked_actions.append("translate"),
                on_lens=lambda: clicked_actions.append("lens"),
                on_ai=lambda: clicked_actions.append("ai"),
                on_copy=lambda: clicked_actions.append("copy"),
                on_close=lambda: clicked_actions.append("close"),
            )
            hud.draw(100, 200)

            # Click translate button (relative x ~ 100 in block 1: 100 + 100 = 200)
            hud.handle_click(200, 225)
            self.assertIn("translate", clicked_actions)

            # Click text button (relative x ~ 35 in block 1: 100 + 35 = 135)
            hud.handle_click(135, 225)
            self.assertIn("text", clicked_actions)
        finally:
            root.destroy()



class TestTechnicalReportOCRAccuracy(unittest.TestCase):
    """Automated reproducible regression tests for technical report and code identifier OCR."""

    GROUND_TRUTH = (
        "Результат: Ran 68 tests in 6.862s — OK (все 68 тестов успешно пройдены).\n"
        "В том числе проверены 10 целевых сценариев:\n"
        "1. test_variant_a_single_line_cohesive_region — объединение слов строки в единую область с промежутками.\n"
        "2. test_variant_a_horizontal_gap_splits_regions — разбиение на независимые области при широком разрыве (колонки/хэши).\n"
        "3. test_variant_a_stable_height_across_jittery_word_boxes — стабильная высота при неравномерных OCR-рамках слов.\n"
        "4. test_variant_a_neighboring_lines_do_not_merge_or_overlap — отсутствие слияния и вертикального наложения соседних строк.\n"
        "5. test_variant_a_copying_single_line_preserves_normal_spaces — нормальные пробелы при копировании фрагмента строки.\n"
        "6. test_variant_a_copying_multiple_lines_preserves_order_and_newlines — порядок чтения и \\n при многострочном копировании.\n"
        "7. test_variant_a_dual_language_duplicates_excluded — отсутствие дубликатов при RU+EN распознавании.\n"
        "8. test_variant_a_dpi_and_offset_alignment — корректность координат при любых смещениях области на экране.\n"
        "9. test_variant_a_original_image_remains_unmodified — побайтовая неизменность исходного изображения скриншота при показе слоя, выделении и копировании.\n"
        "10. test_variant_a_hud_buttons_and_functions_continue_working — работоспособность остальных кнопок HUD (\"Перевод\", \"Текст\", \"Поиск\" и др.)."
    )

    EXPECTED_TESTS = [
        "test_variant_a_single_line_cohesive_region",
        "test_variant_a_horizontal_gap_splits_regions",
        "test_variant_a_stable_height_across_jittery_word_boxes",
        "test_variant_a_neighboring_lines_do_not_merge_or_overlap",
        "test_variant_a_copying_single_line_preserves_normal_spaces",
        "test_variant_a_copying_multiple_lines_preserves_order_and_newlines",
        "test_variant_a_dual_language_duplicates_excluded",
        "test_variant_a_dpi_and_offset_alignment",
        "test_variant_a_original_image_remains_unmodified",
        "test_variant_a_hud_buttons_and_functions_continue_working",
    ]

    @classmethod
    def setUpClass(cls):
        from core.ocr import WindowsMediaOCREngine
        cls.test_image_path = Path("tests/test_data/report_test_screenshot.png")
        if not cls.test_image_path.exists():
            raise unittest.SkipTest("Test image fixture not found")
        cls.engine = WindowsMediaOCREngine(language="rus+eng")
        cls.ocr_res = cls.engine.recognize(cls.test_image_path)
        cls.text = cls.ocr_res.cleaned_text

    @staticmethod
    def _levenshtein(s1: str, s2: str) -> int:
        if len(s1) < len(s2):
            return TestTechnicalReportOCRAccuracy._levenshtein(s2, s1)
        if len(s2) == 0:
            return len(s1)
        previous_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        return previous_row[-1]

    def test_all_10_test_identifiers_exact_match(self):
        """10 из 10 названий тестов должны точно присутствовать со всеми подчёркиваниями."""
        for expected in self.EXPECTED_TESTS:
            with self.subTest(test_name=expected):
                self.assertIn(
                    expected,
                    self.text,
                    f"Test identifier '{expected}' missing or corrupted in recognized text"
                )

    def test_cer_under_one_percent(self):
        """Character Error Rate (CER) должен быть значительно ниже 5% (цель < 1%)."""
        dist = self._levenshtein(self.text, self.GROUND_TRUTH)
        cer = dist / len(self.GROUND_TRUTH)
        self.assertLess(cer, 0.05, f"CER {cer*100:.2f}% exceeds 5% threshold")

    def test_technical_report_numbers_and_symbols(self):
        """Проверка сохранности специальных обозначений, регистра и цифр."""
        self.assertIn("68 tests", self.text)
        self.assertIn("6.862s", self.text)
        self.assertIn("OK", self.text)
        self.assertIn("10", self.text)
        self.assertIn("RU+EN", self.text)
        self.assertIn("\\n", self.text)
        self.assertIn("OCR-рамках", self.text)
        self.assertIn("HUD", self.text)



class TestNaturalAlphaSelectionAndOCRTableFixes(unittest.TestCase):
    """Tests verifying real alpha transparency, heightened selection, and table layout OCR fixes."""

    def test_natural_alpha_composite_selection_and_restore(self):
        """Verifies that selection uses real Pillow alpha compositing and restores original photo upon clearing."""
        from PIL import Image
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=500, height=300)
            layer = SelectableTextLayer(canvas=canvas, root=root)

            orig_img = Image.new("RGB", (300, 150), color=(30, 30, 30))
            orig_photo = tk.PhotoImage(width=10, height=10)
            cutout_id = canvas.create_image(50, 50, image=orig_photo)

            ocr_res = OCRResult(
                raw_text="Commit 1a2b3c",
                cleaned_text="Commit 1a2b3c",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Commit 1a2b3c",
                        "words": [
                            {"text": "Commit", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 50.0, "height": 18.0}},
                            {"text": "1a2b3c", "bounding_rect": {"x": 70.0, "y": 20.0, "width": 50.0, "height": 18.0}},
                        ],
                    },
                ),
            )
            layer.load_from_ocr(
                ocr_res,
                (50, 50, 350, 200),
                crop_img=orig_img,
                cutout_item_id=cutout_id,
                original_crop_photo=orig_photo,
            )
            layer.show()

            # Select first word
            layer.select_range(0, 0)
            self.assertIsNotNone(layer._active_crop_photo, "Alpha composited photo was not generated")

            # Selection rectangle should have hollow fill (fill="") rather than stipple
            sel_items = canvas.find_withtag(SelectableTextLayer.TAG_SELECTION)
            self.assertGreaterEqual(len(sel_items), 1)
            for item in sel_items:
                fill_val = canvas.itemcget(item, "fill")
                self.assertEqual(fill_val, "", "Canvas selection rectangle must be hollow when alpha composite is active")

            # Clearing selection should restore original photo
            layer.clear_selection()
            self.assertEqual(len(canvas.find_withtag(SelectableTextLayer.TAG_SELECTION)), 0)
        finally:
            root.destroy()

    def test_increased_selection_height_with_strict_separation(self):
        """Verifies that visual highlight height is expanded while adjacent lines remain strictly separated."""
        from PIL import Image
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=500, height=300)
            layer = SelectableTextLayer(canvas=canvas, root=root)

            ocr_res = OCRResult(
                raw_text="Line 1\nLine 2",
                cleaned_text="Line 1\nLine 2",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Line 1",
                        "words": [
                            {"text": "Line", "bounding_rect": {"x": 10.0, "y": 20.0, "width": 40.0, "height": 16.0}},
                            {"text": "1", "bounding_rect": {"x": 55.0, "y": 20.0, "width": 20.0, "height": 16.0}},
                        ],
                    },
                    {
                        "text": "Line 2",
                        "words": [
                            {"text": "Line", "bounding_rect": {"x": 10.0, "y": 44.0, "width": 40.0, "height": 16.0}},
                            {"text": "2", "bounding_rect": {"x": 55.0, "y": 44.0, "width": 20.0, "height": 16.0}},
                        ],
                    },
                ),
            )
            layer.load_from_ocr(ocr_res, (0, 0, 400, 200))
            layer.show()

            # Select only line 1
            layer.select_range(0, 1)
            sel_items = canvas.find_withtag(SelectableTextLayer.TAG_SELECTION)
            self.assertEqual(len(sel_items), 1)
            coords = canvas.coords(sel_items[0])
            highlight_h = coords[3] - coords[1]
            # Raw line height is 16.0; visual selection height must be noticeably larger
            self.assertGreater(highlight_h, 19.0, f"Expected heightened selection box, got height {highlight_h}")

            # Select both lines: adjacent highlights must not overlap vertically
            layer.select_all()
            sel_items_all = canvas.find_withtag(SelectableTextLayer.TAG_SELECTION)
            self.assertEqual(len(sel_items_all), 2)
            c1 = canvas.coords(sel_items_all[0])
            c2 = canvas.coords(sel_items_all[1])
            self.assertLess(c1[3], c2[1], "Adjacent visual selection highlights must not overlap vertically")
        finally:
            root.destroy()

    def test_in_place_translation_overlay_and_plates(self):
        """Verifies in-place translation overlay: underlay plates, typography, word selection, and copy."""
        from PIL import Image
        import numpy as np
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        # Create screenshot background (dark theme #202020)
        crop_pil = Image.new("RGB", (300, 150), color=(32, 32, 32))

        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=500, height=400)
            copied = []
            layer = SelectableTextLayer(canvas=canvas, root=root, on_copy_callback=lambda t: copied.append(t))

            ocr_res = OCRResult(
                raw_text="Hello World\nSecond Line",
                cleaned_text="Hello World\nSecond Line",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Hello World",
                        "words": [
                            {"text": "Hello", "bounding_rect": {"x": 20.0, "y": 20.0, "width": 50.0, "height": 18.0}},
                            {"text": "World", "bounding_rect": {"x": 75.0, "y": 20.0, "width": 50.0, "height": 18.0}},
                        ],
                    },
                    {
                        "text": "Second Line",
                        "words": [
                            {"text": "Second", "bounding_rect": {"x": 20.0, "y": 50.0, "width": 60.0, "height": 18.0}},
                            {"text": "Line", "bounding_rect": {"x": 85.0, "y": 50.0, "width": 45.0, "height": 18.0}},
                        ],
                    },
                ),
            )

            box = (50, 60, 350, 210)
            tr_text = "Привет Мир\nВторая Строка"

            cnt = layer.load_translation(ocr_res, tr_text, box, crop_img=crop_pil)
            self.assertEqual(cnt, 4, "Must produce 4 translated words")
            self.assertEqual(layer.mode, "translate")

            # 1. Activate translation overlay
            layer.show()
            self.assertTrue(layer.is_active)

            # In translate mode, underlay plates mask original text and crisp translated words are drawn
            underlays = canvas.find_withtag(SelectableTextLayer.TAG_UNDERLAY)
            words_elems = canvas.find_withtag(SelectableTextLayer.TAG_WORD)
            self.assertGreaterEqual(len(underlays), 2, "Must create underlay plates for translated lines")
            self.assertEqual(len(words_elems), 4, "Must render canvas text items for translated words")

            # Verify sampled background plate color matches dark background (32, 32, 32) -> '#202020'
            reg0 = layer.regions[0]
            self.assertEqual(reg0.hex_bg, "#202020")
            self.assertEqual(reg0.fg_color, "#ffffff", "Dark background must use high-contrast white text")

            # 2. Test word selection on translated text
            layer.select_range(0, 1)  # select "Привет Мир"
            sel_text = layer.get_selected_text()
            self.assertEqual(sel_text, "Привет Мир")

            # Selection rectangle is rendered
            sel_items = canvas.find_withtag(SelectableTextLayer.TAG_SELECTION)
            self.assertGreaterEqual(len(sel_items), 1)

            # 3. Test copy execution
            res = layer.execute_copy()
            self.assertEqual(res, "Привет Мир")
            self.assertIn("Привет Мир", copied)

            # 4. Select all and copy all
            layer.select_all()
            all_text = layer.get_all_text()
            self.assertEqual(all_text, "Привет Мир\nВторая Строка")
        finally:
            root.destroy()

    def test_in_place_translation_mode_toggle_and_caching(self):
        """Verifies instant toggling between text mode (Variant A untouched) and translate mode."""
        from PIL import Image
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        crop_pil = Image.new("RGB", (200, 100), color=(250, 250, 250))
        root = tk.Tk()
        try:
            canvas = tk.Canvas(root, width=400, height=300)
            layer = SelectableTextLayer(canvas=canvas, root=root)
            ocr_res = OCRResult(
                raw_text="Original OCR",
                cleaned_text="Original OCR",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Original OCR",
                        "words": [
                            {"text": "Original", "bounding_rect": {"x": 10.0, "y": 10.0, "width": 60.0, "height": 16.0}},
                            {"text": "OCR", "bounding_rect": {"x": 75.0, "y": 10.0, "width": 40.0, "height": 16.0}},
                        ],
                    },
                ),
            )
            box = (10, 10, 210, 110)

            # Mode 1: Translate mode
            layer.load_translation(ocr_res, "Исходный OCR", box, crop_img=crop_pil)
            layer.show()
            self.assertEqual(layer.mode, "translate")
            self.assertGreater(len(canvas.find_withtag(SelectableTextLayer.TAG_UNDERLAY)), 0)
            self.assertGreater(len(canvas.find_withtag(SelectableTextLayer.TAG_WORD)), 0)
            self.assertEqual(layer.get_all_text(), "Исходный OCR")

            # Mode 2: Switch to Text mode
            layer.load_from_ocr(ocr_res, box, crop_img=crop_pil)
            layer.show()
            self.assertEqual(layer.mode, "text")
            # In text mode, Variant A invariant: 0 underlays and 0 word text items
            self.assertEqual(len(canvas.find_withtag(SelectableTextLayer.TAG_UNDERLAY)), 0)
            self.assertEqual(len(canvas.find_withtag(SelectableTextLayer.TAG_WORD)), 0)
            self.assertEqual(layer.get_all_text(), "Original OCR")

            # Mode 3: Switch back to Translate mode
            layer.load_translation(ocr_res, "Исходный OCR", box, crop_img=crop_pil)
            layer.show()
            self.assertEqual(layer.mode, "translate")
            self.assertGreater(len(canvas.find_withtag(SelectableTextLayer.TAG_UNDERLAY)), 0)
            self.assertGreater(len(canvas.find_withtag(SelectableTextLayer.TAG_WORD)), 0)
            self.assertEqual(layer.get_all_text(), "Исходный OCR")
        finally:
            root.destroy()

    def test_table_markdown_percentage_and_test_names_corrections(self):
        """Verifies correction of OCR confusions in Markdown comparison tables (% and test identifiers)."""
        from core.ocr import _correct_ocr_confusions

        snippet = (
            "Character Error Rate (CER) 35.4196 (расстояние 466) 0.2396 (расстояние 3)\n"
            "Точные совпадения тестов 0/10 10 / 10 (10096)\n"
            "test variant a single_line cohesive_region n\n"
            "test variant a horizontal_gap_splits_regions ns\n"
            "test variant a stable_height_across_jittery_word_boxes\n"
            "test variant_a_neighboring_lines_do not_merge где_ог overlap\n"
            "test variant_a_copying_single_line_preserves погта1 spaces\n"
            "test_variant_a_copying_multiple_lines_preserv es_order_and_newlines\n"
            "test_variant_a_dual_language_duplicates_exclu ded\n"
        )
        cleaned = _correct_ocr_confusions(snippet)

        self.assertIn("35.41%", cleaned)
        self.assertIn("0.23%", cleaned)
        self.assertIn("100%", cleaned)
        self.assertIn("test_variant_a_single_line_cohesive_region", cleaned)
        self.assertIn("test_variant_a_horizontal_gap_splits_regions", cleaned)
        self.assertIn("test_variant_a_stable_height_across_jittery_word_boxes", cleaned)
        self.assertIn("test_variant_a_neighboring_lines_do_not_merge_or_overlap", cleaned)
        self.assertIn("test_variant_a_copying_single_line_preserves_normal_spaces", cleaned)
        self.assertIn("test_variant_a_copying_multiple_lines_preserves_order_and_newlines", cleaned)
        self.assertIn("test_variant_a_dual_language_duplicates_excluded", cleaned)

    def test_median_clustering_prevents_descender_drift(self):
        """Verifies that line clustering with median center prevents drift when lines have descenders."""
        from core.ocr import WindowsMediaOCREngine
        engine = WindowsMediaOCREngine()

        # Line 1 with a descender word ('y' at y=10..26)
        # Line 2 directly below at y=28..42
        res = {
            "lines": [
                {
                    "text": "Header column",
                    "words": [
                        {"text": "Header", "bounding_rect": {"x": 10.0, "y": 10.0, "width": 50.0, "height": 14.0}},
                        {"text": "column_descender_y", "bounding_rect": {"x": 65.0, "y": 10.0, "width": 80.0, "height": 18.0}},
                    ],
                },
                {
                    "text": "Row item text",
                    "words": [
                        {"text": "Row", "bounding_rect": {"x": 10.0, "y": 30.0, "width": 30.0, "height": 14.0}},
                        {"text": "item", "bounding_rect": {"x": 45.0, "y": 30.0, "width": 30.0, "height": 14.0}},
                        {"text": "text", "bounding_rect": {"x": 80.0, "y": 30.0, "width": 30.0, "height": 14.0}},
                    ],
                },
            ]
        }
        restructured = engine._restructure_lines_by_layout(res)
        out_lines = restructured.get("lines", [])
        self.assertEqual(len(out_lines), 2, "Line 2 was erroneously merged into Line 1")
        self.assertIn("Header", out_lines[0]["text"])
        self.assertIn("Row", out_lines[1]["text"])


class TestBenchmarkOCRAccuracyAndCoordinateFidelity(unittest.TestCase):
    """Regression test suite running the 6-category benchmark to prevent OCR regressions and ensure bbox fidelity."""

    @classmethod
    def setUpClass(cls):
        import json
        from core.ocr import WindowsMediaOCREngine
        cls.ds_path = Path("tests/benchmark/dataset.json")
        if not cls.ds_path.exists():
            raise unittest.SkipTest("Benchmark dataset not found")
        with open(cls.ds_path, "r", encoding="utf-8") as f:
            cls.dataset = json.load(f)
        cls.engine = WindowsMediaOCREngine(language="rus+eng")

    def test_benchmark_overall_cer_and_tech_accuracy(self):
        """Verifies overall CER is under 5.0% and tech accuracy exceeds 90%."""
        total_chars = 0
        total_dist = 0
        total_tech = 0
        passed_tech = 0

        for item in self.dataset:
            p = Path(item["image_path"])
            res = self.engine.recognize(p)
            gt = item["ground_truth"]
            dist = TestTechnicalReportOCRAccuracy._levenshtein(res.cleaned_text, gt)
            total_dist += dist
            total_chars += len(gt)

            for tid in item["tech_identifiers"]:
                total_tech += 1
                if tid in res.cleaned_text:
                    passed_tech += 1

        overall_cer = (total_dist / total_chars) * 100.0 if total_chars else 0.0
        tech_acc = (passed_tech / total_tech) * 100.0 if total_tech else 0.0

        self.assertLess(overall_cer, 5.0, f"Overall CER {overall_cer:.2f}% exceeds 5.0% limit")
        self.assertGreaterEqual(tech_acc, 90.0, f"Tech accuracy {tech_acc:.1f}% below 90.0% target")

    def test_word_bounding_boxes_strictly_bounded(self):
        """Verifies that all word bounding boxes are accurately scaled and bounded by original image dimensions."""
        from PIL import Image

        for item in self.dataset:
            p = Path(item["image_path"])
            with Image.open(p) as img:
                orig_w, orig_h = img.size

            res = self.engine.recognize(p)
            for line in res.lines:
                for w in line.get("words", []):
                    b = w.get("bounding_rect", {})
                    x = b.get("x", 0.0)
                    y = b.get("y", 0.0)
                    width = b.get("width", 0.0)
                    height = b.get("height", 0.0)

                    self.assertGreaterEqual(x, -0.5, f"Word {w['text']} x coordinate negative")
                    self.assertGreaterEqual(y, -0.5, f"Word {w['text']} y coordinate negative")
                    self.assertLessEqual(x + width, orig_w + 2.0, f"Word {w['text']} exceeds image width")
                    self.assertLessEqual(y + height, orig_h + 2.0, f"Word {w['text']} exceeds image height")


class TestTableGridOCRAccuracy(unittest.TestCase):
    """Verifies multi-column table OCR grid detection, cell separation, and token recovery."""

    @classmethod
    def setUpClass(cls):
        from core.ocr import WindowsMediaOCREngine
        cls.image_path = Path("tests/test_data/table_comparison_screenshot.png")
        if not cls.image_path.exists():
            raise unittest.SkipTest("Table screenshot fixture not found")
        cls.engine = WindowsMediaOCREngine(language="rus+eng")
        cls.ocr_res = cls.engine.recognize(cls.image_path)
        cls.text = cls.ocr_res.cleaned_text

    def test_table_column_separation_and_rows_count(self):
        """Verifies that table cells are separated into columns rather than horizontally merged across columns."""
        self.assertGreaterEqual(len(self.ocr_res.lines), 14, "Expected at least 14 rows recognized in table")
        # Header title
        self.assertIn("Таблица сравнения ДО и ПОСЛЕ", self.text)
        # Column headers
        self.assertIn("Параметр / Идентификатор", self.text)
        self.assertIn("ДО исправления (Baseline)", self.text)
        self.assertIn("ПОСЛЕ исправления", self.text)

    def test_table_metrics_and_fractions(self):
        """Verifies CER percentages, fractions, and test match counts in table."""
        self.assertIn("35.41%", self.text)
        self.assertIn("0.23%", self.text)
        self.assertIn("0 / 10", self.text)
        self.assertIn("10 / 10", self.text)
        self.assertIn("100%", self.text)

    def test_table_test_identifiers_unbroken(self):
        """Verifies that snake_case test names wrapped within table cells are correctly reconstructed."""
        expected_tests = [
            "test_variant_a_single_line_cohesive_region",
            "test_variant_a_horizontal_gap_splits_regions",
            "test_variant_a_stable_height_across_jittery_word_boxes",
            "test_variant_a_neighboring_lines_do_not_merge_or_overlap",
            "test_variant_a_copying_single_line_preserves_normal_spaces",
            "test_variant_a_copying_multiple_lines_preserves_order_and_newlines",
            "test_variant_a_dual_language_duplicates_excluded",
            "test_variant_a_dpi_and_offset_alignment",
            "test_variant_a_original_image_remains_unmodified",
            "test_variant_a_hud_buttons_and_functions_continue_working",
        ]
        for name in expected_tests:
            with self.subTest(test_name=name):
                self.assertIn(name, self.text, f"Identifier {name} missing or corrupted in recognized table text")

    def test_table_special_symbols_and_numbers(self):
        """Verifies isolated technical symbols, escaped chars, and list numbers in table."""
        self.assertIn("\\n", self.text)
        self.assertIn("RU+EN", self.text)
        self.assertIn("OK", self.text)
        self.assertIn("3.", self.text)
        self.assertIn("6.", self.text)
        self.assertIn("8.", self.text)

    def test_table_word_bounding_boxes_within_original_dimensions(self):
        """Verifies that all cell word coordinates are correctly mapped back to original screenshot bounds."""
        with Image.open(self.image_path) as img:
            orig_w, orig_h = img.size

        for line in self.ocr_res.lines:
            for w in line.get("words", []):
                b = w.get("bounding_rect", {})
                x = b.get("x", 0.0)
                y = b.get("y", 0.0)
                width = b.get("width", 0.0)
                height = b.get("height", 0.0)
                self.assertGreaterEqual(x, -0.5)
                self.assertGreaterEqual(y, -0.5)
                self.assertLessEqual(x + width, orig_w + 2.0)
                self.assertLessEqual(y + height, orig_h + 2.0)



class TestGeminiDeprecationAndClipboardPolicy(unittest.TestCase):
    """Verifies OCR homoglyph corrections for Gemini deprecation reports,
    ensures clipboard image auto-copy is disabled on crop/selection,
    and validates Ctrl+C copying in text and translation selection layers.
    """

    def test_gemini_deprecation_homoglyph_corrections(self):
        from core.ocr import _correct_ocr_confusions

        sample_raw = (
            "• Gemini 3.7 Flash deprecation: gemini-3.7-f1ash is deprecated and has Ьееп replaced by gemini-3.8-f1ash . "
            "It is по 1опде available to select in Google Al Studio, and API requests to gemini-3.7-f1ash are automatically "
            "routed to gemini-3.8-f1ash .\n"
            "Update your model string to gemini-3.8-f1ash and review the Gemini 3.8 Flash guide.\n"
            "• Gemini 3.5 Flash deprecation: gemini-3.5-f1ash is deprecated and has Ьееп replaced by gemini-3.6-f1ash . "
            "It is по longe available to select in Google Al Studio, and API requests to gemini-3.5-f1ash are automatically "
            "routed to gemini-3.6-f1ash .\n"
            "Update your model string to gemini-3.6-f1ash (ог gemini-3.8-f1ash ) and review the Gemini 3.6 Flash guide.\n"
            "• Deep Research Agent deep-research-pro-preview-12-2Ø25 deprecation: The deep-research-pro-preview-12-2Ø25 agent is "
            "deprecated and will be shut down оп October 23, 2026.\n"
            "Migrate your requests to опе of the newer Deep Research agent versions:\n"
            "о deep-research-preview-Ø4-2Ø26 : Designed for speed and efficiency, ideal to be streamed back to а client I-Jl.\n"
            "о deep-research-max-preview-Ø4-2Ø26 : Maximum comprehensiveness for automated context gathering and synthesis.\n"
            "то migrate, update the agent parameter in your interactions.create requests from deep-research-pro-preview-12-2Ø25 to "
            "deep-research-preview-Ø4-2Ø26 (or deep-resear"
        )

        corrected = _correct_ocr_confusions(sample_raw)

        # 1. f1ash -> flash
        self.assertNotIn("f1ash", corrected)
        self.assertIn("gemini-3.7-flash", corrected)
        self.assertIn("gemini-3.8-flash", corrected)
        self.assertIn("gemini-3.5-flash", corrected)
        self.assertIn("gemini-3.6-flash", corrected)

        # 2. Cyrillic homoglyphs in English text
        self.assertNotIn("Ьееп", corrected)
        self.assertIn("has been replaced by", corrected)
        self.assertNotIn("1опде", corrected)
        self.assertIn("no longer available", corrected)
        self.assertNotIn("ог", corrected)
        self.assertIn("(or gemini-3.8-flash )", corrected)
        self.assertNotIn("оп October", corrected)
        self.assertIn("on October", corrected)
        self.assertNotIn("опе", corrected)
        self.assertIn("one of the newer", corrected)
        self.assertNotIn("то migrate", corrected)
        self.assertTrue("To migrate" in corrected or "to migrate" in corrected)

        # 3. Google AI Studio and client UI
        self.assertNotIn("Google Al Studio", corrected)
        self.assertIn("Google AI Studio", corrected)
        self.assertNotIn("I-Jl", corrected)
        self.assertIn("client UI", corrected)

        # 4. Slashed zero / Ø in dates and bullet lists
        self.assertNotIn("2Ø25", corrected)
        self.assertIn("2025", corrected)
        self.assertNotIn("Ø4-2Ø26", corrected)
        self.assertIn("04-2026", corrected)
        self.assertIn("• deep-research-preview", corrected)

    def test_no_auto_image_copy_policy(self):
        """Verifies that crop_and_save_current_selection does NOT copy image to clipboard automatically,
        and that clipboard copying only occurs when action_copy is explicitly triggered.
        """
        import inspect
        from core import capture

        src = inspect.getsource(capture.ScreenAreaSelector)
        # 1. Verify crop_and_save_current_selection definition does NOT call copy_image_to_clipboard
        crop_save_idx = src.find("def crop_and_save_current_selection")
        self.assertNotEqual(crop_save_idx, -1)
        action_text_idx = src.find("def action_text", crop_save_idx)
        self.assertNotEqual(action_text_idx, -1)
        crop_fn_src = src[crop_save_idx:action_text_idx]

        self.assertNotIn("copy_image_to_clipboard", crop_fn_src,
                         "crop_and_save_current_selection must NEVER auto-copy to clipboard!")

        # 2. Verify action_copy DOES contain copy_image_to_clipboard
        act_copy_idx = src.find("def action_copy")
        self.assertNotEqual(act_copy_idx, -1)
        act_close_idx = src.find("def action_close", act_copy_idx)
        act_copy_src = src[act_copy_idx:act_close_idx]
        self.assertIn("copy_image_to_clipboard", act_copy_src,
                      "action_copy should copy image to clipboard when HUD copy button is pressed.")

    def test_selectable_text_layer_copy_and_ctrl_c(self):
        """Verifies that SelectableTextLayer execute_copy writes to clipboard and invokes callback,
        both for plain OCR text and translated text.
        """
        import tkinter as tk
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        root.withdraw()
        try:
            canvas = tk.Canvas(root, width=400, height=300)
            canvas.pack()

            copied_box = [None]
            def on_copy(val):
                copied_box[0] = val

            layer = SelectableTextLayer(root=root, canvas=canvas, on_copy_callback=on_copy)

            ocr_res = OCRResult(
                image_path="dummy.png",
                raw_text="Hello World\nLine Two",
                cleaned_text="Hello World\nLine Two",
                lines=[
                    {"text": "Hello World", "words": [
                        {"text": "Hello", "bounding_rect": {"x": 10.0, "y": 10.0, "width": 40.0, "height": 16.0}},
                        {"text": "World", "bounding_rect": {"x": 55.0, "y": 10.0, "width": 45.0, "height": 16.0}},
                    ]},
                    {"text": "Line Two", "words": [
                        {"text": "Line", "bounding_rect": {"x": 10.0, "y": 35.0, "width": 35.0, "height": 16.0}},
                        {"text": "Two", "bounding_rect": {"x": 50.0, "y": 35.0, "width": 30.0, "height": 16.0}},
                    ]},
                ]
            )

            # Test text mode copy
            layer.load_from_ocr(ocr_res, (0, 0, 200, 100))
            layer.show()
            self.assertTrue(layer.is_active)

            # Copy all (when nothing selected)
            res = layer.execute_copy()
            self.assertEqual(res, "Hello World\nLine Two")
            self.assertEqual(copied_box[0], "Hello World\nLine Two")

            # Select word index 0 ("Hello")
            layer.selected_indices = {0}
            res_sel = layer.execute_copy()
            self.assertEqual(res_sel, "Hello")
            self.assertEqual(copied_box[0], "Hello")

            # Test translate mode copy
            layer.load_translation(ocr_res, "Привет Мир\nСтрока Два", (0, 0, 200, 100))
            layer.show()
            self.assertTrue(layer.is_active)
            self.assertEqual(layer.mode, "translate")

            res_tr = layer.execute_copy()
            self.assertEqual(res_tr, "Привет Мир\nСтрока Два")
            self.assertEqual(copied_box[0], "Привет Мир\nСтрока Два")
        finally:
            root.destroy()

    def test_ocr_detected_boxes_rendering(self):
        """Verifies that subtle detected bounding frames are rendered on OCR detection
        and cleaned up on hide.
        """
        import tkinter as tk
        from PIL import Image
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        root.withdraw()
        try:
            canvas = tk.Canvas(root, width=500, height=400)
            canvas.pack()

            layer = SelectableTextLayer(root=root, canvas=canvas)
            test_img = Image.new("RGB", (300, 200), (250, 250, 250))
            cutout_id = canvas.create_image(50, 50, anchor=tk.NW)

            ocr_res = OCRResult(
                image_path="dummy.png",
                raw_text="Alpha Beta\nGamma Delta",
                cleaned_text="Alpha Beta\nGamma Delta",
                lines=[
                    {"text": "Alpha Beta", "words": [
                        {"text": "Alpha", "bounding_rect": {"x": 10.0, "y": 10.0, "width": 50.0, "height": 18.0}},
                        {"text": "Beta", "bounding_rect": {"x": 70.0, "y": 10.0, "width": 45.0, "height": 18.0}},
                    ]},
                    {"text": "Gamma Delta", "words": [
                        {"text": "Gamma", "bounding_rect": {"x": 10.0, "y": 45.0, "width": 55.0, "height": 18.0}},
                        {"text": "Delta", "bounding_rect": {"x": 75.0, "y": 45.0, "width": 50.0, "height": 18.0}},
                    ]},
                ]
            )

            layer.load_from_ocr(ocr_res, (50, 50, 350, 250), crop_img=test_img, cutout_item_id=cutout_id)
            layer.show()

            detected_items = canvas.find_withtag(SelectableTextLayer.TAG_DETECTED_BOX)
            self.assertGreaterEqual(len(detected_items), 2)
            self.assertIsNotNone(layer._detected_crop_photo)

            # Test deselect preserves detected photo
            layer.selected_indices = {0}
            layer.redraw_selection()
            self.assertIsNotNone(layer._active_crop_photo)

            layer.selected_indices.clear()
            layer.redraw_selection()

            # Test hide cleans up detected elements
            layer.hide()
            self.assertEqual(len(canvas.find_withtag(SelectableTextLayer.TAG_DETECTED_BOX)), 0)
            self.assertIsNone(layer._detected_crop_photo)
        finally:
            root.destroy()

    def test_translation_geometry_and_box_clamping(self):
        """Verifies that translated text repeats original layout geometry and
        strictly stays clamped within selection box boundaries without overflow.
        """
        import tkinter as tk
        from PIL import Image
        from ui.text_selection_layer import SelectableTextLayer
        from core.models import OCRResult

        root = tk.Tk()
        root.withdraw()
        try:
            canvas = tk.Canvas(root, width=600, height=400)
            canvas.pack()

            layer = SelectableTextLayer(root=root, canvas=canvas)
            box = (100, 100, 320, 220)  # Width: 220, Height: 120
            bx1, by1, bx2, by2 = box

            ocr_res = OCRResult(
                image_path="dummy.png",
                raw_text="Short English sentence\nAnother line of original text",
                cleaned_text="Short English sentence\nAnother line of original text",
                lines=[
                    {"text": "Short English sentence", "words": [
                        {"text": "Short", "bounding_rect": {"x": 10.0, "y": 15.0, "width": 40.0, "height": 18.0}},
                        {"text": "English", "bounding_rect": {"x": 55.0, "y": 15.0, "width": 50.0, "height": 18.0}},
                        {"text": "sentence", "bounding_rect": {"x": 110.0, "y": 15.0, "width": 60.0, "height": 18.0}},
                    ]},
                    {"text": "Another line of original text", "words": [
                        {"text": "Another", "bounding_rect": {"x": 10.0, "y": 50.0, "width": 55.0, "height": 18.0}},
                        {"text": "line", "bounding_rect": {"x": 70.0, "y": 50.0, "width": 30.0, "height": 18.0}},
                        {"text": "of", "bounding_rect": {"x": 105.0, "y": 50.0, "width": 15.0, "height": 18.0}},
                        {"text": "original", "bounding_rect": {"x": 125.0, "y": 50.0, "width": 45.0, "height": 18.0}},
                        {"text": "text", "bounding_rect": {"x": 175.0, "y": 50.0, "width": 30.0, "height": 18.0}},
                    ]},
                ]
            )

            # Very long translated text designed to test clamping limits
            long_tr = (
                "Очень длинное развернутое предложение на русском языке с подробным переводом\n"
                "Вторая строка с еще более длинным развернутым объяснением контекста фразы"
            )

            layer.load_translation(ocr_res, long_tr, box)
            layer.show()

            # 1. Verify words coordinates are clamped inside bx2 - 4
            for w in layer.words:
                self.assertGreaterEqual(w.x1, bx1)
                self.assertLessEqual(w.x2, bx2 - 2)

            # 2. Verify region coordinates are clamped inside bx2 - 2
            for reg in layer.regions:
                self.assertGreaterEqual(reg.x1, bx1)
                self.assertLessEqual(reg.x2, bx2 - 2)

            # 3. Verify underlay canvas rectangles stay strictly inside box boundaries
            underlay_ids = canvas.find_withtag(SelectableTextLayer.TAG_UNDERLAY)
            self.assertGreater(len(underlay_ids), 0)
            for u_id in underlay_ids:
                coords = canvas.coords(u_id)
                self.assertEqual(len(coords), 4)
                ux1, uy1, ux2, uy2 = coords
                self.assertGreaterEqual(ux1, bx1)
                self.assertLessEqual(ux2, bx2)
                self.assertGreaterEqual(uy1, by1)
                self.assertLessEqual(uy2, by2)
        finally:
            root.destroy()

    def test_card_placement_side_right_mode(self):
        """Verifies that get_card_placement with placement_mode='side_right' places card
        to the right when space allows, and clamps inside box when space is constrained.
        """
        from ui.floating_hud import get_card_placement

        box = (200, 200, 500, 450)
        card_w, card_h = 400, 250
        hud_bar = (300, 470, 150, 42)

        # Case 1: Ample space on the right (1920x1080)
        cx1, cy1 = get_card_placement(
            box=box,
            card_w=card_w,
            card_h=card_h,
            hud_x=hud_bar[0],
            hud_y=hud_bar[1],
            hud_w=hud_bar[2],
            hud_h=hud_bar[3],
            screen_w=1920,
            screen_h=1080,
            placement_mode="side_right",
        )
        # Should be placed to the right of box[2] = 500 with gap=14 -> 514
        self.assertEqual(cx1, 514)
        self.assertEqual(cy1, box[1])

        # Case 2: Constrained screen width (box near right edge)
        box_edge = (600, 200, 950, 500)
        cx2, cy2 = get_card_placement(
            box=box_edge,
            card_w=card_w,
            card_h=card_h,
            hud_x=hud_bar[0],
            hud_y=hud_bar[1],
            hud_w=hud_bar[2],
            hud_h=hud_bar[3],
            screen_w=1000,
            screen_h=800,
            placement_mode="side_right",
        )
        # Since 950 + 14 + 400 > 1000, placed inside selection frame
        self.assertLessEqual(cx2 + card_w, 1000)
        self.assertGreaterEqual(cx2, 0)

    def test_render_lens_corners_geometry(self):
        """Verifies that render_lens_corners generates crisp, anti-aliased 4-corner sprites."""
        from ui.floating_hud import render_lens_corners

        corners = render_lens_corners(size=22, radius=4, leg=14, thickness=2.4)
        self.assertEqual(set(corners.keys()), {"nw", "ne", "sw", "se"})
        for c_img in corners.values():
            self.assertEqual(c_img.size, (22, 22))
            self.assertEqual(c_img.mode, "RGBA")

        # Also verifies default call
        corners_def = render_lens_corners()
        self.assertEqual(set(corners_def.keys()), {"nw", "ne", "sw", "se"})
        for c_img in corners_def.values():
            self.assertEqual(c_img.size, (22, 22))

    def test_selectable_text_layer_marquee_reuse_and_rgba_cache(self):
        """Verifies that SelectableTextLayer caches base RGBA on box detection and reuses canvas marquee item during drag."""
        from ui.text_selection_layer import SelectableTextLayer
        from PIL import Image

        root = tk.Tk()
        root.withdraw()
        try:
            canvas = tk.Canvas(root, width=400, height=300)
            canvas.pack()
            layer = SelectableTextLayer(canvas=canvas, root=root)

            # Setup test crop image and canvas cutout item
            img = Image.new("RGB", (200, 200), (255, 255, 255))
            layer.crop_img = img
            photo = ImageTk.PhotoImage(img)
            layer.cutout_item_id = canvas.create_image(50, 50, image=photo, anchor="nw")

            # Load OCR result to trigger box detection rendering
            ocr_res = OCRResult(
                raw_text="Hello World",
                cleaned_text="Hello World",
                image_path=Path("dummy.png"),
                lines=(
                    {
                        "text": "Hello World",
                        "words": [
                            {"text": "Hello", "bounding_rect": {"x": 10.0, "y": 10.0, "width": 40.0, "height": 20.0}},
                            {"text": "World", "bounding_rect": {"x": 60.0, "y": 10.0, "width": 40.0, "height": 20.0}},
                        ],
                    },
                ),
            )
            layer.load_from_ocr(
                ocr_res,
                (50, 50, 250, 250),
                crop_img=img,
                cutout_item_id=layer.cutout_item_id,
            )
            layer.show()
            self.assertIsNotNone(layer._base_detected_rgba)
            self.assertEqual(layer._base_detected_rgba.mode, "RGBA")

            # Test marquee creation and reuse during drag
            self.assertIsNone(layer._marquee_id)
            layer.handle_press(50, 50)
            layer.handle_drag(70, 70)
            marquee_id_first = layer._marquee_id
            self.assertIsNotNone(marquee_id_first)

            # Next drag event must update coordinates on the existing canvas item rather than re-creating
            layer.handle_drag(90, 90)
            self.assertEqual(layer._marquee_id, marquee_id_first)

            # Mouse release cleans up marquee
            layer.handle_release(90, 90)
            self.assertIsNone(layer._marquee_id)
        finally:
            root.destroy()


if __name__ == "__main__":
    import time
    unittest.main()





