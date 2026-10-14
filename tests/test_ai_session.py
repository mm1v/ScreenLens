"""Tests for ScreenLens AI Mode: Session management, image passing, error handling,
history persistence, and AI UI controllers.
"""

import base64
import os
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from core.gemini import ChatMessage, GeminiChatSession, GeminiVisionClient
from ui.ai_panel import (
    AIExtendedChatWindow,
    AIPanelController,
    create_rounded_thumbnail,
)
from ui.floating_hud import RoundedCardController


class TestGeminiChatSession(unittest.TestCase):
    """Unit tests for GeminiChatSession state management, payload construction, and error recovery."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.img_path = Path(self.tmp_dir.name) / "test_capture.png"
        img = Image.new("RGB", (120, 80), color=(50, 100, 150))
        img.save(self.img_path, format="PNG")

        self.client = GeminiVisionClient(
            api_key="test_api_key_12345",
            model="gemini-3.5-flash-lite",
            use_dns_ai=False,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_session_initialization(self):
        session = self.client.create_chat_session(
            image_path=self.img_path,
            ocr_text="Recognized Text Line",
            session_id="custom_sess_01",
        )
        self.assertEqual(session.session_id, "custom_sess_01")
        self.assertEqual(session.image_path, self.img_path)
        self.assertEqual(session.ocr_text, "Recognized Text Line")
        self.assertEqual(len(session.history), 0)
        self.assertEqual(session.get_last_response(), "")

    def test_distinct_sessions_for_new_capture(self):
        sess1 = self.client.create_chat_session(self.img_path)
        sess2 = self.client.create_chat_session(self.img_path)
        self.assertNotEqual(sess1.session_id, sess2.session_id)

    def test_turn_0_payload_includes_image_and_ocr(self):
        session = self.client.create_chat_session(
            image_path=self.img_path,
            ocr_text="Important Error 404",
        )

        captured_payloads = []

        def mock_execute(payload, timeout=35.0):
            captured_payloads.append(payload)
            return "This is a detailed explanation of Error 404."

        self.client._execute_generate_content = mock_execute

        ans = session.send_message("Что здесь написано?")
        self.assertEqual(ans, "This is a detailed explanation of Error 404.")
        self.assertEqual(len(captured_payloads), 1)

        payload = captured_payloads[0]
        contents = payload["contents"]
        self.assertEqual(len(contents), 1)
        user_content = contents[0]
        self.assertEqual(user_content["role"], "user")

        parts = user_content["parts"]
        self.assertEqual(len(parts), 2)

        # Part 0: Text with OCR context and user prompt
        text_part = parts[0]["text"]
        self.assertIn("Important Error 404", text_part)
        self.assertIn("Что здесь написано?", text_part)

        # Part 1: Image base64 inline data
        img_part = parts[1]["inline_data"]
        self.assertEqual(img_part["mime_type"], "image/png")
        self.assertTrue(len(img_part["data"]) > 0)

        # History updated
        self.assertEqual(len(session.history), 2)
        self.assertEqual(session.history[0].role, "user")
        self.assertEqual(session.history[0].text, "Что здесь написано?")
        self.assertEqual(session.history[1].role, "model")
        self.assertEqual(session.history[1].text, "This is a detailed explanation of Error 404.")
        self.assertEqual(session.get_last_response(), "This is a detailed explanation of Error 404.")

    def test_multi_turn_follow_up_does_not_resend_image(self):
        session = self.client.create_chat_session(self.img_path)

        captured_payloads = []

        def mock_execute(payload, timeout=35.0):
            captured_payloads.append(payload)
            turn = len(captured_payloads)
            return f"Response for turn {turn}"

        self.client._execute_generate_content = mock_execute

        # Turn 1
        ans1 = session.send_message("Первый вопрос")
        self.assertEqual(ans1, "Response for turn 1")

        # Turn 2 (Follow-up)
        ans2 = session.send_message("А как это исправить в коде?")
        self.assertEqual(ans2, "Response for turn 2")

        self.assertEqual(len(captured_payloads), 2)
        turn2_payload = captured_payloads[1]
        contents = turn2_payload["contents"]

        # 3 turns in contents: user1 (with image), model1, user2 (text only)
        self.assertEqual(len(contents), 3)
        self.assertEqual(contents[0]["role"], "user")
        self.assertEqual(contents[1]["role"], "model")
        self.assertEqual(contents[2]["role"], "user")

        user2_parts = contents[2]["parts"]
        self.assertEqual(len(user2_parts), 1)
        self.assertEqual(user2_parts[0]["text"], "А как это исправить в коде?")
        # Crucial check: image inline data was NOT resent in follow-up turn
        self.assertNotIn("inline_data", user2_parts[0])

        # Verify history consistency
        self.assertEqual(len(session.history), 4)
        self.assertEqual(session.history[2].role, "user")
        self.assertEqual(session.history[2].text, "А как это исправить в коде?")
        self.assertEqual(session.history[3].role, "model")
        self.assertEqual(session.history[3].text, "Response for turn 2")
        self.assertEqual(session.get_last_response(), "Response for turn 2")

    def test_error_rollback_preserves_conversation_state(self):
        session = self.client.create_chat_session(self.img_path)

        # Successful turn 1
        self.client._execute_generate_content = lambda payload, timeout=35.0: "Ответ 1"
        session.send_message("Вопрос 1")
        self.assertEqual(len(session.history), 2)
        self.assertEqual(len(session._contents), 2)

        # Failed turn 2 (Network timeout)
        def fail_execute(payload, timeout=35.0):
            raise TimeoutError("Сбой соединения с Gemini API: Read timed out")

        self.client._execute_generate_content = fail_execute

        with self.assertRaises(TimeoutError):
            session.send_message("Вопрос 2 с ошибкой")

        # User turn 2 must have been cleanly popped so history & contents are not corrupted
        self.assertEqual(len(session.history), 2)
        self.assertEqual(len(session._contents), 2)
        self.assertEqual(session.get_last_response(), "Ответ 1")

        # Retry turn 2 successfully
        self.client._execute_generate_content = lambda payload, timeout=35.0: "Ответ 2 после повтора"
        ans_retry = session.send_message("Вопрос 2 повтор")
        self.assertEqual(ans_retry, "Ответ 2 после повтора")
        self.assertEqual(len(session.history), 4)
        self.assertEqual(len(session._contents), 4)
        self.assertEqual(session.get_last_response(), "Ответ 2 после повтора")

    def test_empty_prompt_raises_value_error(self):
        session = self.client.create_chat_session(self.img_path)
        with self.assertRaises(ValueError):
            session.send_message("   ")

    def test_unconfigured_client_raises_value_error(self):
        unconfigured = GeminiVisionClient(api_key="", use_dns_ai=False)
        self.assertFalse(unconfigured.is_configured)
        session = unconfigured.create_chat_session(self.img_path)
        with self.assertRaises(ValueError):
            session.send_message("Тест")


class TestAIUIComponents(unittest.TestCase):
    """Unit tests for AIPanelController and AIExtendedChatWindow UI workflows."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root:
            try:
                cls.root.destroy()
            except Exception:
                pass

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available")
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.img_path = Path(self.tmp_dir.name) / "test_thumb.png"
        img = Image.new("RGB", (160, 100), color=(100, 150, 200))
        img.save(self.img_path, format="PNG")

        self.client = GeminiVisionClient(api_key="valid_key_xyz", use_dns_ai=False)
        self.session = self.client.create_chat_session(self.img_path, ocr_text="Hello World")

    def tearDown(self):
        self.tmp_dir.cleanup()
        if self.root:
            for child in self.root.winfo_children():
                try:
                    child.destroy()
                except Exception:
                    pass
            try:
                self.root.update_idletasks()
                self.root.update()
            except Exception:
                pass

    def test_create_rounded_thumbnail(self):
        thumb = create_rounded_thumbnail(self.img_path, target_size=(46, 46), radius=6)
        self.assertIsNotNone(thumb)

        # Also supports PIL Image directly
        with Image.open(self.img_path) as pil_im:
            thumb2 = create_rounded_thumbnail(pil_im, target_size=(46, 46), radius=6)
            self.assertIsNotNone(thumb2)

        # Invalid path returns None safely without raising
        self.assertIsNone(create_rounded_thumbnail("non_existent_file.png"))

    def test_ai_panel_controller_build_and_quick_action(self):
        canvas = tk.Canvas(self.root, width=800, height=600)
        card_ctrl = RoundedCardController(
            canvas=canvas,
            box=(50, 50, 300, 250),
            hud_pos=(100, 270, 320, 50),
            screen_size=(800, 600),
            min_w=520,
        )

        opened_extended = [False]

        def on_open_ext(sess):
            opened_extended[0] = True

        panel = AIPanelController(
            controller=card_ctrl,
            session=self.session,
            on_open_extended=on_open_ext,
        )

        # Verify quick prompts chips exist
        self.assertEqual(len(panel.quick_buttons), 4)

        # Test quick action click
        with patch.object(self.session, "send_message", return_value="Объяснение снимка"):
            panel._on_quick_action("Объясни код")
            for _ in range(50):
                time.sleep(0.02)
                try:
                    while not panel._ui_queue.empty():
                        panel._ui_queue.get_nowait()()
                except Exception:
                    pass
                self.root.update_idletasks()
                self.root.update()
                if not panel.is_loading:
                    break

        self.assertFalse(panel.is_loading)
        resp_text = panel.txt_res.get("1.0", tk.END)
        self.assertIn("Объяснение снимка", resp_text)

        # Test extended chat button trigger
        panel._open_extended_chat()
        self.assertTrue(opened_extended[0])

        card_ctrl.destroy()

    def test_ai_panel_input_retention_on_error(self):
        canvas = tk.Canvas(self.root, width=800, height=600)
        card_ctrl = RoundedCardController(
            canvas=canvas,
            box=(50, 50, 300, 250),
            hud_pos=(100, 270, 320, 50),
            screen_size=(800, 600),
            min_w=520,
        )

        panel = AIPanelController(
            controller=card_ctrl,
            session=self.session,
        )

        panel.ent.delete(0, tk.END)
        user_val = "Уникальный вопрос пользователя, который нельзя потерять"
        panel.ent.insert(0, user_val)
        panel._placeholder_active = False

        with patch.object(self.session, "send_message", side_effect=RuntimeError("Connection dropped")):
            panel._submit_query()
            for _ in range(50):
                time.sleep(0.02)
                try:
                    while not panel._ui_queue.empty():
                        panel._ui_queue.get_nowait()()
                except Exception:
                    pass
                self.root.update_idletasks()
                self.root.update()
                if not panel.is_loading:
                    break

        self.assertFalse(panel.is_loading)
        # Verify user text was preserved
        self.assertEqual(panel.ent.get(), user_val)
        # Error is displayed in response box
        self.assertIn("Ошибка: Connection dropped", panel.txt_res.get("1.0", tk.END))

        card_ctrl.destroy()

    def test_extended_chat_window_preserves_and_continues_history(self):
        # Pre-populate session with turn 1
        self.session.history.append(ChatMessage(role="user", text="Первый вопрос"))
        self.session.history.append(ChatMessage(role="model", text="Первый ответ модели"))

        chat_win = AIExtendedChatWindow(
            parent=self.root,
            session=self.session,
        )

        try:
            self.root.update_idletasks()
            self.root.update()

            # Verify history is rendered in the scrollable view
            rendered_children = chat_win.scrollable_frame.winfo_children()
            self.assertEqual(len(rendered_children), 2)

            # Test suggestions chips
            self.assertTrue(len(chat_win.FOLLOW_UP_SUGGESTIONS) >= 4)

            # Test follow-up submission in extended chat
            with patch.object(self.session, "send_message", return_value="Второй ответ модели"):
                chat_win._send_suggestion("Объясни подробнее")
                for _ in range(25):
                    time.sleep(0.02)
                    try:
                        while not chat_win._ui_queue.empty():
                            chat_win._ui_queue.get_nowait()()
                    except Exception:
                        pass
                    self.root.update_idletasks()
                    self.root.update()
                    if not chat_win.is_loading:
                        break

            self.assertFalse(chat_win.is_loading)

            # Test transcript formatting
            copied_texts = []
            chat_win.clipboard_clear = lambda: None
            chat_win.clipboard_append = lambda t: copied_texts.append(t)
            chat_win.update = lambda: None
            chat_win._copy_full_dialog()
            self.assertTrue(len(copied_texts) > 0)
            self.assertIn("ScreenLens AI Диалог", copied_texts[0])
            self.assertIn("Первый вопрос", copied_texts[0])
            self.assertIn("Первый ответ модели", copied_texts[0])

        finally:
            chat_win.destroy()


if __name__ == "__main__":
    unittest.main()
