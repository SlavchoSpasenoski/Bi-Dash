"""Run with an isolated test database, as required by BaseTestCase."""

from unittest.mock import patch

from flask import g

from redash.models import Organization
from tests import BaseTestCase


class TestAiSettings(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.factory.create_admin()

    def post(self, path, data, user=None):
        # Most tests focus on permissions and persistence; CSRF is tested separately.
        with patch("redash.handlers.ai.csrf.protect"):
            return self.make_request("post", path, data=data, user=user or self.admin)

    def test_configuration_and_context_require_admin(self):
        for path in ("/api/settings/ai", "/api/settings/ai/context"):
            self.assertEqual(self.make_request("get", path).status_code, 403)
            self.assertEqual(self.post(path, {}, user=self.factory.user).status_code, 403)

    def test_context_persists_and_is_scoped_to_organization(self):
        path = "/api/settings/ai/context"
        response = self.post(path, {"context": "Revenue excludes cancelled orders."})
        self.assertEqual(response.status_code, 200)
        self.db.session.expire_all()
        self.assertEqual(self.make_request("get", path, user=self.admin).json["context"],
                         "Revenue excludes cancelled orders.")
        other = self.factory.create_org()
        admin = self.factory.create_admin(org=other)
        g.pop("org", None)
        response = self.make_request("get", path, user=admin, org=other)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["context"], "")

    def test_key_is_encrypted_and_not_returned(self):
        response = self.post("/api/settings/ai", {
            "provider": "compatible", "model": "test", "base_url": "https://example.test/v1", "api_key": "private-key",
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("private-key", response.get_data(as_text=True))
        self.assertTrue(response.json["profiles"]["compatible"]["has_api_key"])
        self.db.session.expire_all()
        stored = Organization.get_by_id(self.factory.org.id).settings["ai_configuration"]
        self.assertNotIn("private-key", str(stored))
        response = self.make_request("get", "/api/ai/chat")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("api_key", response.json)

    def test_chat_uses_saved_context_and_configuration(self):
        self.post("/api/settings/ai/context", {"context": "Use PostgreSQL."})
        self.post("/api/settings/ai", {
            "provider": "compatible", "model": "my-model", "base_url": "http://local-ai:1234/v1",
        })
        with patch("redash.handlers.ai.ask_assistant", return_value="SELECT 1;") as ask:
            response = self.post("/api/ai/chat", {"messages": [{"role": "user", "content": "help"}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ask.call_args.kwargs["context"], "Use PostgreSQL.")
        self.assertEqual(ask.call_args.kwargs["provider"], "compatible")
        self.assertEqual(ask.call_args.args[1], "my-model")

    def test_browser_settings_posts_require_csrf_even_when_global_check_disabled(self):
        response = self.make_request("post", "/api/settings/ai/context", data={"context": "x"}, user=self.admin)
        self.assertEqual(response.status_code, 400)
        self.assertIn("CSRF", response.get_data(as_text=True))

    def test_context_validation(self):
        for context in (None, [], "x" * 24001):
            self.assertEqual(self.post("/api/settings/ai/context", {"context": context}).status_code, 400)
