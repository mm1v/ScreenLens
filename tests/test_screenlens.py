import os
import tempfile
import time
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

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



class TestActions(unittest.TestCase):
    def test_translate_empty_text(self):
        from core.actions import translate_text
        self.assertEqual(translate_text(""), "")

    def test_translate_english_to_russian(self):
        from core.actions import translate_text
        res = translate_text("Hello World", target_lang="ru")
        self.assertTrue(len(res) > 0)
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


if __name__ == "__main__":
    import time
    unittest.main()


