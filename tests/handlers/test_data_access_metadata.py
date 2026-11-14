"""Admin metadata API checks: disposable bidash_access_app_test* database only."""
import os
from unittest.mock import patch
from urllib.parse import urlencode

from sqlalchemy.engine import make_url

from redash import models
from tests import BaseTestCase


class TestDataAccessMetadata(BaseTestCase):
    def setUp(self):
        if not (make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unconfigured")).database or "").startswith("bidash_access_app_test"):
            self.skipTest("Requires an explicitly disposable bidash_access_app_test metadata database")
        super().setUp()
        self.admin = self.factory.create_admin()
        self.editor = self.factory.create_user(details={"role": "editor"})
        self.reader = self.factory.create_user(details={"role": "reader"})
        self.source = self.factory.data_source
        models.db.session.commit()
        self.fetch_patch = patch("redash.data_access_metadata.fetch_metadata", return_value={"schemas": ["Sales"], "truncated": False})
        self.fetch = self.fetch_patch.start()
        self.addCleanup(self.fetch_patch.stop)

    def get_metadata(self, args=None, user=None):
        args = args if args is not None else {"data_source_id": self.source.id}
        return self.make_request("get", "/api/settings/data-access/metadata?" + urlencode(args, doseq=True), user=user or self.admin)

    def test_admin_reads_staged_exact_names(self):
        for args, result in (({}, {"schemas": ["Sales.Case", "Продажба"], "truncated": False}),
                             ({"schema": "Sales.Case"}, {"tables": ["Orders`Name"], "truncated": False}),
                             ({"schema": "Sales.Case", "table": "Orders`Name"}, {"columns": ["Secret ID"], "truncated": True})):
            with self.subTest(args=args):
                self.fetch.return_value = result
                response = self.get_metadata({"data_source_id": self.source.id, **args})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json, result)
                self.assertEqual(self.fetch.call_args.args[0].id, self.source.id)

    def test_reader_and_editor_cannot_discover_admin_metadata(self):
        for user in (self.reader, self.editor):
            with self.subTest(role=user.details["role"]):
                self.assertEqual(self.get_metadata(user=user).status_code, 403)
        self.fetch.assert_not_called()

    def test_foreign_org_and_missing_sources_do_not_reach_connector(self):
        foreign = self.factory.create_data_source(org=self.factory.create_org())
        for source_id in (foreign.id, 2147483647):
            with self.subTest(source=source_id):
                self.assertEqual(self.get_metadata({"data_source_id": source_id}).status_code, 404)
        self.fetch.assert_not_called()

    def test_private_profile_id_denied_even_to_admin(self):
        self.factory.org.settings = {**self.factory.org.settings, "data_access_internal_sources": [self.source.id]}
        models.db.session.commit()
        self.assertEqual(self.get_metadata().status_code, 403)
        self.fetch.assert_not_called()

    def test_unknown_duplicate_and_malformed_parameters_rejected(self):
        for args in ({}, {"data_source_id": "invalid"}, {"data_source_id": "0"},
                     {"data_source_id": self.source.id, "unexpected": "value"},
                     {"data_source_id": [self.source.id, self.source.id]},
                     {"data_source_id": self.source.id, "schema": ["Sales", "Other"]}):
            with self.subTest(args=args):
                self.assertEqual(self.get_metadata(args).status_code, 400)
        self.fetch.assert_not_called()

    def test_connector_errors_are_sanitized(self):
        from redash.data_access_metadata import MetadataUnavailable
        self.fetch.side_effect = MetadataUnavailable("synthetic-private-password@internal-host")
        response = self.get_metadata()
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("synthetic-private-password", response.get_data(as_text=True))
        self.assertNotIn("internal-host", response.get_data(as_text=True))

    def test_invalid_metadata_names_report_bad_request(self):
        self.fetch.side_effect = ValueError("Choose a schema before a table.")
        response = self.get_metadata({"data_source_id": self.source.id, "table": "Orders"})
        self.assertEqual(response.status_code, 400)
