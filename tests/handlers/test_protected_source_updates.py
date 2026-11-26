"""Admin credential rotation preserves protected source identity and revokes scopes."""
import copy
import os
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.engine import make_url

from redash import models, redis_connection
from redash.data_access_runtime import access_scope, ensure_execution, result_access_allowed
from redash.data_access_shared import validate_shared_source
from redash.tasks.general import test_connection as check_source_connection
from redash.tasks.queries.execution import QueryExecutor, QueryExecutionError
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class TestProtectedSourceUpdates(BaseTestCase):
    def setUp(self):
        if not (make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unset")).database or "").startswith("bidash_access_app_test"):
            self.skipTest("Requires explicitly disposable metadata database")
        super().setUp()
        self.admin = self.factory.create_admin()
        self.editor = self.factory.create_user(details={"role": "editor"})
        self.reader = self.factory.create_user(details={"role": "reader"})
        self.source = self.factory.data_source
        self.source.type = "mysql"
        self.source.options = ConfigurationContainer({"host": "fixture", "db": "sales", "user": "qa", "passwd": "old-secret"})
        org = self.factory.org
        org.settings["data_access_protected_sources"] = [self.source.id]
        org.settings["data_access_group_rules"] = {"revision": 1, "policies": [{
            "id": "sales", "name": "Sales", "data_source_id": self.source.id, "status": "active",
            "default_effect": "deny", "user_ids": [], "group_ids": [org.default_group.id], "rules": [{
                "catalog": None, "schema": "sales", "table": "orders", "column_mode": "explicit", "columns": ["id"]}]}]}
        models.db.session.commit()
        self.path = f"/api/data_sources/{self.source.id}"
        self.validation = patch("redash.data_access_shared.validate_shared_source", return_value={"select_only": True})
        self.validate = self.validation.start()
        self.addCleanup(self.validation.stop)

    def tearDown(self):
        if hasattr(self, "app_ctx"):
            super().tearDown()

    def update(self, user=None, **overrides):
        data = {"name": "Rotated source", "type": "mysql", "options": {
            "host": "rotated-fixture", "db": "sales", "user": "qa", "passwd": "new-secret"}}
        data.update(overrides)
        return self.make_request("post", self.path, user=user or self.admin, data=data)

    def test_admin_rotation_keeps_id_rules_and_revokes_old_results_bindings_and_queued_scope(self):
        source_id, editor_id = self.source.id, self.editor.id
        old_scope = access_scope(self.source, self.editor)
        old_runner = ensure_execution(self.source, self.editor)
        result = self.factory.create_query_result(data_source=self.source, policy_scope=old_scope)
        models.db.session.commit()
        before_rules = copy.deepcopy(self.factory.org.settings)
        redis_connection.set(self.source._schema_key, "[]")
        response = self.update()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.source.id, source_id)
        self.assertEqual(self.factory.org.settings, before_rules)
        self.assertNotEqual(access_scope(self.source, self.editor), old_scope)
        self.assertFalse(result_access_allowed(result, self.editor))
        self.assertIsNone(redis_connection.get(self.source._schema_key))
        self.validate.assert_called_once()
        candidate = self.validate.call_args.args[0]
        self.assertEqual(candidate.options.get("passwd"), "new-secret")
        with self.assertRaisesRegex(ValueError, "changed"):
            old_runner.run_query("SELECT id FROM sales.orders", self.editor)
        executor = QueryExecutor("SELECT id FROM sales.orders", source_id, editor_id, False,
                                 {"policy_scope": old_scope}, False)
        with self.assertRaisesRegex(QueryExecutionError, "changed"):
            executor.run()

    def test_validation_failure_keeps_old_configuration_and_sanitizes_error(self):
        before = copy.deepcopy(self.source.options.to_dict())
        old_name = self.source.name
        self.validate.side_effect = ValueError("private-password@internal-host")
        response = self.update()
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("private-password", response.get_data(as_text=True))
        self.assertNotIn("internal-host", response.get_data(as_text=True))
        models.db.session.expire_all()
        source = models.DataSource.get_by_id(self.source.id)
        self.assertEqual(source.options.to_dict(), before)
        self.assertEqual(source.name, old_name)
        self.assertEqual(source.type, "mysql")

    def test_masked_password_preserves_existing_secret(self):
        response = self.update(options={"host": "fixture", "db": "sales", "user": "qa", "passwd": "--------"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.source.options.get("passwd"), "old-secret")
        self.assertEqual(self.validate.call_args.args[0].options.get("passwd"), "old-secret")

    def test_editor_reader_cannot_update_or_test_connection(self):
        with patch("redash.handlers.data_sources.test_connection.delay") as queue:
            for user in (self.editor, self.reader):
                self.assertEqual(self.update(user=user).status_code, 403)
                self.assertEqual(self.make_request("post", self.path + "/test", user=user, data={}).status_code, 403)
            queue.assert_not_called()
        self.validate.assert_not_called()

    def test_protected_connector_type_cannot_change(self):
        self.assertEqual(self.update(type="pg").status_code, 400)
        self.validate.assert_not_called()
        self.assertEqual(self.source.type, "mysql")

    def test_new_ssh_tunnel_is_rejected_using_candidate_options(self):
        self.validate.side_effect = validate_shared_source
        response = self.update(options={"db": "sales", "passwd": "new-secret", "ssh_tunnel": {"host": "fixture"}})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.validate.call_args.args[0].uses_ssh_tunnel)
        self.assertNotIn("ssh_tunnel", self.source.options)

    def test_retired_source_cannot_update_or_test_even_as_admin(self):
        self.factory.org.settings["data_access_internal_sources"] = [self.source.id]
        models.db.session.commit()
        with patch("redash.handlers.data_sources.test_connection.delay") as queue:
            self.assertEqual(self.update().status_code, 400)
            response = self.make_request("post", self.path + "/test", user=self.admin, data={})
            self.assertEqual(response.status_code, 400)
            queue.assert_not_called()
        self.validate.assert_not_called()

    def test_admin_test_connection_uses_existing_connector_job(self):
        with patch("redash.handlers.data_sources.test_connection.delay", return_value=SimpleNamespace(
                is_finished=True, is_failed=False, result=True)) as queue:
            response = self.make_request("post", self.path + "/test", user=self.admin, data={})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertTrue(response.json["ok"])
            queue.assert_called_once_with(self.source.id)

    def test_actual_connection_worker_validates_protected_source_without_protected_runner(self):
        self.assertIs(check_source_connection(self.source.id), True)
        self.validate.assert_called_once_with(self.source)

    def test_connection_worker_rechecks_retirement_before_connector(self):
        self.factory.org.settings["data_access_internal_sources"] = [self.source.id]
        models.db.session.commit()
        result = check_source_connection(self.source.id)
        self.assertIsInstance(result, ValueError)
        self.assertIn("Retired", str(result))
        self.validate.assert_not_called()

    def test_foreign_or_missing_source_is_not_updated(self):
        foreign = self.factory.create_data_source(org=self.factory.create_org())
        for source_id in (foreign.id, 2147483647):
            response = self.make_request("post", f"/api/data_sources/{source_id}", user=self.admin,
                                         data={"name": "Denied", "type": "mysql", "options": {"db": "sales"}})
            self.assertEqual(response.status_code, 404)
        self.validate.assert_not_called()
