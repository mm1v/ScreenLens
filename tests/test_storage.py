import unittest
import sqlite3
import tempfile
from pathlib import Path

from storage.db import DatabaseManager, AppSettings, protect_secret, unprotect_secret


class TestStorageSecurity(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db = DatabaseManager(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dpapi_protect_unprotect(self):
        raw_key = "dummy_test_api_key_placeholder_12345"
        encrypted = protect_secret(raw_key)
        self.assertNotEqual(encrypted, raw_key)
        self.assertTrue(encrypted.startswith("DPAPI:"))

        decrypted = unprotect_secret(encrypted)
        self.assertEqual(decrypted, raw_key)

    def test_database_stores_encrypted_key(self):
        raw_key = "dummy_test_api_key_placeholder_12345"
        settings = self.db.get_settings()
        settings.gemini_api_key = raw_key
        settings.gemini_model = "gemini-3.5-flash-lite"
        self.db.save_settings(settings)

        # 1. Verify get_settings returns decrypted key
        loaded = self.db.get_settings()
        self.assertEqual(loaded.gemini_api_key, raw_key)
        self.assertEqual(loaded.gemini_model, "gemini-3.5-flash-lite")

        # 2. Verify raw SQLite table stores DPAPI ciphertext, not plaintext
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", ("gemini_api_key",))
        raw_in_db = cur.fetchone()[0]
        conn.close()

        self.assertTrue(raw_in_db.startswith("DPAPI:"))
        self.assertNotIn(raw_key, raw_in_db)

    def test_database_dns_ai_setting(self):
        settings = self.db.get_settings()
        self.assertTrue(settings.gemini_use_dns_ai)
        settings.gemini_use_dns_ai = False
        self.db.save_settings(settings)
        loaded = self.db.get_settings()
        self.assertFalse(loaded.gemini_use_dns_ai)


if __name__ == "__main__":
    unittest.main()
