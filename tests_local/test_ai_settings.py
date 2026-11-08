import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("ai_settings", Path(__file__).parents[1] / "redash/ai_settings.py")
preferences = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preferences)


class SettingsTests(unittest.TestCase):
    def configuration(self, provider="compatible", **overrides):
        return {"provider": provider, "model": "test-model", "base_url": "https://example.test/v1", **overrides}

    def test_profiles_preserve_keys_without_exposing_them(self):
        saved = preferences.update_configuration({}, self.configuration(api_key="private-key"), "server-secret")
        self.assertNotIn("private-key", json.dumps(saved))
        public = preferences.public_configuration(saved)
        self.assertTrue(public["profiles"]["compatible"]["has_api_key"])
        self.assertNotIn("encrypted_api_key", json.dumps(public))
        self.assertEqual(preferences.runtime_configuration(saved, "server-secret")["api_key"], "private-key")
        local = preferences.update_configuration(saved, self.configuration("ollama", base_url="http://ollama:11434"), "server-secret")
        self.assertEqual(preferences.runtime_configuration(local, "server-secret")["api_key"], "")
        restored = preferences.update_configuration(local, self.configuration(), "server-secret")
        self.assertEqual(preferences.runtime_configuration(restored, "server-secret")["api_key"], "private-key")

    def test_changing_host_or_removing_key_does_not_reuse_credentials(self):
        saved = preferences.update_configuration({}, self.configuration(api_key="private-key"), "secret")
        for updates in ({"base_url": "https://different.test/v1"}, {"clear_api_key": True}):
            with self.subTest(updates=updates):
                changed = preferences.update_configuration(saved, self.configuration(**updates), "secret")
                self.assertEqual(preferences.runtime_configuration(changed, "secret")["api_key"], "")

    def test_saved_settings_never_fall_back_to_environment_key(self):
        with patch.dict(os.environ, {"REDASH_AI_PROVIDER": "openai", "OPENAI_API_KEY": "environment-key"}):
            saved = preferences.update_configuration({}, self.configuration("openai", base_url="https://api.openai.com/v1"), "secret")
            self.assertFalse(preferences.runtime_configuration(saved, "secret")["enabled"])
            self.assertFalse(preferences.public_configuration(saved)["profiles"]["openai"]["has_api_key"])

    def test_configuration_validation(self):
        for updates in ({"provider": "unknown"}, {"provider": []}, {"provider": {}},
                        {"model": " "}, {"base_url": "file:///etc/passwd"},
                        {"base_url": "https://user:pass@example.test"}, {"base_url": "https://test/?key=secret"},
                        {"base_url": "https://test:invalid"}, {"api_key": "key\nheader"},
                        {"api_key": "key", "clear_api_key": True}, {"clear_api_key": "true"},
                        {"provider": "openai"}, {"provider": "anthropic"}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                preferences.update_configuration({}, self.configuration(**updates), "secret")

    def test_wrong_encryption_secret_has_actionable_error(self):
        saved = preferences.update_configuration({}, self.configuration(api_key="private-key"), "secret")
        with self.assertRaisesRegex(ValueError, "Save it again"):
            preferences.runtime_configuration(saved, "different-secret")

    def test_compatible_local_server_can_run_without_key(self):
        saved = preferences.update_configuration({}, self.configuration(base_url="http://localhost:1234/v1"), "secret")
        self.assertTrue(preferences.runtime_configuration(saved, "secret")["enabled"])


if __name__ == "__main__":
    unittest.main()
