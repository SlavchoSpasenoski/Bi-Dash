"""Scoped Editor AI contracts; disposable metadata and MariaDB only."""
import copy
import json
import os
import uuid
from unittest.mock import patch

from rq import SimpleWorker
from sqlalchemy.engine import make_url

from redash import models, redis_connection, rq_redis_connection
from redash.data_access_runtime import access_scope
from redash.tasks.worker import Queue
from redash.utils import json_dumps, utcnow
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class TestEditorAiAccess(BaseTestCase):
    def setUp(self):
        database = make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unset")).database or ""
        if not database.startswith("bidash_access_app_test"):
            self.skipTest("Requires explicitly disposable metadata database")
        host = os.environ.get("EDITOR_AI_MYSQL_HOST")
        if host != "bidash-editor-ai-mysql":
            self.skipTest("Requires isolated Editor AI MariaDB target")
        super().setUp()
        import MySQLdb
        self.target = MySQLdb.connect(host=host, user="root", passwd="disposable-ai-target", autocommit=True)
        with self.target.cursor() as cursor:
            cursor.execute("CREATE DATABASE IF NOT EXISTS bidash_editor_ai")
            cursor.execute("DROP TABLE IF EXISTS bidash_editor_ai.orders")
            cursor.execute("CREATE TABLE bidash_editor_ai.orders(id INT,total INT,created_at DATE,secret INT) ENGINE=InnoDB")
            cursor.execute("INSERT INTO bidash_editor_ai.orders VALUES(1,42,'2026-01-15',900)")
        self.admin = self.factory.create_admin()
        self.editor = self.factory.create_user(details={"role": "editor"})
        self.reader = self.factory.create_user(details={"role": "reader"})
        self.wide = self.factory.create_user(details={"role": "editor"})
        self.narrow_group = self.factory.create_group(name="Scoped AI")
        self.wide_group = self.factory.create_group(name="Broad AI")
        models.db.session.flush()
        self.editor.group_ids = self.editor.group_ids + [self.narrow_group.id]
        self.wide.group_ids = self.wide.group_ids + [self.wide_group.id]
        self.source = self.factory.data_source
        self.source.type = "mysql"
        self.source.queue_name = "editor_ai_test"
        self.source.options = ConfigurationContainer({"host": host, "port": 3306, "db": "bidash_editor_ai",
                                                      "user": "root", "passwd": "disposable-ai-target"})
        rule = {"catalog": None, "schema": "bidash_editor_ai", "table": "orders",
                "column_mode": "explicit", "columns": ["id", "total", "created_at"]}
        policy = {"id": "scoped-ai", "name": "Scoped AI", "data_source_id": self.source.id,
                  "status": "active", "default_effect": "deny", "user_ids": [],
                  "group_ids": [self.narrow_group.id], "rules": [rule]}
        wide_policy = copy.deepcopy(policy)
        wide_policy.update(id="broad-ai", group_ids=[self.wide_group.id])
        wide_policy["rules"][0]["columns"].append("secret")
        self.factory.org.settings["data_access_group_rules"] = {"revision": 1, "policies": [policy, wide_policy]}
        self.factory.org.settings["data_access_protected_sources"] = [self.source.id]
        self.factory.org.settings["ai_context"] = "ORG_PRIVATE_MARKER: orders.secret defines private payroll."
        self.factory.org.settings["ai_configuration"] = {"provider": "ollama", "profiles": {
            "ollama": {"model": "synthetic-fixture", "base_url": "http://ollama:11434", "encrypted_api_key": ""}}}
        models.db.session.commit()
        self.org_id, self.source_id = self.factory.org.id, self.source.id
        self.editor_id, self.wide_id = self.editor.id, self.wide.id
        self.admin_id, self.reader_id = self.admin.id, self.reader.id
        redis_connection.set(self.source._schema_key, json_dumps([{
            "name": "orders", "columns": ["id", "total", "created_at", "secret"],
            "description": "CACHE_PRIVATE_MARKER", "table_comment": "CACHE_PRIVATE_MARKER"}]))
        self.queue = Queue(self.source.queue_name, connection=rq_redis_connection)
        self.queue.empty()
        self.body = {"request_id": str(uuid.uuid4()), "data_source_id": self.source_id,
                     "tables": [], "prompt": "Show order totals"}
        self.plan = {"title": "Scoped orders", "description": "Synthetic scoped report", "widgets": [{
            "title": "Order totals", "sql": "SELECT id,total FROM orders", "visualization": "table"}]}

    def tearDown(self):
        if hasattr(self, "target"):
            self.target.close()
        if hasattr(self, "app_ctx"):
            super().tearDown()

    def make_request(self, *args, **kwargs):
        from flask import g
        g.pop("org", None)
        return super().make_request(*args, **kwargs)

    def user(self, user_id=None):
        return models.User.get_by_id(user_id or self.editor_id)

    def build(self, plan=None, body=None, user_id=None, side_effect=None):
        with patch("redash.handlers.ai.csrf.protect"), patch(
                "redash.handlers.ai_dashboards.ask_assistant", return_value=json.dumps(plan or self.plan),
                side_effect=side_effect) as ask:
            response = self.make_request("post", "/api/ai/dashboards", data=body or self.body,
                                         user=self.user(user_id))
        return response, ask

    def chat(self, answer="```sql\nSELECT id,total FROM orders\n```", body=None, user_id=None, side_effect=None):
        payload = {"data_source_id": self.source_id, "messages": [{"role": "user", "content": "Show order totals"}]}
        if body:
            payload.update(body)
        with patch("redash.handlers.ai.csrf.protect"), patch(
                "redash.handlers.ai.ask_assistant", return_value=answer, side_effect=side_effect) as ask:
            response = self.make_request("post", "/api/ai/chat", data=payload, user=self.user(user_id))
        return response, ask

    def revoke_total(self):
        org = models.Organization.get_by_id(self.org_id)
        config = copy.deepcopy(org.settings["data_access_group_rules"])
        config["policies"][0]["rules"][0]["columns"].remove("total")
        org.settings["data_access_group_rules"] = config
        models.db.session.commit()

    def generated_query(self, response):
        self.assertEqual(response.status_code, 200, response.json)
        return models.Query.get_by_id(response.json["queries"][0]["id"])

    def execute(self, query_id, user_id, *, dashboard_id=None):
        body = {"max_age": 0}
        if dashboard_id is not None:
            body["dashboard_id"] = dashboard_id
        response = self.make_request("post", f"/api/queries/{query_id}/results", data=body, user=self.user(user_id))
        self.assertEqual(response.status_code, 200, response.json)
        job_id = response.json["job"]["id"]
        SimpleWorker([self.queue], connection=rq_redis_connection, job_class=self.queue.job_class).work(
            burst=True, with_scheduler=False, logging_level="ERROR")
        response = self.make_request("get", f"/api/jobs/{job_id}", user=self.user(user_id))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["job"]["status"], 3, response.json)
        return models.db.session.get(models.QueryResult, response.json["job"]["query_result_id"])

    def test_editor_configuration_lists_only_authorized_sources_without_shared_context(self):
        response = self.make_request("get", "/api/ai/chat", user=self.user())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["scoped"])
        self.assertFalse(response.json["has_context"])
        self.assertIn(self.source_id, [source["id"] for source in response.json["data_sources"]])
        self.assertNotIn("ORG_PRIVATE_MARKER", response.get_data(as_text=True))

    def test_editor_and_reader_cannot_change_settings_context_or_data_rules(self):
        before = copy.deepcopy(models.Organization.get_by_id(self.org_id).settings)
        for user_id in (self.editor_id, self.reader_id):
            for path, payload in (("/api/settings/ai", {"provider": "ollama"}),
                                  ("/api/settings/ai/context", {"context": "unauthorized"}),
                                  ("/api/settings/data-access", {"revision": 1, "policies": []})):
                response = self.make_request("post", path, data=payload, user=self.user(user_id))
                self.assertEqual(response.status_code, 403, (path, response.status_code))
        self.assertEqual(models.Organization.get_by_id(self.org_id).settings, before)

    def test_chat_uses_server_scope_not_client_schema_global_context_or_prior_history(self):
        response, ask = self.chat(body={"schema": "SPOOF_PRIVATE_MARKER(secret)", "messages": [
            {"role": "user", "content": "PRIOR_PRIVATE_MARKER"},
            {"role": "assistant", "content": "ASSISTANT_PRIVATE_MARKER orders.secret"},
            {"role": "user", "content": "Show order totals"}]})
        self.assertEqual(response.status_code, 200, response.json)
        prompt = json.dumps({"args": ask.call_args.args[2:], "kwargs": ask.call_args.kwargs})
        for marker in ("ORG_PRIVATE_MARKER", "CACHE_PRIVATE_MARKER", "SPOOF_PRIVATE_MARKER",
                       "PRIOR_PRIVATE_MARKER", "ASSISTANT_PRIVATE_MARKER", "secret", "disposable-ai-target"):
            self.assertNotIn(marker, prompt)
        self.assertIn("total", prompt)
        self.assertEqual(ask.call_args.args[2], [{"role": "user", "content": "Show order totals"}])
        self.assertEqual(models.Query.query.count(), 0)

    def test_chat_rejects_forbidden_generated_dependencies_before_returning_answer(self):
        for sql in ("SELECT secret FROM orders", "SELECT id FROM orders WHERE secret>0",
                    "SELECT SUM(secret) FROM orders", "SELECT id FROM orders ORDER BY secret"):
            with self.subTest(sql=sql):
                response, ask = self.chat(answer=f"```sql\n{sql}\n```")
                self.assertIn(response.status_code, (403, 422), response.json)
                self.assertNotIn(sql, response.get_data(as_text=True))
                ask.assert_called_once()

    def test_chat_missing_or_foreign_source_never_calls_provider(self):
        for source_id in (None, 987654321):
            response, ask = self.chat(body={"data_source_id": source_id})
            self.assertIn(response.status_code, (400, 403, 404))
            ask.assert_not_called()
        other_org = self.factory.create_org()
        foreign = self.factory.create_data_source(org=other_org, group=other_org.default_group)
        models.db.session.commit()
        response, ask = self.chat(body={"data_source_id": foreign.id})
        self.assertIn(response.status_code, (403, 404))
        ask.assert_not_called()

    def test_reader_and_editor_without_group_rules_are_denied_before_provider(self):
        response, ask = self.chat(user_id=self.reader_id)
        self.assertEqual(response.status_code, 403)
        ask.assert_not_called()
        user = self.user()
        user.group_ids = [self.factory.default_group.id]
        models.db.session.commit()
        response, ask = self.chat()
        self.assertEqual(response.status_code, 403)
        ask.assert_not_called()
        response, ask = self.build()
        self.assertEqual(response.status_code, 403)
        ask.assert_not_called()

    def test_disabled_editor_and_disallowed_ai_policy_do_not_call_provider(self):
        editor = self.user()
        editor.disabled_at = utcnow()
        models.db.session.commit()
        response, ask = self.chat()
        self.assertIn(response.status_code, (401, 403, 404))
        ask.assert_not_called()
        editor = self.user()
        editor.disabled_at = None
        org = models.Organization.get_by_id(self.org_id)
        org.settings["access_policy"] = {"editor": {"allow_ai": False}}
        models.db.session.commit()
        response, ask = self.build()
        self.assertEqual(response.status_code, 403)
        ask.assert_not_called()

    def test_builder_omits_shared_context_and_creates_recipient_scoped_queries(self):
        response, ask = self.build(body={**self.body, "context": "EXTRA_PRIVATE_MARKER"})
        query = self.generated_query(response)
        source = models.DataSource.get_by_id(self.source_id)
        self.assertEqual(query.options["data_access_scope"], access_scope(source, self.user()))
        sent = json.dumps({"args": ask.call_args.args[2:], "kwargs": ask.call_args.kwargs})
        for marker in ("ORG_PRIVATE_MARKER", "CACHE_PRIVATE_MARKER", "EXTRA_PRIVATE_MARKER", "secret", "disposable-ai-target"):
            self.assertNotIn(marker, sent)
        self.assertIn("total", sent)

    def test_report_api_token_cannot_use_ai(self):
        dashboard = self.factory.create_dashboard(user=self.user())
        token = self.factory.create_api_key(object=dashboard)
        models.db.session.commit()
        client = self.app.test_client()
        prefix = f"/{models.Organization.get_by_id(self.org_id).slug}/api"
        headers = {"Authorization": "Key " + token.api_key}
        with patch("redash.handlers.ai.ask_assistant") as ask:
            response = client.post(prefix + "/ai/chat", json={"data_source_id": self.source_id,
                "messages": [{"role": "user", "content": "Show orders"}]}, headers=headers)
        self.assertEqual(response.status_code, 403)
        ask.assert_not_called()

    def test_paused_source_is_not_listed_or_sent_to_provider(self):
        source = models.DataSource.get_by_id(self.source_id)
        source.pause("Synthetic paused source")
        response = self.make_request("get", "/api/ai/chat", user=self.user())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertNotIn(self.source_id, [source["id"] for source in response.json["data_sources"]])
        response, ask = self.chat()
        self.assertEqual(response.status_code, 403)
        ask.assert_not_called()

    def test_admin_context_management_and_chat_keep_existing_behavior(self):
        with patch("redash.handlers.ai.csrf.protect"):
            response = self.make_request("post", "/api/settings/ai/context", data={"context": "ADMIN_CONTEXT_MARKER"},
                                         user=self.user(self.admin_id))
        self.assertEqual(response.status_code, 200, response.json)
        response, ask = self.chat(answer="Administrator explanation", user_id=self.admin_id)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["answer"], "Administrator explanation")
        self.assertEqual(ask.call_args.kwargs["context"], "ADMIN_CONTEXT_MARKER")

    def test_unprotected_mysql_uses_fresh_names_only_metadata_and_no_shared_context(self):
        org = models.Organization.get_by_id(self.org_id)
        org.settings["data_access_protected_sources"] = []
        org.settings["data_access_group_rules"] = {"revision": 2, "policies": []}
        models.db.session.commit()
        response, ask = self.chat()
        self.assertEqual(response.status_code, 200, response.json)
        sent = json.dumps({"args": ask.call_args.args[2:], "kwargs": ask.call_args.kwargs})
        self.assertIn("orders", sent)
        self.assertNotIn("CACHE_PRIVATE_MARKER", sent)
        self.assertNotIn("ORG_PRIVATE_MARKER", sent)
        self.assertNotIn("disposable-ai-target", sent)

    def test_builder_forbidden_widget_rejects_entire_plan_without_resources(self):
        for sql in ("SELECT secret FROM orders", "SELECT id FROM orders WHERE secret>0",
                    "SELECT SUM(secret) AS total FROM orders", "SELECT a.id FROM orders a JOIN orders b ON a.total=b.secret",
                    "SELECT id FROM orders WHERE total IN (SELECT secret FROM orders)"):
            with self.subTest(sql=sql):
                plan = copy.deepcopy(self.plan)
                plan["widgets"].append({"title": "Forbidden", "sql": sql, "visualization": "table"})
                response, _ = self.build(plan=plan)
                self.assertIn(response.status_code, (403, 422), response.json)
                self.assertEqual(models.Query.query.count(), 0)
                self.assertEqual(models.Dashboard.query.count(), 0)

    def test_builder_cannot_request_forbidden_schema(self):
        response, ask = self.build(body={**self.body, "tables": ["private_payroll"]})
        self.assertIn(response.status_code, (400, 403))
        ask.assert_not_called()

    def test_allowed_date_parameters_are_checked_and_saved(self):
        plan = copy.deepcopy(self.plan)
        plan["widgets"][0].update(sql="SELECT id,total FROM orders WHERE created_at BETWEEN '{{period.start}}' AND '{{period.end}}'",
            parameters=[{"name": "period", "title": "Period", "type": "date-range",
                         "value": {"start": "2026-01-01", "end": "2026-01-31"}}])
        response, _ = self.build(plan=plan)
        query = self.generated_query(response)
        self.assertEqual(query.options["parameters"][0]["type"], "date-range")
        self.assertEqual(response.json["queries"][0]["parameters"]["period"]["end"], "2026-01-31")

    def test_builder_rechecks_revocation_after_provider_before_persistence(self):
        def answer(*args, **kwargs):
            self.revoke_total()
            return json.dumps(self.plan)
        response, _ = self.build(side_effect=answer)
        self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(models.Dashboard.query.count(), 0)
        self.assertEqual(models.Query.query.count(), 0)

    def test_chat_rechecks_source_identity_after_provider(self):
        def answer(*args, **kwargs):
            source = models.DataSource.get_by_id(self.source_id)
            options = copy.deepcopy(source.options.to_dict())
            options["db"] = "changed-during-generation"
            source.options = ConfigurationContainer(options)
            models.db.session.commit()
            return "```sql\nSELECT id,total FROM orders\n```"
        response, _ = self.chat(side_effect=answer)
        self.assertEqual(response.status_code, 403, response.json)
        self.assertNotIn("SELECT id,total", response.get_data(as_text=True))

    def test_existing_request_rechecks_scope_after_revocation(self):
        response, _ = self.build()
        self.generated_query(response)
        self.revoke_total()
        response, ask = self.build()
        self.assertEqual(response.status_code, 403, response.json)
        ask.assert_not_called()

    def test_real_worker_executes_allowed_ai_query_and_results_stay_recipient_scoped(self):
        response, _ = self.build()
        query_id = self.generated_query(response).id
        narrow_result = self.execute(query_id, self.editor_id)
        narrow_id, narrow_scope = narrow_result.id, narrow_result.policy_scope
        self.assertEqual(narrow_result.data["rows"], [{"id": 1, "total": 42}])
        denied = self.make_request("post", f"/api/queries/{query_id}/results",
                                   data={"max_age": 0}, user=self.user(self.wide_id))
        self.assertEqual(denied.status_code, 403)
        dashboard = models.db.session.get(models.Dashboard, response.json["dashboard_id"])
        dashboard.is_draft = False
        models.db.session.add(models.DashboardReadGrant(
            dashboard_id=dashboard.id, user_id=self.wide_id, grantor_id=self.editor_id,
        ))
        models.db.session.commit()
        wide_result = self.execute(query_id, self.wide_id, dashboard_id=dashboard.id)
        self.assertEqual(wide_result.data["rows"], [{"id": 1, "total": 42}])
        self.assertNotEqual(wide_result.policy_scope, narrow_scope)
        for user_id, result_id in ((self.editor_id, wide_result.id), (self.wide_id, narrow_id)):
            for suffix in ("", ".csv"):
                response = self.make_request("get", f"/api/query_results/{result_id}{suffix}", user=self.user(user_id))
                self.assertEqual(response.status_code, 403)

    def test_real_worker_rechecks_queued_ai_query_after_revocation(self):
        response, _ = self.build()
        query_id = self.generated_query(response).id
        response = self.make_request("post", f"/api/queries/{query_id}/results", data={"max_age": 0}, user=self.user())
        self.assertEqual(response.status_code, 200, response.json)
        self.revoke_total()
        SimpleWorker([self.queue], connection=rq_redis_connection, job_class=self.queue.job_class).work(
            burst=True, with_scheduler=False, logging_level="ERROR")
        self.assertEqual(models.QueryResult.query.count(), 0)
