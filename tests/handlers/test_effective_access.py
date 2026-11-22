"""Admin explanations must match permissions without opening source connections."""
import os
from unittest.mock import patch
from sqlalchemy.engine import make_url
from redash import models
from redash.utils import utcnow
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class TestEffectiveAccess(BaseTestCase):
    def setUp(self):
        if not (make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unset")).database or "").startswith("bidash_access_app_test"):
            self.skipTest("Requires disposable metadata")
        super().setUp()
        self.admin = self.factory.create_admin()
        self.editor = self.factory.create_user(details={"role": "editor"})
        self.reader = self.factory.create_user(details={"role": "reader"})
        self.source = self.factory.data_source
        self.source.type = "mysql"
        self.source.options = ConfigurationContainer({"host": "never-connect.invalid", "db": "Sales", "user": "synthetic", "passwd": "DO_NOT_EXPOSE_SECRET"})
        self.board = self.factory.create_dashboard(user=self.admin, name="Assigned report", is_draft=False)
        self.query = self.factory.create_query(user=self.admin, data_source=self.source, query_text="SELECT id FROM orders", is_draft=False)
        vis = models.Visualization(query_rel=self.query, type="TABLE", name="Orders", options={})
        models.db.session.add(vis)
        models.db.session.flush()
        models.db.session.add(models.Widget(dashboard=self.board, visualization=vis, width=1, text="", options={"position": {"col": 0, "row": 0, "sizeX": 6, "sizeY": 3}}))
        models.db.session.add(models.DashboardReadGrant(dashboard_id=self.board.id, user_id=self.editor.id, grantor_id=self.admin.id))
        models.db.session.commit()

    def report(self, user=None, actor=None):
        return self.make_request("get", f"/api/users/{(user or self.editor).id}/effective-access", user=actor or self.admin)

    def source_row(self, report):
        return next(s for s in report["sources"] if s["id"] == self.source.id)

    def restrict(self, group_ids):
        self.factory.org.settings = {**self.factory.org.settings, "data_access_protected_sources": [self.source.id],
            "data_access_group_rules": {"revision": 1, "policies": [{"id": "rule", "name": "Order identifiers", "status": "active",
            "data_source_id": self.source.id, "group_ids": group_ids, "user_ids": [], "default_effect": "deny",
            "rules": [{"catalog": None, "schema": "Sales", "table": "orders", "column_mode": "explicit", "columns": ["id"]}]}]}}
        models.db.session.commit()

    def test_no_rules_source_access_and_dashboard_widgets_without_secrets_or_connection(self):
        with patch("redash.data_access_metadata.fetch_metadata", side_effect=AssertionError("No metadata connections")), patch("redash.query_runner.get_query_runner", side_effect=AssertionError("No source connections")):
            response = self.report()
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertTrue(self.source_row(data)["view"]["allowed"])
        self.assertEqual(self.source_row(data)["table_scope"], "all")
        board = next(d for d in data["dashboards"] if d["id"] == self.board.id)
        self.assertTrue(board["view"]["allowed"])
        self.assertFalse(board["edit"]["allowed"])
        self.assertTrue(board["widgets"][0]["allowed"])
        for secret in ("DO_NOT_EXPOSE_SECRET", "never-connect.invalid", self.editor.api_key, self.query.query_text):
            self.assertNotIn(secret, response.get_data(as_text=True))

    def test_editor_reader_and_foreign_target_are_denied(self):
        for actor in (self.editor, self.reader):
            self.assertEqual(self.report(actor=actor).status_code, 403)
        foreign = self.factory.create_user(org=self.factory.create_org())
        models.db.session.commit()
        self.assertEqual(self.report(user=foreign).status_code, 404)

    def test_no_matching_rule_explains_denial_and_hides_allowlist(self):
        group = self.factory.create_group()
        models.db.session.commit()
        self.restrict([group.id])
        data = self.report().json
        source = self.source_row(data)
        self.assertFalse(source["view"]["allowed"])
        self.assertEqual(source["view"]["reason"], "No group data access rules are assigned.")
        self.assertEqual(source["tables"], [])
        self.assertFalse(data["dashboards"][0]["widgets"][0]["allowed"])

    def test_matching_rule_returns_exact_columns_but_not_sql(self):
        self.restrict(self.editor.group_ids)
        source = self.source_row(self.report().json)
        self.assertTrue(source["view"]["allowed"])
        self.assertEqual(source["table_scope"], "allowlist")
        self.assertEqual(source["tables"][0]["columns"], ["id"])

    def test_source_membership_and_dashboard_grant_are_independent(self):
        models.DashboardReadGrant.query.delete()
        models.db.session.commit()
        data = self.report().json
        self.assertTrue(self.source_row(data)["view"]["allowed"])
        self.assertFalse(data["dashboards"][0]["view"]["allowed"])
        self.editor.group_ids = []
        models.db.session.commit()
        source = self.source_row(self.report().json)
        self.assertFalse(source["view"]["allowed"])
        self.assertIn("No source access", source["view"]["reason"])

    def test_disabled_user_cannot_inherit_allowed_permissions(self):
        self.editor.disabled_at = utcnow()
        models.db.session.commit()
        data = self.report().json
        self.assertTrue(all(not a["allowed"] for a in data["actions"]))
        self.assertFalse(self.source_row(data)["view"]["allowed"])
        self.assertFalse(data["dashboards"][0]["view"]["allowed"])

    def test_reader_scope_and_admin_nonowner_permissions(self):
        self.restrict(self.editor.group_ids)
        reader = self.report(user=self.reader).json
        self.assertEqual(self.source_row(reader)["table_scope"], "report_output")
        self.assertFalse(self.source_row(reader)["execute"]["allowed"])
        self.assertFalse(next(a for a in reader["actions"] if a["key"] == "ai")["allowed"])
        other_admin = self.factory.create_admin()
        models.db.session.commit()
        board = self.report(user=other_admin).json["dashboards"][0]
        self.assertTrue(board["view"]["allowed"])
        self.assertTrue(board["share"]["allowed"])
        self.assertTrue(board["edit"]["allowed"])
