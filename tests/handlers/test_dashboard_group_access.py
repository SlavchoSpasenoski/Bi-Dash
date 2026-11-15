"""Group dashboard audiences; destructive fixtures only on disposable metadata."""
import os
from unittest.mock import patch

from rq import SimpleWorker
from sqlalchemy.engine import make_url

from redash import models, rq_redis_connection
from redash.tasks.worker import Queue
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class TestDashboardGroupAccess(BaseTestCase):
    def setUp(self):
        url = make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unset"))
        if not (url.database or "").startswith("bidash_access_app_test"):
            self.skipTest("Requires explicitly disposable metadata database")
        super().setUp()
        self.owner = self.factory.create_user(details={"role": "editor"})
        self.editor = self.factory.create_user(details={"role": "editor"})
        self.reader = self.factory.create_user(details={"role": "reader"})
        self.admin = self.factory.create_admin()
        self.group = self.factory.create_group(name="Report audience")
        self.other_group = self.factory.create_group(name="Other audience")
        models.db.session.flush()
        for user in (self.editor, self.reader):
            user.group_ids = user.group_ids + [self.group.id]
        self.source = self.factory.data_source
        self.source.queue_name = "dashboard_group_acl_test"
        self.source.options = ConfigurationContainer({"host": url.host, "port": url.port or 5432,
            "dbname": url.database, "user": url.username, "password": url.password})
        self.query = self.factory.create_query(user=self.owner, data_source=self.source,
            query_text="SELECT 42 AS amount", is_draft=False)
        self.result = self.factory.create_query_result(data_source=self.source,
            query_text=self.query.query_text, query_hash=self.query.query_hash,
            data={"columns": [{"name": "amount", "type": "integer"}], "rows": [{"amount": 42}]})
        self.query.latest_query_data = self.result
        self.dashboard = self.factory.create_dashboard(user=self.owner, name="Group report", is_draft=False)
        self.draft = self.factory.create_dashboard(user=self.owner, name="Unpublished report", is_draft=True)
        self.archived = self.factory.create_dashboard(user=self.owner, name="Retired report", is_draft=False, is_archived=True)
        visualization = models.Visualization(query_rel=self.query, type="TABLE", name="Amount", options={})
        models.db.session.add(visualization)
        models.db.session.flush()
        widget = models.Widget(dashboard=self.dashboard, visualization=visualization, width=1,
            options={"position": {"col": 0, "row": 0, "sizeX": 6, "sizeY": 4}})
        models.db.session.add(widget)
        models.db.session.flush()
        self.dashboard.layout = [[widget.id]]
        models.db.session.commit()
        self.ids = {name: getattr(self, name).id for name in ("owner", "editor", "reader", "admin", "group", "other_group", "source", "query", "result", "dashboard", "draft", "archived")}
        self.queue = Queue(self.source.queue_name, connection=rq_redis_connection)
        self.queue.empty()

    def make_request(self, *args, **kwargs):
        from flask import g
        g.pop("org", None)
        return super().make_request(*args, **kwargs)

    def actor(self, name):
        return models.db.session.get(models.User, self.ids[name])

    def board(self, name="dashboard"):
        return models.db.session.get(models.Dashboard, self.ids[name])

    def access(self, actor="owner", board="dashboard"):
        return self.make_request("get", f"/api/dashboards/{self.ids[board]}/access", user=self.actor(actor))

    def save(self, groups=None, users=None, actor="owner", board="dashboard", revision=None, extra=None):
        body = {"revision": self.board(board).version if revision is None else revision,
                "read_user_ids": [] if users is None else users}
        if groups is not None:
            body["read_group_ids"] = groups
        body.update(extra or {})
        with patch("redash.security.csrf.protect"):
            return self.make_request("post", f"/api/dashboards/{self.ids[board]}/access", data=body, user=self.actor(actor))

    def dashboard_get(self, actor="reader", board="dashboard"):
        return self.make_request("get", f"/api/dashboards/{self.ids[board]}", user=self.actor(actor))

    def group_list(self, actor="admin", group="group", query=""):
        return self.make_request("get", f"/api/groups/{self.ids[group]}/dashboards{query}", user=self.actor(actor))

    def group_toggle(self, assigned, actor="admin", group="group", board="dashboard", revision=None):
        body = {"dashboard_id": self.ids[board], "revision": self.board(board).version if revision is None else revision,
                "assigned": assigned}
        with patch("redash.security.csrf.protect"):
            return self.make_request("post", f"/api/groups/{self.ids[group]}/dashboards", user=self.actor(actor), data=body)

    def test_group_grants_show_editor_and_reader_without_direct_user_rows(self):
        self.assertEqual(self.dashboard_get().status_code, 403)
        saved = self.save([self.ids["group"]])
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json["read_group_ids"], [self.ids["group"]])
        self.assertEqual(models.DashboardReadGrant.query.count(), 0)
        for actor in ("editor", "reader"):
            response = self.dashboard_get(actor)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json["can_edit"])
            self.assertFalse(response.json["can_manage_access"])
            listing = self.make_request("get", "/api/dashboards?q=Group&page_size=1", user=self.actor(actor))
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.json["count"], 1)

    def test_membership_removal_revokes_direct_results_and_listing(self):
        self.assertEqual(self.save([self.ids["group"]]).status_code, 200)
        result_path = f"/api/query_results/{self.ids['result']}.csv"
        self.assertEqual(self.make_request("get", result_path, user=self.actor("reader")).status_code, 200)
        self.actor("reader").group_ids = [g for g in self.actor("reader").group_ids if g != self.ids["group"]]
        models.db.session.commit()
        self.assertEqual(self.dashboard_get().status_code, 403)
        self.assertEqual(self.make_request("get", result_path, user=self.actor("reader")).status_code, 403)
        self.assertEqual(self.dashboard_get("editor").status_code, 200)

    def test_group_visibility_never_grants_source_access(self):
        self.assertEqual(self.save([self.ids["group"]]).status_code, 200)
        self.actor("reader").group_ids = [self.ids["group"]]
        models.db.session.commit()
        response = self.dashboard_get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["widgets"][0]["restricted"])
        self.assertEqual(self.make_request("get", f"/api/query_results/{self.ids['result']}", user=self.actor("reader")).status_code, 403)

    def test_old_client_preserves_groups_and_group_revoke_preserves_direct_user(self):
        self.assertEqual(self.save([self.ids["group"]], users=[self.ids["reader"]]).status_code, 200)
        self.assertEqual(self.save(users=[self.ids["reader"]]).status_code, 200)
        self.assertEqual(self.access().json["read_group_ids"], [self.ids["group"]])
        self.assertEqual(self.group_toggle(False).status_code, 200)
        self.assertEqual(self.access().json["read_user_ids"], [self.ids["reader"]])
        self.assertEqual(self.dashboard_get().status_code, 200)
        self.assertEqual(self.dashboard_get("editor").status_code, 403)

    def test_group_side_and_dashboard_side_use_same_rows_and_versions(self):
        start = self.board().version
        granted = self.group_toggle(True)
        self.assertEqual(granted.status_code, 200, granted.json)
        self.assertEqual(self.access().json["read_group_ids"], [self.ids["group"]])
        self.assertGreater(self.board().version, start)
        self.assertEqual(self.group_toggle(False, revision=start).status_code, 409)
        listing = self.group_list(query="?q=Group%20report&page=1&page_size=1")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json["results"]), 1)
        self.assertEqual(listing.json["page_size"], 1)
        row = next(d for d in listing.json["results"] if d["id"] == self.ids["dashboard"])
        self.assertTrue(row["assigned"])
        self.assertEqual(self.save([]).status_code, 200)
        self.assertFalse(next(d for d in self.group_list().json["results"] if d["id"] == self.ids["dashboard"])["assigned"])

    def test_nonadmins_cannot_use_group_side_or_manage_other_owners_grants(self):
        for actor in ("owner", "editor", "reader"):
            self.assertEqual(self.group_list(actor=actor).status_code, 403)
            self.assertEqual(self.group_toggle(True, actor=actor).status_code, 403)
        for actor in ("editor", "reader"):
            self.assertEqual(self.save([self.ids["group"]], actor=actor).status_code, 403)
        self.assertEqual(self.save([self.ids["group"]], actor="admin").status_code, 200)

    def test_invalid_foreign_and_duplicate_group_ids_are_rejected(self):
        foreign = self.factory.create_group(org=self.factory.create_org())
        models.db.session.commit()
        for ids in ([foreign.id], [self.ids["group"], self.ids["group"]], [True], [0], ["1"], [2147483647]):
            with self.subTest(ids=ids):
                self.assertEqual(self.save(ids).status_code, 400)
        self.assertEqual(models.DashboardGroupReadGrant.query.count(), 0)
        foreign_list = self.make_request("get", f"/api/groups/{foreign.id}/dashboards", user=self.actor("admin"))
        self.assertEqual(foreign_list.status_code, 404)
        candidate_ids = {g["id"] for g in self.access().json["group_candidates"]}
        self.assertNotIn(foreign.id, candidate_ids)

    def test_drafts_and_archives_cannot_receive_new_grants(self):
        for name in ("draft", "archived"):
            self.assertEqual(self.save([self.ids["group"]], board=name).status_code, 400)
            self.assertEqual(self.group_toggle(True, board=name).status_code, 400)

    def test_group_deletion_cascades_grant_and_revokes_visibility(self):
        self.assertEqual(self.save([self.ids["group"]]).status_code, 200)
        deleted = self.make_request("delete", f"/api/groups/{self.ids['group']}", user=self.actor("admin"))
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(models.DashboardGroupReadGrant.query.count(), 0)
        self.assertEqual(self.dashboard_get().status_code, 403)

    def test_group_removal_before_worker_execution_blocks_queued_refresh(self):
        self.assertEqual(self.save([self.ids["group"]]).status_code, 200)
        queued = self.make_request("post", f"/api/queries/{self.ids['query']}/results", user=self.actor("reader"),
            data={"dashboard_id": self.ids["dashboard"], "max_age": 0, "parameters": {}})
        self.assertEqual(queued.status_code, 200, queued.json)
        job_id = queued.json["job"]["id"]
        before = models.QueryResult.query.count()
        self.actor("reader").group_ids = [g for g in self.actor("reader").group_ids if g != self.ids["group"]]
        models.db.session.commit()
        SimpleWorker([self.queue], connection=rq_redis_connection, job_class=self.queue.job_class).work(
            burst=True, with_scheduler=False, logging_level="ERROR")
        self.assertEqual(models.QueryResult.query.count(), before)
        self.assertEqual(self.make_request("get", f"/api/jobs/{job_id}", user=self.actor("reader")).status_code, 403)

    def test_boolean_assignment_and_cross_org_dashboard_are_rejected(self):
        self.assertEqual(self.group_toggle(1).status_code, 400)
        foreign_org = self.factory.create_org()
        foreign = self.factory.create_dashboard(org=foreign_org, user=self.factory.create_user(org=foreign_org), is_draft=False)
        models.db.session.commit()
        with patch("redash.security.csrf.protect"):
            response = self.make_request("post", f"/api/groups/{self.ids['group']}/dashboards", user=self.actor("admin"),
                data={"dashboard_id": foreign.id, "revision": foreign.version, "assigned": True})
        self.assertEqual(response.status_code, 404)

    def test_group_mutation_requires_csrf_and_preserves_another_group(self):
        from werkzeug.exceptions import Forbidden
        with patch("redash.security.csrf.protect", side_effect=Forbidden("Missing CSRF token")):
            response = self.make_request("post", f"/api/groups/{self.ids['group']}/dashboards", user=self.actor("admin"),
                data={"dashboard_id": self.ids["dashboard"], "revision": self.board().version, "assigned": True})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.DashboardGroupReadGrant.query.count(), 0)
        self.assertEqual(self.save([self.ids["group"], self.ids["other_group"]]).status_code, 200)
        self.assertEqual(self.group_toggle(False).status_code, 200)
        self.assertEqual(self.access().json["read_group_ids"], [self.ids["other_group"]])

    def test_unpublished_and_archived_reduction_preserves_existing_grants(self):
        for archived in (False, True):
            with self.subTest(archived=archived):
                self.board().is_draft = False
                self.board().is_archived = False
                models.db.session.commit()
                self.assertEqual(self.save([self.ids["group"], self.ids["other_group"]], users=[self.ids["reader"]]).status_code, 200)
                self.board().is_draft = not archived
                self.board().is_archived = archived
                models.db.session.commit()
                reduced = self.save([self.ids["other_group"]], users=[self.ids["reader"]])
                self.assertEqual(reduced.status_code, 200, reduced.json)
                self.assertEqual(reduced.json["read_group_ids"], [self.ids["other_group"]])
                self.assertEqual(reduced.json["read_user_ids"], [self.ids["reader"]])
                # The same unpublished board still cannot receive a new audience.
                self.assertEqual(self.save([self.ids["group"], self.ids["other_group"]], users=[self.ids["reader"]]).status_code, 400)
                self.assertEqual(self.dashboard_get().status_code, 403)

    def test_group_dashboard_pagination_rejects_malformed_values(self):
        for query in ("?page=abc", "?page_size=abc", "?page=0", "?page=-1", "?page_size=0", "?page_size=101", "?page=1.5"):
            with self.subTest(query=query):
                self.assertEqual(self.group_list(query=query).status_code, 400)

    def test_effective_access_explains_group_and_direct_paths(self):
        self.assertEqual(self.save([self.ids["group"]], users=[self.ids["reader"]]).status_code, 200)

        def report_board(actor):
            response = self.make_request("get", f"/api/users/{self.ids[actor]}/effective-access", user=self.actor("admin"))
            self.assertEqual(response.status_code, 200)
            return next(row for row in response.json["dashboards"] if row["id"] == self.ids["dashboard"])

        reader = report_board("reader")
        self.assertTrue(reader["view"]["allowed"])
        self.assertIn("Explicit viewer assignment.", reader["view"]["reason"])
        self.assertIn("Via groups: Report audience.", reader["view"]["reason"])
        editor = report_board("editor")
        self.assertTrue(editor["view"]["allowed"])
        self.assertIn("Via groups: Report audience.", editor["view"]["reason"])
        self.assertNotIn("Explicit viewer assignment.", editor["view"]["reason"])
        self.actor("reader").group_ids = [g for g in self.actor("reader").group_ids if g != self.ids["group"]]
        models.db.session.commit()
        reader = report_board("reader")
        self.assertTrue(reader["view"]["allowed"])
        self.assertIn("Explicit viewer assignment.", reader["view"]["reason"])
        self.assertNotIn("Via groups:", reader["view"]["reason"])
