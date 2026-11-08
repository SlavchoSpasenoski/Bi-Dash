from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rq import SimpleWorker
from sqlalchemy import text
from sqlalchemy.engine import make_url

from redash import models, rq_redis_connection
from redash.query_runner.mysql import Mysql, Result
from redash.tasks.queries.execution import QueryExecutionError, enqueue_query
from redash.tasks.worker import Queue
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class TestAiDashboardExecution(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.editor = self.factory.create_user(details={"role": "editor"})
        url = make_url(self.app.config["SQLALCHEMY_DATABASE_URI"])
        self.source = self.factory.create_data_source(queue_name="ai_dashboard_test", options=ConfigurationContainer({
            "host": url.host, "port": url.port or 5432, "dbname": url.database,
            "user": url.username, "password": url.password,
        }))
        models.db.session.execute(text("DROP TABLE IF EXISTS ai_dashboard_rows"))
        models.db.session.execute(text("CREATE TABLE ai_dashboard_rows AS SELECT generate_series(1,1005) AS value"))
        models.db.session.commit()

    def tearDown(self):
        models.db.session.rollback()
        models.db.session.execute(text("DROP TABLE IF EXISTS ai_dashboard_rows"))
        models.db.session.commit()
        super().tearDown()

    def test_real_generated_query_runs_readonly_without_truncating_results(self):
        query = self.factory.create_query(user=self.editor, data_source=self.source,
            query_text="SELECT value FROM ai_dashboard_rows",
            options={"ai_generated": True, "parameters": []})
        queue = Queue(self.source.queue_name, connection=rq_redis_connection)
        queue.empty()
        job = enqueue_query(query.query_text, self.source, self.editor.id, metadata={"query_id": query.id})
        self.assertEqual(job.timeout, 60)
        self.assertTrue(job.meta["ai_read_only"])
        SimpleWorker([queue], connection=rq_redis_connection, job_class=queue.job_class).work(
            burst=True, with_scheduler=False, logging_level="ERROR")
        job.refresh()
        self.assertIsInstance(job.result, int)
        result = models.db.session.get(models.QueryResult, job.result)
        self.assertEqual(len(result.data["rows"]), 1005)
        models.db.session.expire_all()
        self.assertEqual(models.db.session.get(models.Query, query.id).latest_query_data_id, result.id)

    def test_postgres_transaction_independently_rejects_writes(self):
        runner = self.source.query_runner
        with patch("rq.get_current_job", return_value=SimpleNamespace(timeout=5, meta={"ai_read_only": True})):
            data, error = runner.run_query("DELETE FROM ai_dashboard_rows", self.editor)
        self.assertIsNone(data)
        self.assertIn("read-only", error)
        self.assertEqual(models.db.session.execute(text("SELECT count(*) FROM ai_dashboard_rows")).scalar(), 1005)

    def test_optional_editor_auto_limit_still_limits_results(self):
        runner = self.source.query_runner
        sql = runner.apply_auto_limit("SELECT value FROM ai_dashboard_rows", True)
        with patch("rq.get_current_job", return_value=SimpleNamespace(timeout=60, meta={"ai_read_only": True})):
            data, error = runner.run_query(sql, self.editor)
        self.assertIsNone(error)
        self.assertEqual(len(data["rows"]), 1000)

    def test_generated_query_cannot_bypass_guard_by_direct_api_execution(self):
        query = self.factory.create_query(user=self.editor, data_source=self.source,
            query_text="SELECT pg_read_file('/etc/passwd')", options={"ai_generated": True})
        with self.assertRaises(QueryExecutionError):
            enqueue_query(query.query_text, self.source, self.editor.id, metadata={"query_id": query.id})

    def test_mysql_thread_starts_a_readonly_transaction_before_query(self):
        runner = Mysql(ConfigurationContainer({"dbname": "test"}))
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.description = [("value", 3)]
        cursor.fetchall.return_value = [(i,) for i in range(1005)]
        cursor.nextset.return_value = None
        result, event = Result(), Event()
        runner._run_query("SELECT 1 AS value", self.editor, connection, result, event, True)
        self.assertEqual(cursor.execute.call_args_list[0].args[0], "START TRANSACTION READ ONLY")
        self.assertEqual(cursor.execute.call_args_list[1].args[0], "SELECT 1 AS value")
        cursor.fetchall.assert_called_once()
        cursor.fetchmany.assert_not_called()
        self.assertEqual(len(result.data["rows"]), 1005)
        self.assertIsNone(result.error)
        connection.close.assert_called_once()
        self.assertTrue(event.is_set())
