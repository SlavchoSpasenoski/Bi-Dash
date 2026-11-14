"""Reader refresh contracts; requires a disposable metadata/target database."""
import os
from unittest.mock import patch
from rq import SimpleWorker
from sqlalchemy import text
from sqlalchemy.engine import make_url
from redash import models, rq_redis_connection
from redash.tasks.worker import Queue
from redash.tasks.queries.execution import enqueue_query, QueryExecutionError
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class TestReaderDashboardRefresh(BaseTestCase):
    def setUp(self):
        url = make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unset"))
        if not (url.database or "").startswith("bidash_access_app_test"):
            self.skipTest("Requires explicitly disposable database")
        super().setUp()
        self.admin = self.factory.create_admin()
        self.reader = self.factory.create_user(details={"role": "reader"})
        self.source = self.factory.data_source
        self.source.queue_name = "reader_report_test"
        self.source.options = ConfigurationContainer({"host": url.host, "port": url.port or 5432,
            "dbname": url.database, "user": url.username, "password": url.password})
        self.query = self.factory.create_query(user=self.admin, data_source=self.source,
            query_text="SELECT COUNT(*) AS count FROM reader_report_rows WHERE created_at BETWEEN '{{period.start}}' AND '{{period.end}}'",
            options={"parameters": [{"name": "period", "type": "date-range", "value": {"start": "2026-01-01", "end": "2026-01-31"}}]})
        self.dashboard = self.factory.create_dashboard(user=self.admin, is_draft=False)
        self.visualization = self.factory.create_visualization(query_rel=self.query)
        self.widget = self.factory.create_widget(dashboard=self.dashboard, visualization=self.visualization)
        models.db.session.execute(text("DROP TABLE IF EXISTS reader_report_rows"))
        models.db.session.execute(text("CREATE TABLE reader_report_rows(created_at DATE)"))
        models.db.session.execute(text("INSERT INTO reader_report_rows VALUES ('2026-01-15'), ('2026-02-15')"))
        models.db.session.commit()
        self.body = {"dashboard_id": self.dashboard.id, "max_age": 0,
                     "parameters": {"period": {"start": "2026-01-01", "end": "2026-01-31"}}}
        self.queue = Queue(self.source.queue_name, connection=rq_redis_connection)
        self.queue.empty()

    def tearDown(self):
        models.db.session.rollback()
        models.db.session.execute(text("DROP TABLE IF EXISTS reader_report_rows"))
        models.db.session.commit()
        super().tearDown()

    def refresh(self, body=None):
        return self.make_request("post", f"/api/queries/{self.query.id}/results", data=body or self.body, user=self.reader)

    def work(self):
        SimpleWorker([self.queue], connection=rq_redis_connection, job_class=self.queue.job_class).work(
            burst=True, with_scheduler=False, logging_level="ERROR")

    def test_reader_changes_dates_and_forces_fresh_readonly_results(self):
        self.assertFalse(self.reader.has_permission("execute_query"))
        for month in ("01", "02"):
            self.body["parameters"]["period"] = {"start": f"2026-{month}-01", "end": f"2026-{month}-28"}
            response = self.refresh()
            self.assertEqual(response.status_code, 200, response.json)
            job_id = response.json["job"]["id"]
            self.work()
            response = self.make_request("get", f"/api/jobs/{job_id}", user=self.reader)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json["job"]["status"], 3, response.json)
            result_id = response.json["job"]["query_result_id"]
            result = models.db.session.get(models.QueryResult, result_id)
            self.assertEqual(result.data["rows"], [{"count": 1}])
            self.assertEqual(len(result.policy_scope), 64)
            response = self.make_request("get", f"/api/query_results/{result_id}", user=self.reader)
            self.assertEqual(response.status_code, 200)
        models.db.session.expire_all()
        self.assertIsNone(models.Query.get_by_id(self.query.id).latest_query_data_id)

    def test_rejects_hidden_dashboard_unrelated_query_and_untyped_values(self):
        with patch("redash.handlers.query_results.enqueue_query") as enqueue:
            self.dashboard.is_draft = True
            models.db.session.commit()
            self.assertEqual(self.refresh().status_code, 403)
            self.dashboard.is_draft = False
            self.widget.visualization_id = None
            models.db.session.commit()
            self.assertEqual(self.refresh().status_code, 403)
            enqueue.assert_not_called()

    def test_forged_sql_adhoc_and_invalid_parameters_cannot_execute(self):
        bad = {**self.body, "parameters": {"period": {"start": "2026-01-01' OR 1=1 --", "end": "2026-01-31"}}}
        with patch("redash.handlers.query_results.enqueue_query") as enqueue:
            self.assertEqual(self.refresh(bad).status_code, 403)
            enqueue.assert_not_called()
        response = self.make_request("post", "/api/query_results", data={"query": "SELECT 1", "data_source_id": self.source.id}, user=self.reader)
        self.assertEqual(response.status_code, 403)
        with self.assertRaises(QueryExecutionError):
            enqueue_query("SELECT 1", self.source, self.reader.id, metadata={"query_id": self.query.id})

    def test_queued_report_rechecks_source_membership_and_query_changes(self):
        response = self.refresh()
        job_id = response.json["job"]["id"]
        self.query.query_text = "SELECT 99 AS count"
        models.db.session.commit()
        self.work()
        response = self.make_request("get", f"/api/jobs/{job_id}", user=self.reader)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.QueryResult.query.count(), 0)

    def test_source_membership_revocation_blocks_queued_refresh(self):
        response = self.refresh()
        self.reader.group_ids = []
        models.db.session.commit()
        self.work()
        self.assertEqual(models.QueryResult.query.count(), 0)
        response = self.make_request("get", f"/api/jobs/{response.json['job']['id']}", user=self.reader)
        self.assertEqual(response.status_code, 403)

    def test_reader_cannot_edit_sql_or_poll_another_readers_job(self):
        response = self.make_request("post", f"/api/queries/{self.query.id}", data={"query": "SELECT 123"}, user=self.reader)
        self.assertEqual(response.status_code, 403)
        queued = self.refresh()
        other = self.factory.create_user(details={"role": "reader"})
        response = self.make_request("get", f"/api/jobs/{queued.json['job']['id']}", user=other)
        self.assertEqual(response.status_code, 403)

    def test_protected_mysql_reader_refresh_is_real_readonly_execution(self):
        host = os.environ.get("READER_TEST_MYSQL_HOST")
        if host != "bidash-reader-refresh-mysql":
            self.skipTest("Requires isolated Reader MariaDB target")
        import MySQLdb
        target = MySQLdb.connect(host=host, user="root", passwd="disposable-reader-target", autocommit=True)
        try:
            with target.cursor() as cursor:
                cursor.execute("CREATE DATABASE IF NOT EXISTS bidash_reader_reports")
                cursor.execute("DROP TABLE IF EXISTS bidash_reader_reports.reader_report_rows")
                cursor.execute("CREATE TABLE bidash_reader_reports.reader_report_rows(created_at DATE) ENGINE=InnoDB")
                cursor.execute("INSERT INTO bidash_reader_reports.reader_report_rows VALUES ('2026-01-15')")
            self.source.type = "mysql"
            self.source.options = ConfigurationContainer({"host": host, "port": 3306, "db": "bidash_reader_reports",
                                                          "user": "root", "passwd": "disposable-reader-target"})
            org = models.Organization.get_by_id(self.source.org_id)
            org.settings["data_access_protected_sources"] = [self.source.id]
            models.db.session.commit()
            response = self.refresh()
            self.assertEqual(response.status_code, 200, response.json)
            self.work()
            response = self.make_request("get", f"/api/jobs/{response.json['job']['id']}", user=self.reader)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json["job"]["status"], 3, response.json)
            result = models.db.session.get(models.QueryResult, response.json["job"]["query_result_id"])
            self.assertEqual(result.data["rows"], [{"count": 1}])
        finally:
            target.close()
