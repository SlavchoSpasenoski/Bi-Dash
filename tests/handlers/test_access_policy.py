from unittest.mock import patch

from redash import models
from redash.access_policy import policy_for
from redash.tasks.queries.execution import QueryExecutionError, enqueue_query
from tests import BaseTestCase


class TestAccessPolicy(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.factory.create_admin()
        self.editor = self.factory.create_user(details={"role": "editor"})
        self.reader = self.factory.create_user(details={"role": "reader"})

    def post_access(self, path, data, user=None):
        with patch("redash.handlers.access_policy.csrf.protect"):
            return self.make_request("post", path, data=data, user=user or self.admin)

    def report(self):
        query = self.factory.create_query(query_text="SELECT 42 AS private_sql_marker", user=self.editor)
        result = self.factory.create_query_result(
            data_source=query.data_source, query_text=query.query_text, query_hash=query.query_hash,
            data={"columns": [{"name": "value", "type": "integer"}], "rows": [{"value": 42}]})
        query.latest_query_data = result
        visualization = self.factory.create_visualization(query_rel=query)
        dashboard = self.factory.create_dashboard(user=self.editor, is_draft=False)
        self.factory.create_widget(dashboard=dashboard, visualization=visualization)
        models.db.session.commit()
        return query, result, dashboard

    def test_reader_sees_data_and_downloads_but_never_sql_or_query_key(self):
        query, result, dashboard = self.report()
        response = self.make_request("get", f"/api/dashboards/{dashboard.id}", user=self.reader)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("private_sql_marker", response.get_data(as_text=True))
        self.assertNotIn(query.api_key, response.get_data(as_text=True))
        self.assertFalse(response.json["can_edit"])
        response = self.make_request("get", f"/api/query_results/{result.id}", user=self.reader)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["query_result"]["data"]["rows"], [{"value": 42}])
        self.assertNotIn("query", response.json["query_result"])
        self.assertNotIn("query_hash", response.json["query_result"])
        for filetype in ("csv", "tsv", "xlsx"):
            self.assertEqual(self.make_request("get", f"/api/query_results/{result.id}.{filetype}", user=self.reader).status_code, 200)

    def test_reader_direct_api_cannot_view_execute_modify_or_access_schema(self):
        query, _, dashboard = self.report()
        for method, path, data in (
            ("get", f"/api/queries/{query.id}", None),
            ("get", "/api/queries", None),
            ("get", f"/api/data_sources/{query.data_source_id}/schema", None),
            ("get", "/api/query_snippets", None),
            ("post", "/api/query_results", {"query": "SELECT 1", "data_source_id": query.data_source_id}),
            ("post", f"/api/queries/{query.id}/refresh", {}),
            ("delete", f"/api/queries/{query.id}", None),
            ("post", f"/api/dashboards/{dashboard.id}", {"name": "changed"}),
            ("post", "/api/ai/chat", {"messages": [{"role": "user", "content": "help"}]}),
        ):
            with self.subTest(path=path, method=method):
                response = self.make_request(method, path, data=data, user=self.reader)
                self.assertEqual(response.status_code, 403)

    def test_reader_cached_parameter_requests_do_not_enqueue(self):
        query, _, _ = self.report()
        with patch("redash.handlers.query_results.enqueue_query") as enqueue:
            response = self.make_request("post", f"/api/queries/{query.id}/results",
                                         data={"max_age": 0}, user=self.reader)
        self.assertEqual(response.status_code, 200)
        enqueue.assert_not_called()
        self.assertNotIn("query", response.json["query_result"])
        query.latest_query_data = None
        models.db.session.commit()
        models.QueryResult.query.delete()
        models.db.session.commit()
        with patch("redash.handlers.query_results.enqueue_query") as enqueue:
            response = self.make_request("post", f"/api/queries/{query.id}/results", data={}, user=self.reader)
        self.assertEqual(response.status_code, 403)
        enqueue.assert_not_called()

    def test_reader_download_policy_is_enforced_by_api(self):
        _, result, _ = self.report()
        policy = policy_for(self.factory.org)
        policy["reader"]["allow_download"] = False
        self.assertEqual(self.post_access("/api/settings/access-policy", policy).status_code, 200)
        self.assertEqual(self.make_request("get", f"/api/query_results/{result.id}.csv", user=self.reader).status_code, 403)
        self.assertEqual(self.make_request("get", f"/api/query_results/{result.id}", user=self.reader).status_code, 200)

    def test_only_admin_assigns_roles_and_no_self_demotion(self):
        path = f"/api/users/{self.editor.id}/access"
        self.assertEqual(self.post_access(path, {"role": "admin"}, user=self.editor).status_code, 403)
        self.assertEqual(self.post_access(path, {"role": "reader"}).status_code, 200)
        models.db.session.expire_all()
        self.assertEqual(models.User.get_by_id(self.editor.id).role, "reader")
        self.assertEqual(self.post_access(f"/api/users/{self.admin.id}/access", {"role": "reader"}).status_code, 400)

    def test_editor_group_admin_permission_cannot_override_assigned_role(self):
        self.editor.group_ids.append(self.factory.admin_group.id)
        models.db.session.commit()
        self.assertFalse(self.editor.has_permission("admin"))
        self.assertEqual(self.make_request("get", "/api/settings/access-policy", user=self.editor).status_code, 403)

    def test_editor_scheduling_sharing_deletion_and_ai_policy(self):
        from redash.tasks.queries.maintenance import _should_refresh_query
        query, _, dashboard = self.report()
        self.assertFalse(_should_refresh_query(query))
        self.assertEqual(self.make_request("post", f"/api/queries/{query.id}",
                                         data={"schedule": {"interval": 60}}, user=self.editor).status_code, 403)
        self.assertEqual(self.make_request("post", f"/api/dashboards/{dashboard.id}/share", data={}, user=self.editor).status_code, 403)
        self.assertEqual(self.make_request("delete", f"/api/queries/{query.id}", user=self.editor).status_code, 403)
        self.assertEqual(self.make_request("post", f"/api/dashboards/{dashboard.id}",
                                         data={"is_archived": True}, user=self.editor).status_code, 403)
        policy = policy_for(self.factory.org)
        policy["editor"]["allow_ai"] = False
        self.post_access("/api/settings/access-policy", policy)
        self.assertEqual(self.make_request("get", "/api/ai/chat", user=self.editor).status_code, 403)

    def test_editor_timeout_and_outstanding_query_limit(self):
        source = self.factory.data_source
        first = enqueue_query("SELECT 1", source, self.editor.id, metadata={"query_id": "adhoc"})
        self.assertEqual(first.timeout, 60)
        self.assertEqual(enqueue_query("SELECT 1", source, self.editor.id).id, first.id)
        enqueue_query("SELECT 2", source, self.editor.id)
        with self.assertRaises(QueryExecutionError):
            enqueue_query("SELECT 3", source, self.editor.id)
        first.cancel()
        self.assertIsNotNone(enqueue_query("SELECT 3", source, self.editor.id))

    def test_stricter_user_limit_and_admin_exemption(self):
        self.post_access(f"/api/users/{self.editor.id}/access", {
            "role": "editor", "limits": {"query_timeout_seconds": 5, "max_concurrent_queries": 1}})
        job = enqueue_query("SELECT 1", self.factory.data_source, self.editor.id)
        self.assertEqual(job.timeout, 5)
        with self.assertRaises(QueryExecutionError):
            enqueue_query("SELECT 2", self.factory.data_source, self.editor.id)
        with patch("redash.settings.dynamic_settings.query_time_limit", return_value=180):
            job = enqueue_query("SELECT 3", self.factory.data_source, self.admin.id)
        self.assertEqual(job.timeout, 180)

    def test_policy_validation_and_csrf(self):
        policy = policy_for(self.factory.org)
        policy["editor"]["query_timeout_seconds"] = -1
        self.assertEqual(self.post_access("/api/settings/access-policy", policy).status_code, 400)
        response = self.make_request("post", "/api/settings/access-policy", data=policy, user=self.admin)
        self.assertEqual(response.status_code, 400)
        self.assertIn("CSRF", response.get_data(as_text=True))

    def test_new_invitations_default_to_reader_and_admin_can_choose_editor(self):
        response = self.make_request("post", "/api/users?no_invite", data={
            "name": "New Reader", "email": "reader@bidash-testing.test"}, user=self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["role"], "reader")
        response = self.make_request("post", "/api/users?no_invite", data={
            "name": "New Editor", "email": "editor@bidash-testing.test", "role": "editor"}, user=self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["role"], "editor")

    def test_reader_personal_api_key_cannot_bypass_role(self):
        query, result, _ = self.report()
        client = self.app.test_client()
        headers = {"Authorization": "Key " + self.reader.api_key}
        prefix = f"/{self.factory.org.slug}/api"
        self.assertEqual(client.get(f"{prefix}/queries/{query.id}", headers=headers).status_code, 403)
        response = client.get(f"{prefix}/query_results/{result.id}", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("query", response.json["query_result"])

    def test_report_tokens_cannot_reveal_sql_or_snippets(self):
        query, result, dashboard = self.report()
        client = self.app.test_client()
        token = self.factory.create_api_key(object=dashboard)
        headers = {"Authorization": "Key " + token.api_key}
        prefix = f"/{self.factory.org.slug}/api"
        response = client.get(f"{prefix}/queries/{query.id}", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("query", response.json)
        self.assertNotIn("private_sql_marker", response.get_data(as_text=True))
        self.assertEqual(client.get(f"{prefix}/query_snippets", headers=headers).status_code, 403)
        self.assertEqual(client.get(f"{prefix}/data_sources/{query.data_source_id}/schema", headers=headers).status_code, 403)

    def test_query_serialization_without_a_request_hides_sql_and_credentials(self):
        from redash.serializers import serialize_query

        query = self.factory.create_query(query_text="SELECT 'private_sql_marker'")
        result = serialize_query(query)
        for field in ("query", "query_hash", "api_key", "schedule"):
            self.assertNotIn(field, result)
