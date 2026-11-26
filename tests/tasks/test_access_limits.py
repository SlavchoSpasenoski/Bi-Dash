import time
from types import SimpleNamespace
from unittest.mock import patch

from rq import SimpleWorker
from sqlalchemy.engine import make_url

from redash import models, rq_redis_connection
from redash.query_runner.pg import PostgreSQL
from redash.tasks.queries.execution import QueryExecutionError, enqueue_query
from redash.tasks.worker import Queue
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class TestRealQueryLimits(BaseTestCase):
    def postgres_source(self):
        url = make_url(self.app.config["SQLALCHEMY_DATABASE_URI"])
        source = self.factory.create_data_source(
            queue_name="access_limits_test",
            group=self.factory.default_group,
            options=ConfigurationContainer({
                "host": url.host, "port": url.port or 5432, "dbname": url.database,
                "user": url.username, "password": url.password,
            }))
        models.db.session.commit()
        return source

    def test_postgres_statement_timeout_cancels_server_query(self):
        source = self.postgres_source()
        runner = source.query_runner
        started = time.monotonic()
        with patch("rq.get_current_job", return_value=SimpleNamespace(timeout=1)):
            data, error = runner.run_query("SELECT pg_sleep(5) /* access-statement-test */", self.factory.user)
        self.assertIsNone(data)
        self.assertIn("statement timeout", error)
        self.assertLess(time.monotonic() - started, 4)
        data, error = runner.run_query(
            "SELECT count(*) AS active FROM pg_stat_activity WHERE datname = current_database() AND state='active' "
            "AND query LIKE '%access-statement-test%' AND pid <> pg_backend_pid()", self.factory.user)
        self.assertIsNone(error)
        self.assertEqual(data["rows"][0]["active"], 0)

    def test_real_worker_interrupts_editor_job(self):
        editor = self.factory.create_user(details={"role": "editor", "access_limits": {"query_timeout_seconds": 1}})
        source = self.postgres_source()
        queue = Queue(source.queue_name, connection=rq_redis_connection)
        queue.empty()
        job = enqueue_query("SELECT pg_sleep(5) /* access-worker-test */", source, editor.id,
                            metadata={"query_id": "adhoc"})
        self.assertEqual(job.timeout, 1)
        started = time.monotonic()
        worker = SimpleWorker([queue], connection=rq_redis_connection, job_class=queue.job_class)
        worker.work(burst=True, with_scheduler=False, logging_level="ERROR")
        job.refresh()
        self.assertLess(time.monotonic() - started, 4)
        self.assertIsInstance(job.result, QueryExecutionError)
        self.assertIn("time limit", str(job.result))

    def test_query_key_cannot_execute_anonymously(self):
        editor = self.factory.create_user(details={"role": "editor"})
        query = self.factory.create_query(user=editor)
        with self.assertRaisesRegex(QueryExecutionError, "Anonymous query execution is unavailable"):
            enqueue_query(query.query_text, query.data_source, query.api_key, is_api_key=True,
                          metadata={"query_id": query.id})

    def test_authenticated_queries_share_the_same_user_query_quota(self):
        editor = self.factory.create_user(details={"role": "editor", "access_limits": {"max_concurrent_queries": 1}})
        query = self.factory.create_query(user=editor, query_text="SELECT 112")
        enqueue_query("SELECT 111", query.data_source, editor.id)
        with self.assertRaisesRegex(QueryExecutionError, "Query limit reached"):
            enqueue_query(query.query_text, query.data_source, editor.id, metadata={"query_id": query.id})

    def test_authenticated_query_uses_owners_timeout_and_permissions(self):
        editor = self.factory.create_user(details={"role": "editor", "access_limits": {"query_timeout_seconds": 3}})
        source = self.postgres_source()
        query = self.factory.create_query(user=editor, data_source=source, query_text="SELECT 42 AS value")
        queue = Queue(source.queue_name, connection=rq_redis_connection)
        queue.empty()
        job = enqueue_query(query.query_text, source, editor.id, metadata={"query_id": query.id})
        self.assertEqual(job.timeout, 3)
        worker = SimpleWorker([queue], connection=rq_redis_connection, job_class=queue.job_class)
        worker.work(burst=True, with_scheduler=False, logging_level="ERROR")
        job.refresh()
        self.assertIsInstance(job.result, int)
        result = models.db.session.get(models.QueryResult, job.result)
        self.assertEqual(result.data["rows"], [{"value": 42}])
