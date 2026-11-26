"""Real RQ, metadata, and source integration for Dataset-backed saved Queries."""

import os
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from rq import SimpleWorker
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from redash import datasets, models, rq_redis_connection, settings
from redash.data_access_runtime import resolved_policy
from redash.data_access_shared import run_shared_query
from redash.dataset_reports import attach_dataset_report
from redash.tasks.worker import Queue
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class DatasetReportRuntimeTests(BaseTestCase):
    def setUp(self):
        metadata = make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unset"))
        redis = make_url(os.environ.get("REDASH_REDIS_URL", "redis://localhost/0"))
        mysql_host = os.environ.get("BIDASH_DATASET_REPORT_MYSQL_HOST")
        mysql_password = os.environ.get("BIDASH_DATASET_REPORT_MYSQL_PASSWORD")
        if (
            not (metadata.database or "").startswith("bidash_access_app_test")
            or not mysql_host
            or mysql_host not in {"bidash-dataset-qa-mysql", "bidash-datasets-demo-mysql"}
            or metadata.host not in {"bidash-dataset-qa-pg", "bidash-datasets-demo-pg"}
            or redis.host not in {"bidash-dataset-qa-redis", "bidash-datasets-demo-redis"}
            or mysql_password is None
        ):
            self.skipTest("Requires disposable Dataset report metadata, Redis and MySQL fixtures")
        super().setUp()
        self.enterContext(patch.object(settings, "DATASETS_ENABLED", True))

        import MySQLdb

        suffix = uuid4().hex
        source_db = f"dataset_report_{suffix}"
        connection = MySQLdb.connect(host=mysql_host, user="root", passwd=mysql_password, autocommit=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE `{source_db}`")
                self.addCleanup(self.drop_source_fixture, mysql_host, mysql_password, source_db)
                cursor.execute(
                    f"CREATE TABLE `{source_db}`.sales (region VARCHAR(10), amount DECIMAL(14,4), cost DECIMAL(14,4))"
                )
                cursor.execute(f"INSERT INTO `{source_db}`.sales VALUES ('West', 10.25, 3), ('West', 5.50, 2)")
        finally:
            connection.close()

        owner = self.factory.create_user(details={"role": "editor"})
        editor = self.factory.create_user(details={"role": "editor"})
        reader = self.factory.create_user(details={"role": "reader"})
        group = self.factory.create_group(name="Dataset QA group")
        models.db.session.flush()
        editor.group_ids = [*editor.group_ids, group.id]
        self.editor_id = editor.id
        self.reader_id = reader.id
        self.group_id = group.id
        source = self.factory.create_data_source(
            type="mysql", group=self.factory.default_group,
            options=ConfigurationContainer({
                "host": mysql_host, "port": 3306, "db": source_db,
                "user": "root", "passwd": mysql_password,
            }),
        )
        source.queue_name = f"dataset_report_{suffix}"
        self.queue = Queue(source.queue_name, connection=rq_redis_connection)
        base = self.factory.create_query(
            user=owner, data_source=source, query_text="SELECT region, amount, cost FROM sales",
        )
        fields = {
            "region": {"field": "region"},
            "profit": {"op": "-", "left": {"field": "amount"}, "right": {"field": "cost"}},
        }
        dataset = datasets.create_dataset(owner, base.id, "Sales", fields)
        models.db.session.flush()
        published = datasets.publish_dataset(dataset.id, owner, dataset.draft_version)
        models.db.session.flush()
        models.db.session.add(models.DatasetGroupReadGrant(dataset_id=dataset.id, group_id=group.id))
        self.dataset_id = dataset.id
        self.base_id = base.id
        spec = {
            "group_by": ["region"],
            "metrics": {"total_profit": {"field": "profit", "aggregation": "sum"}},
            "filters": [],
        }
        policy = resolved_policy(source, editor) or {"unrestricted": True}
        compiled, error = run_shared_query(source, policy, base.query_text, dataset_report=(fields, spec, None))
        self.assertIsNone(error)
        report = self.factory.create_query(
            user=editor, data_source=source, query_text=compiled["sql"],
            options={"dataset_report": True, "apply_auto_limit": False},
        )
        attach_dataset_report(report, dataset, published, spec)
        dashboard = self.factory.create_dashboard(user=editor, is_draft=False)
        visualization = self.factory.create_visualization(query_rel=report)
        self.factory.create_widget(dashboard=dashboard, visualization=visualization)
        models.db.session.flush()
        models.db.session.add(models.DashboardReadGrant(
            dashboard_id=dashboard.id, user_id=reader.id, grantor_id=editor.id,
        ))
        self.report_id = report.id
        self.dashboard_id = dashboard.id
        models.db.session.commit()

    @staticmethod
    def drop_source_fixture(host, password, database):
        import MySQLdb

        connection = MySQLdb.connect(host=host, user="root", passwd=password, autocommit=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database}`")
        finally:
            connection.close()

    def work(self):
        SimpleWorker([self.queue], connection=rq_redis_connection, job_class=self.queue.job_class).work(
            burst=True, with_scheduler=False, logging_level="ERROR",
        )
        models.db.session.expire_all()

    def request_as(self, actor_id, method, path, data=None):
        return self.make_request(method, path, user=models.User.get_by_id(actor_id), data=data)

    def refresh(self, actor_id, *, dashboard_id=None, max_age=0):
        body = {"max_age": max_age, "parameters": {}}
        if dashboard_id is not None:
            body["dashboard_id"] = dashboard_id
        return self.request_as(actor_id, "post", f"/api/queries/{self.report_id}/results", body)

    def enqueue(self, actor_id, *, dashboard_id=None):
        response = self.refresh(actor_id, dashboard_id=dashboard_id)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["job"]["id"]

    def result_id(self, actor_id, job_id):
        response = self.request_as(actor_id, "get", f"/api/jobs/{job_id}")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["job"]["status"], 3, response.json)
        return response.json["job"]["query_result_id"]

    def remove_editor_group_membership(self):
        editor = models.User.get_by_id(self.editor_id)
        editor.group_ids = [group_id for group_id in editor.group_ids if group_id != self.group_id]
        models.db.session.commit()

    def assert_denied(self, response):
        self.assertIn(response.status_code, (403, 404), response.json)

    def test_dashboard_matches_source_before_and_after_compatible_publication(self):
        admin = self.factory.create_admin(details={"role": "admin"})
        models.db.session.commit()
        admin_id = admin.id
        report = models.Query.get_by_id(self.report_id)
        expected, error = run_shared_query(
            report.data_source, {"unrestricted": True},
            "SELECT region, SUM(amount - cost) AS total_profit FROM sales GROUP BY region",
        )
        self.assertIsNone(error)

        def check_dashboard_results():
            for actor_id in (admin_id, self.editor_id, self.reader_id):
                job = self.enqueue(actor_id, dashboard_id=self.dashboard_id)
                self.work()
                result_id = self.result_id(actor_id, job)
                result = models.db.session.get(models.QueryResult, result_id)
                self.assertEqual(
                    [(row["region"], Decimal(str(row["total_profit"]))) for row in result.data["rows"]],
                    [(row["region"], row["total_profit"]) for row in expected["rows"]],
                )

        check_dashboard_results()
        admin = models.User.get_by_id(admin_id)
        dataset = models.db.session.get(models.Dataset, self.dataset_id)
        fields = {**dataset.draft["fields"], "cost": {"field": "cost"}}
        edited = datasets.save_dataset_draft(dataset.id, admin, dataset.draft_version, dataset.name, fields)
        datasets.publish_dataset(edited.id, admin, edited.draft_version)
        models.db.session.commit()
        check_dashboard_results()

        self.remove_editor_group_membership()
        self.assert_denied(self.refresh(self.editor_id, dashboard_id=self.dashboard_id, max_age=-1))

    def test_group_revocation_before_worker_blocks_execution_and_poll(self):
        job = self.enqueue(self.editor_id)
        self.remove_editor_group_membership()
        self.work()
        self.assertEqual(models.QueryResult.query.count(), 0)
        self.assert_denied(self.request_as(self.editor_id, "get", f"/api/jobs/{job}"))

    def test_group_grant_revocation_during_execution_discards_source_result(self):
        from redash.dataset_report_runtime import run_dataset_report

        job = self.enqueue(self.editor_id)

        def run_and_revoke(query, user, context):
            result = run_dataset_report(query, user, context)
            self.assertIsNone(result[1])
            with models.db.engine.begin() as transaction:
                transaction.execute(delete(models.DatasetGroupReadGrant).where(
                    models.DatasetGroupReadGrant.dataset_id == self.dataset_id,
                    models.DatasetGroupReadGrant.group_id == self.group_id,
                ))
            return result

        with patch("redash.dataset_report_runtime.run_dataset_report", side_effect=run_and_revoke) as run:
            self.work()
        run.assert_called_once()
        self.assertEqual(models.QueryResult.query.count(), 0)
        self.assert_denied(self.request_as(self.editor_id, "get", f"/api/jobs/{job}"))

    def test_group_revocation_blocks_warm_cache_direct_result_and_export(self):
        job = self.enqueue(self.editor_id)
        self.work()
        result = self.result_id(self.editor_id, job)
        for extension in ("", ".csv"):
            response = self.request_as(self.editor_id, "get", f"/api/query_results/{result}{extension}")
            self.assertEqual(response.status_code, 200, response.json)
        rows = models.db.session.get(models.QueryResult, result).data["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["region"], "West")
        self.assertEqual(Decimal(str(rows[0]["total_profit"])), Decimal("10.75"))
        report = models.Query.get_by_id(self.report_id)
        self.assertIsNone(report.latest_query_data_id)
        self.assertIsNone(models.QueryResult.get_latest(report.data_source, report.query_text, -1))
        warm = self.refresh(self.editor_id, max_age=-1)
        self.assertEqual(warm.status_code, 200, warm.json)
        self.assertEqual(warm.json["query_result"]["id"], result)

        self.remove_editor_group_membership()
        self.assert_denied(self.refresh(self.editor_id, max_age=-1))
        self.assert_denied(self.request_as(self.editor_id, "get", f"/api/jobs/{job}"))
        for extension in ("", ".csv"):
            self.assert_denied(self.request_as(self.editor_id, "get", f"/api/query_results/{result}{extension}"))

    def test_reader_requires_dashboard_context_and_loses_cached_result_after_revocation(self):
        self.assert_denied(self.refresh(self.reader_id))
        editor_job = self.enqueue(self.editor_id)
        self.work()
        editor_result = self.result_id(self.editor_id, editor_job)
        self.assert_denied(self.request_as(self.reader_id, "get", f"/api/query_results/{editor_result}"))
        job = self.enqueue(self.reader_id, dashboard_id=self.dashboard_id)
        self.work()
        result = self.result_id(self.reader_id, job)
        self.assertNotEqual(result, editor_result)
        self.assertEqual(self.request_as(self.reader_id, "get", f"/api/query_results/{result}").status_code, 200)
        warm = self.refresh(self.reader_id, dashboard_id=self.dashboard_id, max_age=-1)
        self.assertEqual(warm.status_code, 200, warm.json)
        self.assertEqual(warm.json["query_result"]["id"], result)
        grant = models.DashboardReadGrant.query.filter_by(dashboard_id=self.dashboard_id, user_id=self.reader_id).one()
        models.db.session.delete(grant)
        models.db.session.commit()
        self.assert_denied(self.refresh(self.reader_id, dashboard_id=self.dashboard_id, max_age=-1))
        self.assert_denied(self.request_as(self.reader_id, "get", f"/api/jobs/{job}"))
        self.assert_denied(self.request_as(self.reader_id, "get", f"/api/query_results/{result}"))
        self.assert_denied(self.request_as(self.reader_id, "get", f"/api/query_results/{result}.csv"))

    def test_view_only_editor_can_refresh_saved_dataset_report_on_dashboard(self):
        report = models.Query.get_by_id(self.report_id)
        for binding in models.DataSourceGroup.query.filter_by(data_source_id=report.data_source_id):
            binding.view_only = True
        models.db.session.commit()

        job = self.enqueue(self.editor_id, dashboard_id=self.dashboard_id)
        self.work()
        result_id = self.result_id(self.editor_id, job)
        warm = self.refresh(self.editor_id, dashboard_id=self.dashboard_id, max_age=-1)
        self.assertEqual(warm.status_code, 200, warm.json)
        self.assertEqual(warm.json["query_result"]["id"], result_id)

    def test_changed_base_revision_blocks_queued_jobs_and_previous_results(self):
        completed = self.enqueue(self.editor_id)
        self.work()
        previous_result = self.result_id(self.editor_id, completed)
        pending = self.enqueue(self.editor_id)
        base = models.Query.get_by_id(self.base_id)
        base.query_text += " WHERE amount > 6"
        models.db.session.commit()
        self.work()
        self.assertEqual(models.QueryResult.query.count(), 1)
        self.assert_denied(self.request_as(self.editor_id, "get", f"/api/jobs/{pending}"))
        self.assert_denied(self.request_as(self.editor_id, "get", f"/api/query_results/{previous_result}"))
