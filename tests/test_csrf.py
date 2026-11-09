from unittest.mock import patch

from tests import BaseTestCase


class TestCSRFEnforcement(BaseTestCase):
    def setUp(self):
        # CSRF enforcement is wired up when the app is created, and the test environment
        # turns it off by default.
        with patch("redash.settings.ENFORCE_CSRF", True):
            super().setUp()

    def test_rejects_post_without_token(self):
        rv = self.post_request("/login", data={"email": "a@example.com", "password": "x"}, org=self.factory.org)
        self.assertEqual(rv.status_code, 400)

    def test_exempt_blueprint_does_not_require_token(self):
        # SAML is disabled for the org, so the view redirects instead of failing CSRF validation.
        rv = self.post_request("/saml/callback", data={"SAMLResponse": "x"}, org=self.factory.org)
        self.assertEqual(rv.status_code, 302)

    def test_authenticated_browser_post_without_token_is_rejected(self):
        rv = self.make_request("post", "/api/dashboards", data={"name": "CSRF check"})
        self.assertEqual(rv.status_code, 400)
        self.assertIn("CSRF", rv.get_data(as_text=True))

    def test_authenticated_browser_post_with_token_succeeds(self):
        self.make_request("get", "/api/dashboards")
        token = self.client.get_cookie("csrf_token").value
        rv = self.client.post(f"/{self.factory.org.slug}/api/dashboards", json={"name": "CSRF check"},
                              headers={"X-CSRFToken": token})
        self.assertEqual(rv.status_code, 200)

    def test_api_key_post_does_not_require_browser_csrf_token(self):
        admin = self.factory.create_admin()
        client = self.app.test_client()
        rv = client.post(f"/{self.factory.org.slug}/api/dashboards", json={"name": "API key check"},
                         headers={"Authorization": "Key " + admin.api_key})
        self.assertEqual(rv.status_code, 200)
