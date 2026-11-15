"""Per-user dashboard grants; destructive fixtures only on explicitly disposable DBs."""
import copy
import os
from unittest.mock import patch

from rq import SimpleWorker
from sqlalchemy import text
from sqlalchemy.engine import make_url

from redash import models, rq_redis_connection
from redash.tasks.worker import Queue
from redash.utils import utcnow
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class TestDashboardAccess(BaseTestCase):
    def setUp(self):
        url = make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unset"))
        if not (url.database or "").startswith("bidash_access_app_test"):
            self.skipTest("Requires explicitly disposable metadata database")
        super().setUp()
        self.owner = self.factory.create_user(details={"role": "editor"})
        self.viewer = self.factory.create_user(details={"role": "editor"})
        self.reader = self.factory.create_user(details={"role": "reader"})
        self.unassigned = self.factory.create_user(details={"role": "reader"})
        self.admin = self.factory.create_admin()
        self.disabled = self.factory.create_user(details={"role": "reader"}, disabled_at=utcnow())
        self.foreign = self.factory.create_user(org=self.factory.create_org(), details={"role": "reader"})
        self.source = self.factory.data_source
        self.source.queue_name = "dashboard_acl_test"
        self.source.options = ConfigurationContainer({"host": url.host, "port": url.port or 5432,
            "dbname": url.database, "user": url.username, "password": url.password})
        models.db.session.execute(text("DROP TABLE IF EXISTS dashboard_acl_rows"))
        models.db.session.execute(text("CREATE TABLE dashboard_acl_rows(created_at DATE)"))
        models.db.session.execute(text("INSERT INTO dashboard_acl_rows VALUES ('2026-01-15'),('2026-02-15')"))
        self.query = self.factory.create_query(user=self.owner, data_source=self.source, is_draft=False,
            query_text="SELECT COUNT(*) AS count FROM dashboard_acl_rows WHERE created_at BETWEEN '{{period.start}}' AND '{{period.end}}'",
            options={"parameters": [{"name": "period", "type": "date-range", "value": {
                "start": "2026-01-01", "end": "2026-01-31"}}]})
        self.cached_query = self.factory.create_query(user=self.owner, data_source=self.source,
            query_text="SELECT 42 AS amount", is_draft=False)
        self.cached_result = self.factory.create_query_result(data_source=self.source,
            query_text=self.cached_query.query_text, query_hash=self.cached_query.query_hash,
            data={"columns": [{"name": "amount", "type": "integer"}], "rows": [{"amount": 42}]})
        self.cached_query.latest_query_data = self.cached_result
        self.hidden_query = self.factory.create_query(user=self.owner, data_source=self.source,
            query_text="SELECT 99 AS private_amount", is_draft=False)
        self.hidden_result = self.factory.create_query_result(data_source=self.source,
            query_text=self.hidden_query.query_text, query_hash=self.hidden_query.query_hash,
            data={"columns": [{"name": "private_amount", "type": "integer"}], "rows": [{"private_amount": 99}]})
        self.hidden_query.latest_query_data = self.hidden_result
        self.dashboard = self.factory.create_dashboard(user=self.owner, name="ACL Visible", is_draft=False, tags=["visible-tag"])
        self.hidden = self.factory.create_dashboard(user=self.owner, name="ACL Hidden", is_draft=False, tags=["hidden-tag"])
        self.draft = self.factory.create_dashboard(user=self.owner, name="ACL Draft", is_draft=True, tags=["draft-tag"])
        self.archived = self.factory.create_dashboard(user=self.owner, name="ACL Archived", is_archived=True)
        self.widget = self.add_widget(self.dashboard, self.query)
        self.add_widget(self.dashboard, self.cached_query)
        self.add_widget(self.hidden, self.hidden_query)
        models.db.session.commit()
        self.ids = {name: getattr(self, name).id for name in ("owner", "viewer", "reader", "unassigned", "admin",
            "disabled", "foreign", "source", "query", "cached_query", "cached_result", "hidden_query", "hidden_result",
            "dashboard", "hidden", "draft", "archived", "widget")}
        self.org_id = self.source.org_id
        self.grant("dashboard", "reader")
        self.grant("dashboard", "viewer")
        self.queue = Queue(self.source.queue_name, connection=rq_redis_connection)
        self.queue.empty()

    def tearDown(self):
        if hasattr(self, "app_ctx"):
            models.db.session.rollback()
            models.db.session.execute(text("DROP TABLE IF EXISTS dashboard_acl_rows"))
            models.db.session.commit()
            super().tearDown()

    def make_request(self, *args, **kwargs):
        from flask import g
        g.pop("org", None)
        return super().make_request(*args, **kwargs)

    def user(self, name="reader"):
        return models.User.get_by_id(self.ids[name])

    def add_widget(self, dashboard, query):
        visualization = models.Visualization(query_rel=query, type="TABLE", name="ACL table", options={})
        widget = models.Widget(dashboard=dashboard, visualization=visualization, width=1,
                               options={"position": {"col": 0, "row": 0, "sizeX": 6, "sizeY": 8}})
        models.db.session.add_all([visualization, widget]); models.db.session.flush()
        dashboard.layout = (dashboard.layout or []) + [[widget.id]]
        return widget

    def grant(self, dashboard="dashboard", user="reader"):
        models.db.session.add(models.DashboardReadGrant(dashboard_id=self.ids[dashboard],
            user_id=self.ids[user], grantor_id=self.ids["owner"]))
        current = models.db.session.get(models.Dashboard, self.ids[dashboard])
        current.version = (current.version or 0) + 1
        models.db.session.commit()

    def revoke(self, dashboard="dashboard", user="reader"):
        models.DashboardReadGrant.query.filter_by(dashboard_id=self.ids[dashboard], user_id=self.ids[user]).delete()
        current = models.db.session.get(models.Dashboard, self.ids[dashboard])
        current.version = (current.version or 0) + 1
        models.db.session.commit()

    def dashboard_get(self, dashboard="dashboard", user="reader"):
        return self.make_request("get", f"/api/dashboards/{self.ids[dashboard]}", user=self.user(user))

    def access(self, dashboard="dashboard", user="owner"):
        return self.make_request("get", f"/api/dashboards/{self.ids[dashboard]}/access", user=self.user(user))

    def save_access(self, users, dashboard="dashboard", user="owner", revision=None, extra=None):
        if revision is None:
            revision = models.db.session.get(models.Dashboard, self.ids[dashboard]).version
        payload = {"read_user_ids": [self.ids[name] for name in users], "revision": revision, **(extra or {})}
        with patch("redash.security.csrf.protect"):
            return self.make_request("post", f"/api/dashboards/{self.ids[dashboard]}/access", data=payload, user=self.user(user))

    def refresh(self, user="reader", dashboard="dashboard", query="query", parameters=None):
        return self.make_request("post", f"/api/queries/{self.ids[query]}/results", user=self.user(user), data={
            "dashboard_id": self.ids[dashboard], "max_age": 0, "parameters": parameters if parameters is not None else {
                "period": {"start": "2026-01-01", "end": "2026-01-31"}}})

    def work(self):
        SimpleWorker([self.queue], connection=rq_redis_connection, job_class=self.queue.job_class).work(
            burst=True, with_scheduler=False, logging_level="ERROR")

    def completed_refresh(self, user="reader", parameters=None):
        response = self.refresh(user=user, parameters=parameters)
        self.assertEqual(response.status_code, 200, response.json)
        job_id = response.json["job"]["id"]
        self.work()
        response = self.make_request("get", f"/api/jobs/{job_id}", user=self.user(user))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["job"]["status"], 3, response.json)
        return job_id, response.json["job"]["query_result_id"]

    def test_default_deny_does_not_implicitly_share_old_published_dashboards(self):
        self.revoke(user="reader"); self.revoke(user="viewer")
        for role in ("reader", "viewer", "unassigned"):
            self.assertIn(self.dashboard_get(user=role).status_code, (403, 404))
        self.assertEqual(self.dashboard_get(user="owner").status_code, 200)
        self.assertEqual(self.dashboard_get(user="admin").status_code, 200)

    def test_listing_search_pagination_favorites_and_tags_filter_before_counts(self):
        for name in ("dashboard", "hidden", "draft"):
            models.db.session.add(models.Favorite(org_id=self.org_id, object=models.db.session.get(models.Dashboard, self.ids[name]),
                                                 user=self.user("viewer")))
        models.db.session.commit()
        for path in ("/api/dashboards?q=ACL&page_size=1", "/api/dashboards/favorites?q=ACL&page_size=1"):
            response = self.make_request("get", path, user=self.user("viewer"))
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json["count"], 1)
            self.assertEqual([item["id"] for item in response.json["results"]], [self.ids["dashboard"]])
        response = self.make_request("get", "/api/dashboards/tags", user=self.user("viewer"))
        tags = [item["name"] for item in response.json["tags"]]
        self.assertIn("visible-tag", tags); self.assertNotIn("hidden-tag", tags); self.assertNotIn("draft-tag", tags)
        response = self.make_request("get", "/api/dashboards?include_all=true&q=ACL", user=self.user("unassigned"))
        self.assertEqual(response.json["count"], 0)

    def test_direct_draft_and_archive_visibility_is_consistent(self):
        self.grant("draft", "reader"); self.grant("draft", "viewer")
        self.grant("archived", "reader")
        for role in ("reader", "viewer", "unassigned"):
            self.assertIn(self.dashboard_get("draft", role).status_code, (403, 404))
        for role in ("reader", "owner", "admin"):
            self.assertIn(self.dashboard_get("archived", role).status_code, (403, 404))
        for role in ("owner", "admin"):
            self.assertEqual(self.dashboard_get("draft", role).status_code, 200)

    def test_only_admin_or_owner_editor_can_manage_read_grants(self):
        for role in ("owner", "admin"):
            response = self.access(user=role)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertTrue(response.json["can_manage"])
            candidates = {item["id"] for item in response.json["candidates"]}
            self.assertIn(self.ids["reader"], candidates)
            self.assertNotIn(self.ids["foreign"], candidates)
            self.assertNotIn(self.ids["disabled"], candidates)
            self.assertNotIn(self.ids["admin"], candidates)
        for role in ("reader", "viewer", "unassigned"):
            self.assertEqual(self.access(user=role).status_code, 403)
            self.assertEqual(self.save_access(["unassigned"], user=role).status_code, 403)

    def test_grants_replace_atomically_with_optimistic_revision_and_no_edit_rights(self):
        initial = self.access().json
        response = self.save_access(["unassigned"], revision=initial["revision"])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["read_user_ids"], [self.ids["unassigned"]])
        self.assertIn(self.dashboard_get(user="reader").status_code, (403, 404))
        response = self.dashboard_get(user="unassigned")
        self.assertEqual(response.status_code, 200); self.assertFalse(response.json["can_edit"])
        stale = self.save_access(["reader"], revision=initial["revision"])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.access().json["read_user_ids"], [self.ids["unassigned"]])

    def test_invalid_foreign_inactive_admin_and_forged_payloads_do_not_change_grants(self):
        before = self.access().json["read_user_ids"]
        for users in (["foreign"], ["disabled"], ["admin"], ["owner"]):
            self.assertEqual(self.save_access(users).status_code, 400)
        self.assertEqual(self.save_access(["reader"], extra={"user_id": self.ids["viewer"]}).status_code, 400)
        revision = models.db.session.get(models.Dashboard, self.ids["dashboard"]).version
        for payload in ({"read_user_ids": [True], "revision": revision},
                        {"read_user_ids": [self.ids["reader"], self.ids["reader"]], "revision": revision},
                        {"read_user_ids": [self.ids["reader"]], "revision": str(revision)}):
            with patch("redash.security.csrf.protect"):
                response = self.make_request("post", f"/api/dashboards/{self.ids['dashboard']}/access",
                                             data=payload, user=self.user("owner"))
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.access().json["read_user_ids"], before)

    def test_read_grant_save_requires_session_csrf(self):
        response = self.make_request("post", f"/api/dashboards/{self.ids['dashboard']}/access", user=self.user("owner"),
            data={"revision": models.db.session.get(models.Dashboard, self.ids["dashboard"]).version, "read_user_ids": []})
        self.assertEqual(response.status_code, 400)
        self.assertIn("CSRF", response.get_data(as_text=True))

    def test_draft_archive_cannot_receive_new_grants_but_manager_can_clear(self):
        for dashboard in ("draft", "archived"):
            self.assertEqual(self.save_access(["reader"], dashboard=dashboard, user="admin").status_code, 400)
            self.assertEqual(self.save_access([], dashboard=dashboard, user="admin").status_code, 200)

    def test_dashboard_content_owner_only_even_for_nonowner_admin(self):
        for role in ("viewer", "admin", "reader"):
            response = self.make_request("post", f"/api/dashboards/{self.ids['dashboard']}",
                user=self.user(role), data={"name": "unauthorized content"})
            self.assertEqual(response.status_code, 403)
        response = self.make_request("post", f"/api/dashboards/{self.ids['dashboard']}",
            user=self.user("owner"), data={"name": "Owner content"})
        self.assertEqual(response.status_code, 200, response.json)

    def test_dashboard_options_do_not_forge_read_grants_or_transfer_owner(self):
        response = self.make_request("post", f"/api/dashboards/{self.ids['dashboard']}", user=self.user("owner"),
            data={"user_id": self.ids["viewer"], "options": {"read_user_ids": [self.ids["unassigned"]]}})
        self.assertIn(response.status_code, (200, 400))
        self.assertEqual(models.db.session.get(models.Dashboard, self.ids["dashboard"]).user_id, self.ids["owner"])
        self.assertIn(self.dashboard_get(user="unassigned").status_code, (403, 404))

    def test_recipient_cannot_fork_reshare_or_attach_original_query_to_owned_copy(self):
        self.assertEqual(self.make_request("post", f"/api/dashboards/{self.ids['dashboard']}/fork",
            user=self.user("viewer")).status_code, 403)
        owned = self.factory.create_dashboard(user=self.user("viewer"), is_draft=False)
        vis = models.db.session.get(models.Widget, self.ids["widget"]).visualization
        response = self.make_request("post", "/api/widgets", user=self.user("viewer"),
            data={"dashboard_id": owned.id, "visualization_id": vis.id, "width": 1, "options": {}})
        self.assertEqual(response.status_code, 403)

    def test_query_visualization_and_generic_modify_grants_cannot_bypass_owner(self):
        query = models.Query.get_by_id(self.ids["query"])
        models.AccessPermission.grant(query, "modify", self.user("viewer"), self.user("admin"))
        models.db.session.commit()
        vis_id = models.db.session.get(models.Widget, self.ids["widget"]).visualization_id
        for role in ("viewer", "admin"):
            for path, data in ((f"/api/visualizations/{vis_id}", {"name": "unauthorized chart"}),
                               (f"/api/queries/{query.id}", {"query": "SELECT 999"}),
                               (f"/api/widgets/{self.ids['widget']}", {"text": "unauthorized", "options": {}})):
                self.assertEqual(self.make_request("post", path, data=data, user=self.user(role)).status_code, 403)
        response = self.make_request("post", f"/api/dashboards/{self.ids['dashboard']}/acl", user=self.user("admin"),
            data={"user_id": self.ids["viewer"], "access_type": "modify"})
        self.assertIn(response.status_code, (400, 403))
        self.assertFalse(models.AccessPermission.exists(models.db.session.get(models.Dashboard, self.ids["dashboard"]),
                                                        "modify", self.user("viewer")))

    def test_hidden_dashboard_cannot_be_favorited(self):
        response = self.make_request("post", f"/api/dashboards/{self.ids['hidden']}/favorite", user=self.user("reader"))
        self.assertIn(response.status_code, (403, 404))

    def test_read_grant_does_not_bypass_source_access(self):
        reader = self.user(); reader.group_ids = []; models.db.session.commit()
        response = self.dashboard_get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(widget.get("restricted") for widget in response.json["widgets"]))
        self.assertEqual(self.refresh().status_code, 403)

    def test_reader_legacy_result_and_exports_require_assigned_dashboard(self):
        for role, expected in (("reader", 200), ("unassigned", 403)):
            for suffix in ("", ".csv", ".tsv", ".xlsx"):
                response = self.make_request("get", f"/api/query_results/{self.ids['cached_result']}{suffix}", user=self.user(role))
                self.assertEqual(response.status_code, expected, response.json if suffix == "" else response.status_code)
        self.revoke()
        self.assertEqual(self.make_request("get", f"/api/query_results/{self.ids['cached_result']}", user=self.user()).status_code, 403)

    def test_reader_orphan_and_hidden_results_and_mismatched_result_ids_fail_closed(self):
        orphan = self.factory.create_query_result(data_source=models.DataSource.get_by_id(self.ids["source"]))
        for result_id in (orphan.id, self.ids["hidden_result"]):
            response = self.make_request("get", f"/api/query_results/{result_id}", user=self.user())
            self.assertEqual(response.status_code, 403)
        response = self.make_request("get", f"/api/queries/{self.ids['cached_query']}/results/{self.ids['hidden_result']}.json", user=self.user())
        self.assertIn(response.status_code, (403, 404))

    def test_reader_refresh_real_pg_parameters_and_result_provenance(self):
        for month in ("01", "02"):
            _, result_id = self.completed_refresh(parameters={"period": {"start": f"2026-{month}-01", "end": f"2026-{month}-28"}})
            result = models.db.session.get(models.QueryResult, result_id)
            self.assertEqual(result.data["rows"], [{"count": 1}])
            response = self.make_request("get", f"/api/query_results/{result_id}", user=self.user())
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("query", response.json["query_result"])
        self.assertIsNone(models.Query.get_by_id(self.ids["query"]).latest_query_data_id)

    def test_reader_completed_result_job_and_export_revoked_with_dashboard_grant(self):
        job_id, result_id = self.completed_refresh()
        self.revoke()
        for path in (f"/api/jobs/{job_id}", f"/api/query_results/{result_id}", f"/api/query_results/{result_id}.csv"):
            self.assertEqual(self.make_request("get", path, user=self.user()).status_code, 403)

    def test_queued_reader_revocation_and_template_change_prevent_execution(self):
        before = models.QueryResult.query.count()
        queued = self.refresh(); self.assertEqual(queued.status_code, 200, queued.json)
        job_id = queued.json["job"]["id"]
        self.revoke(); self.work()
        self.assertEqual(models.QueryResult.query.count(), before)
        self.assertEqual(self.make_request("get", f"/api/jobs/{job_id}", user=self.user()).status_code, 403)
        self.grant()
        queued = self.refresh(); self.assertEqual(queued.status_code, 200, queued.json)
        query = models.Query.get_by_id(self.ids["query"]); query.query_text = "SELECT 999 AS count"; models.db.session.commit()
        self.work(); self.assertEqual(models.QueryResult.query.count(), before)

    def test_reader_refresh_rejects_unrelated_dashboard_unassigned_query_and_unsafe_values(self):
        with patch("redash.handlers.query_results.enqueue_query") as enqueue:
            self.assertEqual(self.refresh(dashboard="hidden").status_code, 403)
            self.assertEqual(self.refresh(query="hidden_query").status_code, 403)
            self.assertEqual(self.refresh(parameters={"period": {"start": "2026-01-01' OR 1=1 --", "end": "2026-01-31"}}).status_code, 403)
            enqueue.assert_not_called()

    def test_query_dropdowns_do_not_expose_same_source_unassigned_report(self):
        for path in (f"/api/queries/{self.ids['hidden_query']}/dropdown",
                     f"/api/queries/{self.ids['query']}/dropdowns/{self.ids['hidden_query']}"):
            self.assertEqual(self.make_request("get", path, user=self.user()).status_code, 403)

    def test_dashboard_context_editor_job_and_result_recheck_revocation(self):
        before = models.QueryResult.query.count()
        queued = self.refresh(user="viewer"); self.assertEqual(queued.status_code, 200, queued.json)
        self.revoke(user="viewer"); self.work()
        self.assertEqual(models.QueryResult.query.count(), before)
        self.assertEqual(self.make_request("get", f"/api/jobs/{queued.json['job']['id']}", user=self.user("viewer")).status_code, 403)
        self.grant(user="viewer")
        _, result_id = self.completed_refresh(user="viewer")
        self.revoke(user="viewer")
        self.assertEqual(self.make_request("get", f"/api/query_results/{result_id}", user=self.user("viewer")).status_code, 403)

    def test_demoted_reader_owner_still_requires_explicit_published_grant(self):
        owner = self.user("owner"); owner.details = {"role": "reader"}; models.db.session.commit()
        self.assertIn(self.dashboard_get(user="owner").status_code, (403, 404))
        self.assertIn(self.dashboard_get("draft", "owner").status_code, (403, 404))
        response = self.save_access(["owner"], user="admin")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.dashboard_get(user="owner").status_code, 200)
        self.assertIn(self.dashboard_get("draft", "owner").status_code, (403, 404))

    def test_public_and_query_tokens_do_not_replace_user_read_grants(self):
        response = self.make_request("post", f"/api/dashboards/{self.ids['dashboard']}/share", user=self.user("owner"))
        self.assertEqual(response.status_code, 403)
        dashboard = models.db.session.get(models.Dashboard, self.ids["dashboard"])
        key = models.ApiKey.create_for_object(dashboard, self.user("owner")); models.db.session.commit()
        anonymous = self.app.test_client()
        prefix = "/" + self.factory.org.slug
        response = anonymous.get(prefix + f"/api/dashboards/public/{key.api_key}")
        self.assertIn(response.status_code, (403, 404))
        query = models.Query.get_by_id(self.ids["cached_query"])
        response = anonymous.get(prefix + f"/api/queries/{query.id}/results?api_key={query.api_key}")
        self.assertIn(response.status_code, (403, 404))

    def test_optional_mysql_editor_column_rules_remain_independent_of_dashboard_grants(self):
        host = os.environ.get("DASHBOARD_ACL_MYSQL_HOST")
        if host != "bidash-dashboard-acl-mysql":
            self.skipTest("Requires isolated dashboard ACL MariaDB target")
        import MySQLdb
        from redash.data_access_runtime import access_scope
        target = MySQLdb.connect(host=host, user="root", passwd="disposable-acl-target", autocommit=True)
        try:
            with target.cursor() as cursor:
                cursor.execute("CREATE DATABASE IF NOT EXISTS bidash_dashboard_acl")
                cursor.execute("DROP TABLE IF EXISTS bidash_dashboard_acl.orders")
                cursor.execute("CREATE TABLE bidash_dashboard_acl.orders(id INT,total INT,secret INT) ENGINE=InnoDB")
                cursor.execute("INSERT INTO bidash_dashboard_acl.orders VALUES(1,42,900)")
            source = models.DataSource.get_by_id(self.ids["source"])
            source.type = "mysql"; source.options = ConfigurationContainer({"host": host, "db": "bidash_dashboard_acl",
                "user": "root", "passwd": "disposable-acl-target", "port": 3306})
            wide, narrow = self.factory.create_group(), self.factory.create_group()
            self.user("owner").group_ids = self.user("owner").group_ids + [wide.id]
            self.user("viewer").group_ids = self.user("viewer").group_ids + [narrow.id]
            rule = {"catalog": None, "schema": "bidash_dashboard_acl", "table": "orders", "column_mode": "explicit", "columns": ["id", "total", "secret"]}
            policy = {"id": "wide", "name": "Wide", "data_source_id": source.id, "status": "active", "default_effect": "deny",
                "user_ids": [], "group_ids": [wide.id], "rules": [rule]}
            limited = copy.deepcopy(policy); limited.update(id="narrow", group_ids=[narrow.id]); limited["rules"][0]["columns"].remove("secret")
            org = models.Organization.get_by_id(self.org_id)
            org.settings["data_access_group_rules"] = {"revision": 1, "policies": [policy, limited]}
            org.settings["data_access_protected_sources"] = [source.id]
            models.db.session.commit()
            query = models.Query.get_by_id(self.ids["query"])
            query.query_text = "SELECT id,total FROM orders"
            query.options = {"parameters": [], "data_access_scope": access_scope(source, self.user("owner"))}
            denied = self.factory.create_query(user=self.user("owner"), data_source=source, query_text="SELECT secret FROM orders",
                options={"parameters": [], "data_access_scope": access_scope(source, self.user("owner"))})
            self.add_widget(models.db.session.get(models.Dashboard, self.ids["dashboard"]), denied); models.db.session.commit()
            denied_id = denied.id
            response = self.dashboard_get(user="viewer")
            self.assertEqual(response.status_code, 200, response.json)
            self.assertTrue(any(widget.get("restricted") for widget in response.json["widgets"]))
            _, result_id = self.completed_refresh(user="viewer", parameters={})
            self.assertEqual(models.db.session.get(models.QueryResult, result_id).data["rows"], [{"id": 1, "total": 42}])
            self.assertEqual(self.make_request("post", f"/api/queries/{denied_id}/results", user=self.user("viewer"),
                data={"dashboard_id": self.ids["dashboard"], "parameters": {}, "max_age": 0}).status_code, 403)
        finally:
            target.close()

    def test_cached_dependencies_enforce_query_acl_and_report_provenance(self):
        from redash.query_runner.query_results import get_query_results, PermissionError
        from redash.query_runner.python import Python
        reader = self.user("reader")
        self.assertEqual(get_query_results(reader, self.ids["cached_query"], True)["rows"], [{"amount": 42}])
        with self.assertRaises(PermissionError):
            get_query_results(reader, self.ids["hidden_query"], True)
        runner = object.__new__(Python)
        runner._get_current_user = lambda: reader
        self.assertEqual(runner.get_query_result(self.ids["cached_query"])["rows"], [{"amount": 42}])
        with self.assertRaises(Exception):
            runner.get_query_result(self.ids["hidden_query"])

    def test_dropdown_dependency_requires_named_actor_and_assigned_report(self):
        from redash.models.parameterized_query import _load_result, InvalidParameterError
        with self.assertRaises(InvalidParameterError):
            _load_result(self.ids["cached_query"], self.factory.org)
        with self.app.test_request_context("/"), patch("flask_login.current_user", self.user("reader")):
            self.assertEqual(_load_result(self.ids["cached_query"], self.factory.org)["rows"], [{"amount": 42}])
            with self.assertRaises(InvalidParameterError):
                _load_result(self.ids["hidden_query"], self.factory.org)

    def test_queued_adhoc_rechecks_source_full_access_downgrade(self):
        response = self.make_request("post", "/api/query_results", user=self.user("viewer"),
            data={"query": "SELECT 1 AS count", "data_source_id": self.ids["source"], "max_age": 0})
        self.assertEqual(response.status_code, 200, response.json)
        job_id = response.json["job"]["id"]
        before = models.QueryResult.query.count()
        for link in models.DataSourceGroup.query.filter_by(data_source_id=self.ids["source"]).all():
            link.view_only = True
        models.db.session.commit()
        self.work()
        self.assertEqual(models.QueryResult.query.count(), before)
        polled = self.make_request("get", f"/api/jobs/{job_id}", user=self.user("viewer"))
        self.assertIn(polled.status_code, (200, 403))
        if polled.status_code == 200:
            self.assertEqual(polled.json["job"]["status"], 4)
