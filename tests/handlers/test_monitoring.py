"""Safe organization monitoring; never initialize BaseTestCase on live metadata."""
import datetime
import json
import os
import time
import uuid
from unittest.mock import patch

from redis.exceptions import ConnectionError as RedisConnectionError
from rq import SimpleWorker
from sqlalchemy.engine import make_url

from redash import models, redis_connection, rq_redis_connection
from redash.models.users import LAST_ACTIVE_KEY
from redash.tasks.worker import Queue
from redash.utils import utcnow
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class TestMonitoring(BaseTestCase):
    def setUp(self):
        url = make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unset"))
        if not (url.database or "").startswith("bidash_access_app_test"):
            self.skipTest("Requires explicitly disposable metadata database")
        super().setUp()
        from redash import operational_monitoring
        self.monitor = operational_monitoring
        self.admin = self.factory.create_user(details={"role": "admin"})
        self.editor = self.factory.create_user(details={"role": "editor"})
        self.reader = self.factory.create_user(details={"role": "reader"})
        self.disabled = self.factory.create_user(details={"role": "admin"}, disabled_at=utcnow())
        self.other_org = self.factory.create_org()
        self.foreign = self.factory.create_admin(org=self.other_org, details={"role": "admin"}, name="FOREIGN_NAME_MARKER")
        self.source = self.factory.data_source
        self.source.type = "pg"
        self.source.queue_name = "monitoring_synthetic_test"
        self.source.options = ConfigurationContainer({"host": url.host, "port": url.port or 5432,
            "dbname": url.database, "user": url.username, "password": url.password})
        self.query = self.factory.create_query(user=self.admin, data_source=self.source,
            query_text="SELECT 42 AS amount", name="Synthetic monitoring query", is_draft=False)
        models.db.session.commit()
        self.queue = Queue(self.source.queue_name, connection=rq_redis_connection)
        self.queue.empty()
        self.created_jobs = []

    def tearDown(self):
        if hasattr(self, "created_jobs"):
            for job in self.created_jobs:
                try:
                    job.delete(remove_from_queue=True)
                except Exception:
                    pass
        if hasattr(self, "app_ctx"):
            super().tearDown()

    def make_request(self, *args, **kwargs):
        from flask import g
        g.pop("org", None)
        return super().make_request(*args, **kwargs)

    def report(self, user=None, params="", org=None):
        return self.make_request("get", "/api/admin/monitoring" + params,
            user=user or self.admin, org=org or self.factory.org)

    def presence(self, user=None, tab=None, active=True):
        with patch("redash.security.csrf.protect"):
            return self.make_request("post", "/api/session/presence", user=user or self.editor,
                data={"tab_id": tab or str(uuid.uuid4()), "active": active})

    def enqueue(self, query=None, meta=None, register=True):
        from redash.tasks.queries.execution import execute_query
        metadata = {"org_id": self.factory.org.id, "data_source_id": self.source.id,
            "query_id": self.query.id, "user_id": self.admin.id, "is_api_key": False,
            "scheduled": False, "monitoring_api": False, "policy_scope": "SCOPE_SECRET_MARKER", "parameters": "PARAM_SECRET_MARKER"}
        metadata.update(meta or {})
        job = self.queue.enqueue(execute_query, query or self.query.query_text, self.source.id,
            {"query_id": self.query.id}, user_id=self.admin.id, meta=metadata, result_ttl=600)
        self.created_jobs.append(job)
        if register:
            self.monitor.register_job(self.factory.org.id, job.id)
        return job

    def job_row(self, job_id, payload=None):
        payload = payload or self.report().json
        return next(row for row in payload["jobs"]["results"] if row["id"] == job_id)

    def user_row(self, user, payload=None):
        payload = payload or self.report().json
        return next(row for row in payload["users"]["results"] if row["id"] == user.id)

    def test_only_same_org_active_human_admin_can_monitor(self):
        self.assertEqual(self.report().status_code, 200)
        for user in (self.editor, self.reader, self.disabled):
            self.assertIn(self.report(user=user).status_code, (401, 403, 404))
        self.assertIn(self.report(org=self.other_org).status_code, (403, 404))
        response = self.report()
        self.assertNotIn("FOREIGN_NAME_MARKER", response.get_data(as_text=True))
        self.assertNotIn(self.disabled.id, [row["id"] for row in response.json["users"]["results"]])

    def test_api_tokens_cannot_monitor_or_send_presence(self):
        anonymous = self.app.test_client()
        prefix = "/" + self.factory.org.slug
        for key in (self.query.api_key, self.admin.api_key):
            for path in ("/api/admin/monitoring", "/api/session/presence"):
                if path.endswith("presence"):
                    response = anonymous.post(prefix + path + "?api_key=" + key,
                        json={"tab_id": str(uuid.uuid4()), "active": True})
                else:
                    response = anonymous.get(prefix + path + "?api_key=" + key)
                self.assertNotEqual(response.status_code, 200)

    def test_nonadmins_still_cannot_use_raw_instance_endpoints(self):
        # Organization Admin does not imply installation-wide capability.
        self.assertFalse(self.admin.has_permission("super_admin"))
        for actor in (self.admin, self.editor, self.reader):
            self.assertEqual(self.make_request("get", "/status.json", org=False, user=actor).status_code, 403)
            self.assertEqual(self.make_request("get", "/api/admin/queries/rq_status", org=False, user=actor).status_code, 403)

    def test_job_output_is_allowlisted_and_foreign_jobs_are_filtered(self):
        job = self.enqueue(meta={"token": "TOKEN_SECRET_MARKER", "password": "PASSWORD_SECRET_MARKER"})
        foreign = self.enqueue(meta={"org_id": self.other_org.id, "query_name": "FOREIGN_JOB_MARKER"})
        response = self.report()
        self.assertEqual(response.status_code, 200)
        row = self.job_row(job.id, response.json)
        self.assertEqual(row["query_id"], self.query.id)
        self.assertEqual(row["query_name"], self.query.name)
        self.assertEqual(row["state"], "queued")
        self.assertNotIn(foreign.id, [item["id"] for item in response.json["jobs"]["results"]])
        body = response.get_data(as_text=True)
        for forbidden in (self.query.query_text, self.admin.api_key, "SCOPE_SECRET_MARKER", "PARAM_SECRET_MARKER",
                          "TOKEN_SECRET_MARKER", "PASSWORD_SECRET_MARKER", "FOREIGN_JOB_MARKER", "FOREIGN_NAME_MARKER"):
            self.assertNotIn(forbidden, body)
        self.assertEqual(response.json["workers"], [])

    def test_real_synthetic_worker_changes_queued_job_to_completed(self):
        job = self.enqueue()
        self.assertEqual(self.job_row(job.id)["state"], "queued")
        SimpleWorker([self.queue], connection=rq_redis_connection, job_class=self.queue.job_class).work(
            burst=True, with_scheduler=False, logging_level="ERROR")
        job.refresh()
        self.assertEqual(job.get_status().value, "finished")
        self.assertIn(self.job_row(job.id)["state"], ("finished", "completed"))
        result = models.QueryResult.query.order_by(models.QueryResult.id.desc()).first()
        self.assertEqual(result.data["rows"], [{"amount": 42}])

    def test_started_failed_cancelled_states_do_not_expose_errors(self):
        job = self.enqueue()
        job.started_at = utcnow() - datetime.timedelta(seconds=600)
        job.set_status("started")
        job.save()
        row = self.job_row(job.id)
        self.assertIn(row["state"], ("started", "running"))
        self.assertGreaterEqual(row["runtime_seconds"], 590)
        self.assertTrue(row["long_running"])
        job.set_status("failed")
        rq_redis_connection.hset(job.key, "exc_info", "SQL_AND_PASSWORD_ERROR_MARKER")
        response = self.report()
        self.assertEqual(self.job_row(job.id, response.json)["state"], "failed")
        self.assertNotIn("SQL_AND_PASSWORD_ERROR_MARKER", response.get_data(as_text=True))
        job.set_status("canceled")
        self.assertIn(self.job_row(job.id)["state"], ("cancelled", "canceled"))

    def test_presence_merges_tabs_and_preserves_recent_activity_distinction(self):
        self.assertEqual(self.presence(active=False).status_code, 200)
        self.assertEqual(self.user_row(self.editor)["presence"], "idle")
        tab = str(uuid.uuid4())
        self.assertEqual(self.presence(tab=tab, active=True).status_code, 200)
        self.assertEqual(self.user_row(self.editor)["presence"], "active")
        # A second idle tab must not hide another fresh active tab.
        self.assertEqual(self.presence(active=False).status_code, 200)
        response = self.report()
        rows = [row for row in response.json["users"]["results"] if row["id"] == self.editor.id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["presence"], "active")

    def test_presence_requires_csrf_and_rejects_bad_or_foreign_payloads(self):
        response = self.make_request("post", "/api/session/presence", user=self.editor,
            data={"tab_id": str(uuid.uuid4()), "active": True})
        self.assertEqual(response.status_code, 400)
        with patch("redash.security.csrf.protect"):
            for body in ({"tab_id": "not-a-uuid", "active": True},
                         {"tab_id": str(uuid.uuid4()), "active": "true"},
                         {"tab_id": str(uuid.uuid4()), "active": True, "user_id": self.admin.id}):
                response = self.make_request("post", "/api/session/presence", user=self.editor, data=body)
                self.assertEqual(response.status_code, 400)
            response = self.make_request("post", "/api/session/presence", org=self.other_org,
                user=self.editor, data={"tab_id": str(uuid.uuid4()), "active": True})
            self.assertIn(response.status_code, (403, 404))

    def test_monitoring_poll_and_heartbeat_do_not_inflate_last_active(self):
        redis_connection.hdel(LAST_ACTIVE_KEY, self.admin.id, self.editor.id)
        self.assertEqual(self.report().status_code, 200)
        self.assertEqual(self.presence().status_code, 200)
        self.assertIsNone(redis_connection.hget(LAST_ACTIVE_KEY, self.admin.id))
        self.assertIsNone(redis_connection.hget(LAST_ACTIVE_KEY, self.editor.id))

    def test_pagination_is_bounded_and_filters_before_counting(self):
        response = self.report(params="?users_page=1&jobs_page=1&page_size=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["users"]["results"]), 1)
        self.assertGreaterEqual(response.json["users"]["total"], 3)
        self.assertNotIn("FOREIGN_NAME_MARKER", response.get_data(as_text=True))
        self.assertEqual(self.report(params="?page_size=0").status_code, 400)
        self.assertEqual(self.report(params="?jobs_page=-1").status_code, 400)

    def test_redis_failure_is_unavailable_not_a_healthy_empty_job_list(self):
        # Both telemetry connections are unavailable; no credentials/error string may leak.
        with patch.object(self.monitor.redis_connection, "hmget", side_effect=RedisConnectionError("PRIVATE_REDIS_URL_MARKER")), \
             patch.object(self.monitor.rq_redis_connection, "zrevrangebyscore", side_effect=RedisConnectionError("PRIVATE_REDIS_URL_MARKER")):
            response = self.report()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["health"]["jobs"], "unavailable")
        self.assertNotIn("PRIVATE_REDIS_URL_MARKER", response.get_data(as_text=True))

    def test_presence_ttl_and_expired_tab_are_not_reported_online(self):
        self.monitor.record_presence(self.factory.org, self.editor, "synthetic-session", str(uuid.uuid4()), True)
        key = self.monitor.presence_key(self.factory.org.id, self.editor.id)
        entries = redis_connection.zrange(key, 0, -1)
        self.assertEqual(len(entries), 1)
        member = entries[0].decode() if isinstance(entries[0], bytes) else entries[0]
        tab_key = self.monitor.tab_key(self.factory.org.id, self.editor.id, member)
        self.assertGreater(redis_connection.ttl(tab_key), 0)
        self.assertLessEqual(redis_connection.ttl(tab_key), self.monitor.PRESENCE_TTL)
        redis_connection.delete(tab_key)
        redis_connection.zadd(key, {member: time.time() - self.monitor.PRESENCE_TTL - 1})
        self.assertEqual(self.user_row(self.editor)["presence"], "stale")
        redis_connection.delete(key)
        self.assertEqual(self.user_row(self.editor)["presence"], "unknown")

    def test_last_request_activity_merges_without_inventing_presence(self):
        self.editor.active_at = utcnow() - datetime.timedelta(minutes=20)
        models.db.session.commit()
        now = int(time.time())
        redis_connection.hset(LAST_ACTIVE_KEY, self.editor.id, now)
        row = self.user_row(self.editor)
        self.assertEqual(row["presence"], "unknown")
        timestamp = datetime.datetime.fromisoformat(row["last_activity_at"])
        self.assertAlmostEqual(timestamp.timestamp(), now, delta=2)

    def test_api_scheduled_reader_and_legacy_job_attribution(self):
        api = self.enqueue(meta={"is_api_key": True, "user_id": self.admin.id})
        scheduled = self.enqueue(meta={"scheduled": True, "is_api_key": True, "user_id": None})
        reader = self.enqueue(meta={"reader_report": {"private": "READER_SCOPE_SECRET"}, "user_id": self.reader.id})
        legacy = self.enqueue(meta={"is_api_key": None})
        data = self.report().json
        self.assertEqual(self.job_row(api.id, data)["trigger"], "api")
        self.assertEqual(self.job_row(api.id, data)["user_name"], "API")
        self.assertEqual(self.job_row(scheduled.id, data)["trigger"], "scheduled")
        self.assertEqual(self.job_row(reader.id, data)["trigger"], "reader_report")
        self.assertEqual(self.job_row(reader.id, data)["user_name"], self.reader.name)
        self.assertEqual(self.job_row(legacy.id, data)["user_name"], "Unknown")
        self.assertNotIn("READER_SCOPE_SECRET", json.dumps(data))

    def test_duplicate_registration_is_one_job_and_missing_jobs_are_partial(self):
        job = self.enqueue()
        self.monitor.register_job(self.factory.org.id, job.id)
        self.monitor.register_job(self.factory.org.id, str(uuid.uuid4()))
        data = self.report().json
        self.assertEqual(sum(row["id"] == job.id for row in data["jobs"]["results"]), 1)
        self.assertEqual(data["health"]["jobs"], "partial")

    def test_non_query_background_jobs_are_not_reported(self):
        from operator import add
        job = self.queue.enqueue(add, 1, 2, meta={"org_id": self.factory.org.id,
            "data_source_id": self.source.id, "user_id": self.admin.id})
        self.created_jobs.append(job)
        self.monitor.register_job(self.factory.org.id, job.id)
        self.assertNotIn(job.id, [row["id"] for row in self.report().json["jobs"]["results"]])

    def test_browser_cookie_with_api_credentials_cannot_send_presence_or_monitor(self):
        self.assertEqual(self.report().status_code, 200)
        prefix = "/" + self.factory.org.slug
        response = self.client.get(prefix + "/api/admin/monitoring", headers={"Authorization": "Key " + self.admin.api_key})
        self.assertEqual(response.status_code, 403)
        with patch("redash.security.csrf.protect"):
            response = self.client.post(prefix + "/api/session/presence?api_key=" + self.admin.api_key,
                json={"tab_id": str(uuid.uuid4()), "active": True})
        self.assertEqual(response.status_code, 403)

    def test_safe_org_endpoint_does_not_expose_unrelated_instance_workers(self):
        worker = {"name": "FOREIGN_WORKER_NAME", "state": "idle", "last_heartbeat": None,
                  "stale": True, "current_job_id": "FOREIGN_JOB_ID"}
        with patch.object(self.monitor, "_worker_data", return_value=({worker["name"]: worker}, False)):
            response = self.report()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["workers"], [])
        self.assertNotIn("FOREIGN_WORKER_NAME", response.get_data(as_text=True))
        self.assertNotIn("FOREIGN_JOB_ID", response.get_data(as_text=True))

    def test_preexisting_queued_query_is_visible_before_index_registration(self):
        job = self.enqueue(register=False)
        row = self.job_row(job.id)
        self.assertEqual(row["state"], "queued")
        self.assertEqual(row["query_id"], self.query.id)

    def test_ad_hoc_query_label_and_queue_wait_without_sql(self):
        job = self.enqueue(meta={"query_id": "adhoc"})
        row = self.job_row(job.id)
        self.assertIsNone(row["query_id"])
        self.assertEqual(row["query_name"], "Ad hoc")
        self.assertGreaterEqual(row["wait_seconds"], 0)
        self.assertNotIn(self.query.query_text, json.dumps(row))

    def test_personal_api_execution_is_attributed_as_api_and_auto_indexed(self):
        anonymous = self.app.test_client()
        prefix = "/" + self.factory.org.slug
        with patch("redash.security.csrf.protect"):
            response = anonymous.post(prefix + f"/api/queries/{self.query.id}/results",
                json={"max_age": 0}, headers={"Authorization": "Key " + self.admin.api_key})
        self.assertEqual(response.status_code, 200, response.json)
        from redash.tasks.worker import Job
        job = Job.fetch(response.json["job"]["id"], connection=rq_redis_connection)
        self.created_jobs.append(job)
        self.assertIs(job.meta["monitoring_api"], True)
        row = self.job_row(job.id)
        self.assertEqual(row["trigger"], "api")
        self.assertEqual(row["user_name"], self.admin.name)
