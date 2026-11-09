from unittest.mock import patch

from werkzeug.datastructures import MultiDict

from redash.handlers.setup import SetupForm
from redash.models import User
from tests import BaseTestCase


class TestSetupForm(BaseTestCase):
    def _form(self, email):
        data = {"name": "Admin", "email": email, "password": "secret1", "org_name": "Org"}
        with self.app.test_request_context():
            form = SetupForm(MultiDict(data))
            return form.validate(), form.errors

    def test_valid_email(self):
        self.assertEqual((True, {}), self._form("admin@example.com"))

    def test_invalid_email(self):
        valid, errors = self._form("not-an-email")
        self.assertFalse(valid)
        self.assertIn("email", errors)


class TestSetupConsent(BaseTestCase):
    def setUp(self):
        super().setUp()
        # The regular factory seeds an organization; setup needs an empty install.
        self.db.session.remove()
        self.db.drop_all()
        self.db.create_all()

    def submit(self, **preferences):
        data = {"name": "Admin", "email": "admin@example.com", "password": "secret1", "org_name": "Org"}
        # The suite imports routes in multi-org mode; setup is single-org only.
        with (
            patch("redash.handlers.setup.settings.MULTI_ORG", False),
            patch("redash.handlers.setup.url_for", return_value="/"),
            patch("redash.handlers.setup.subscribe.delay") as subscribe,
        ):
            response = self.client.post("/setup", data={**data, **preferences})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.query.count(), 1)
        if subscribe.called:
            self.assertNotIn("password", subscribe.call_args.args[0])
        return subscribe

    def test_setup_does_not_subscribe_without_consent(self):
        self.submit().assert_not_called()

    def test_setup_respects_the_selected_subscription(self):
        subscribe = self.submit(security_notifications="y")
        subscribe.assert_called_once()
        self.assertTrue(subscribe.call_args.args[0]["security_notifications"])
        self.assertFalse(subscribe.call_args.args[0]["newsletter"])

    def test_setup_respects_newsletter_only(self):
        subscribe = self.submit(newsletter="y")
        subscribe.assert_called_once()
        self.assertTrue(subscribe.call_args.args[0]["newsletter"])
        self.assertFalse(subscribe.call_args.args[0]["security_notifications"])
