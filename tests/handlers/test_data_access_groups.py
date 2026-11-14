"""Shared-account group enforcement; disposable app and target databases only."""
import copy
import os
import uuid
from unittest.mock import patch
from sqlalchemy.engine import make_url
import MySQLdb
from tests import BaseTestCase
from redash import models
from redash.data_access_runtime import access_scope
from redash.tasks.queries.execution import enqueue_query, QueryExecutionError
from redash.utils import gen_query_hash, utcnow
from redash.utils.configuration import ConfigurationContainer


class TestGroupDataAccess(BaseTestCase):
    def make_request(self, *args, **kwargs):
        from flask import g
        g.pop("org", None)
        return super().make_request(*args, **kwargs)

    def setUp(self):
        if not (make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unconfigured")).database or "").startswith("bidash_access_app_test"):
            self.skipTest("Requires explicitly disposable metadata database")
        host = os.environ.get("DATA_ACCESS_TEST_MYSQL_HOST")
        if host not in ("access-test-mysql", "access-test-mariadb"):
            self.skipTest("Requires explicitly isolated MySQL target")
        password = os.environ["DATA_ACCESS_TEST_MYSQL_PASSWORD"]
        super().setUp()
        self.target = MySQLdb.connect(host=host, user="root", passwd=password, autocommit=True)
        with self.target.cursor() as c:
            c.execute("DROP DATABASE IF EXISTS bidash_policy_app_sales")
            c.execute("CREATE DATABASE bidash_policy_app_sales")
            c.execute("CREATE TABLE bidash_policy_app_sales.orders(id INT,amount INT,secret TEXT) ENGINE=InnoDB")
            c.execute("INSERT INTO bidash_policy_app_sales.orders VALUES(1,42,'hidden')")
            c.execute("DROP USER IF EXISTS 'qa_group_shared'@'%'")
            c.execute("CREATE USER 'qa_group_shared'@'%' IDENTIFIED BY 'test-only-password'")
            c.execute("GRANT ALL PRIVILEGES ON *.* TO 'qa_group_shared'@'%'")
        self.admin = self.factory.create_admin()
        self.editor = self.factory.create_user(details={"role": "editor"})
        self.reader = self.factory.create_user(details={"role": "reader"})
        self.other = self.factory.create_user(details={"role": "editor"})
        self.group = self.factory.create_group(name="Allowed sales")
        models.db.session.flush()
        self.editor.group_ids = self.editor.group_ids + [self.group.id]
        # Reader deliberately has no Editor rule group; source/report access remains.
        self.source = models.DataSource.create_with_group(org=self.factory.org, name="Shared read-only",
            type="mysql", queue_name="group_policy_qa_" + uuid.uuid4().hex,
            options=ConfigurationContainer({"host": host, "port":3306,"db":"bidash_policy_app_sales",
                                            "user":"qa_group_shared","passwd":"test-only-password"}))
        models.db.session.commit()
        self.policy = {"id":"sales","name":"Sales","data_source_id":self.source.id,"status":"active",
            "default_effect":"deny","user_ids":[],"group_ids":[self.group.id],"rules":[{
                "catalog":None,"schema":"bidash_policy_app_sales","table":"orders",
                "column_mode":"explicit","columns":["id","amount"]}]}
        self.revision = 0
        self.sql = "SELECT * FROM orders"

    def tearDown(self):
        if hasattr(self, "target"): self.target.close()
        if hasattr(self, "app_ctx"): super().tearDown()

    def save(self, policies=None):
        with patch("redash.handlers.access_policy.csrf.protect"):
            response = self.make_request("post","/api/settings/data-access",user=self.admin,
                data={"revision":self.revision,"policies":[self.policy] if policies is None else policies})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.revision = response.json["revision"]
        return response

    def query(self):
        response = self.make_request("post","/api/queries",user=self.editor,
            data={"name":"Sales report","query":self.sql,"data_source_id":self.source.id,"options":{"parameters":[]}})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        return models.Query.get_by_id(response.json["id"])

    def result(self, user=None, sql=None):
        user, sql = user or self.editor, sql or self.sql
        data, error = self.source.query_runner.run_query(sql,user)
        self.assertIsNone(error)
        result=models.QueryResult.store_result(self.factory.org.id,self.source,gen_query_hash(sql),sql,data,0.1,utcnow(),
                                               policy_scope=access_scope(self.source,user))
        models.db.session.commit()
        return result

    def test_one_connection_save_applies_metadata_and_execution(self):
        before = models.DataSource.query.count()
        self.save()
        self.assertEqual(models.DataSource.query.count(),before)
        schema=self.make_request("get",f"/api/data_sources/{self.source.id}/schema",user=self.editor)
        self.assertEqual(schema.status_code,200,schema.get_data(as_text=True))
        self.assertEqual(schema.json["schema"],[{"name":"bidash_policy_app_sales.orders","columns":["amount","id"]}])
        data,error=self.source.query_runner.run_query(self.sql,self.editor)
        self.assertIsNone(error)
        self.assertEqual({col["name"] for col in data["columns"]},{"id","amount"})
        for sql in ("SELECT secret FROM orders","SELECT id FROM orders WHERE secret='hidden'", "SELECT * FROM information_schema.tables"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                self.source.query_runner.run_query(sql,self.editor)
        self.assertEqual(self.make_request("get",f"/api/data_sources/{self.source.id}/schema",user=self.other).status_code,403)

    def test_results_exports_legacy_admin_scope_and_reduction(self):
        legacy=models.QueryResult.store_result(self.factory.org.id,self.source,gen_query_hash(self.sql),self.sql,
                    {"columns":[{"name":"secret","type":"string"}],"rows":[{"secret":"legacy"}]},0.1,utcnow())
        models.db.session.commit()
        self.save(); query=self.query(); result=self.result()
        self.assertEqual(query.options["data_access_scope"],access_scope(self.source,self.editor))
        for user in (self.editor,self.reader):
            for suffix in ("", ".csv", ".tsv", ".xlsx"):
                response=self.make_request("get",f"/api/query_results/{result.id}{suffix}",user=user)
                self.assertEqual(response.status_code,200,response.get_data()[:200])
            response=self.make_request("get",f"/api/queries/{query.id}/results",user=user)
            self.assertEqual(response.status_code,200,response.get_data()[:200])
        self.assertEqual(self.make_request("get",f"/api/query_results/{result.id}",user=self.admin).status_code,200)
        for user in (self.other,):
            self.assertEqual(self.make_request("get",f"/api/query_results/{result.id}",user=user).status_code,403)
        self.assertEqual(self.make_request("get",f"/api/query_results/{legacy.id}",user=self.editor).status_code,403)
        self.policy["rules"][0]["columns"]=["id"]; self.save()
        self.assertEqual(self.make_request("get",f"/api/query_results/{result.id}",user=self.editor).status_code,403)
        self.assertEqual(self.make_request("get",f"/api/queries/{query.id}",user=self.editor).status_code,403)
        self.assertIsNone(models.QueryResult.get_latest(self.source,self.sql,-1,policy_scope=access_scope(self.source,self.editor)))

    def test_group_union_membership_removal_and_default_deny(self):
        second=self.factory.create_group(name="More sales")
        models.db.session.flush()
        self.editor.group_ids=self.editor.group_ids+[second.id]; models.db.session.commit()
        extra=copy.deepcopy(self.policy); extra.update(id="sales-extra",group_ids=[second.id])
        extra["rules"][0]["columns"]=["secret"]
        self.save([self.policy,extra])
        data,_=self.source.query_runner.run_query(self.sql,self.editor)
        self.assertEqual(len(data["columns"]),3)
        saved=self.result()
        self.editor.group_ids=[gid for gid in self.editor.group_ids if gid!=second.id]; models.db.session.commit()
        self.assertEqual(self.make_request("get",f"/api/query_results/{saved.id}",user=self.editor).status_code,403)
        self.save([])
        self.assertEqual(self.make_request("get",f"/api/data_sources/{self.source.id}/schema",user=self.editor).status_code,403)

    def test_admin_legacy_reports_remain_visible_and_refreshable(self):
        from rq import Queue,SimpleWorker
        from redash import rq_redis_connection
        legacy=self.factory.create_query(data_source=self.source,user=self.editor,query_text=self.sql,
                                         options={"parameters":[],"apply_auto_limit":False})
        result=models.QueryResult.store_result(self.factory.org.id,self.source,gen_query_hash(self.sql),self.sql,
            {"columns":[{"name":"secret","type":"string"}],"rows":[{"secret":"synthetic-legacy"}]},0.1,utcnow())
        legacy.latest_query_data_id=result.id
        dashboard=self.factory.create_dashboard(user=self.editor)
        visualization=self.factory.create_visualization(query_rel=legacy)
        self.factory.create_widget(dashboard=dashboard,visualization=visualization)
        models.db.session.commit()
        self.save()
        response=self.make_request("get","/api/queries?page_size=100",user=self.admin)
        self.assertEqual(response.status_code,200,response.get_data()[:200])
        self.assertIn(legacy.id,[q["id"] for q in response.json["results"]])
        for path in (f"/api/queries/{legacy.id}",f"/api/queries/{legacy.id}/results",
                     f"/api/query_results/{result.id}",f"/api/dashboards/{dashboard.id}"):
            with self.subTest(path=path):
                response=self.make_request("get",path,user=self.admin)
                self.assertEqual(response.status_code,200,response.get_data()[:200])
        self.assertEqual(response.json["widgets"][0]["visualization"]["query"]["latest_query_data_id"],result.id)
        self.assertEqual(self.make_request("get",f"/api/queries/{legacy.id}",user=self.editor).status_code,403)
        self.assertEqual(self.make_request("get",f"/api/query_results/{result.id}",user=self.editor).status_code,403)
        job=enqueue_query(self.sql,self.source,self.admin.id,metadata={"query_id":legacy.id})
        queue=Queue(job.origin,connection=rq_redis_connection)
        SimpleWorker([queue],connection=rq_redis_connection).work(burst=True,with_scheduler=False)
        job.refresh();self.assertTrue(job.is_finished,job.exc_info)
        refreshed=models.QueryResult.query.filter_by(id=job.result).one()
        self.assertEqual(len(refreshed.data["columns"]),3)
        self.assertEqual(refreshed.policy_scope,access_scope(self.source,self.admin))
        self.assertEqual(self.make_request("get",f"/api/query_results/{refreshed.id}",user=self.editor).status_code,403)

    def test_actual_worker_and_queued_rule_change(self):
        from rq import Queue, SimpleWorker
        from redash import rq_redis_connection
        self.save(); query=self.query()
        job=enqueue_query(self.sql,self.source,self.editor.id,metadata={"query_id":query.id})
        queue=Queue(job.origin,connection=rq_redis_connection)
        SimpleWorker([queue],connection=rq_redis_connection).work(burst=True,with_scheduler=False)
        job.refresh(); self.assertTrue(job.is_finished,job.exc_info)
        result=models.QueryResult.query.filter_by(id=job.result).one()
        self.assertEqual({c["name"] for c in result.data["columns"]},{"id","amount"})
        queued=enqueue_query(self.sql+" WHERE id=1",self.source,self.editor.id,metadata={"query_id":query.id})
        self.save([])
        SimpleWorker([queue],connection=rq_redis_connection).work(burst=True,with_scheduler=False)
        queued.refresh(); self.assertIsInstance(queued.result,QueryExecutionError)

    def test_nonadmin_save_csrf_revision_and_removed_activation(self):
        response=self.make_request("post","/api/settings/data-access",user=self.editor,data={"revision":0,"policies":[self.policy]})
        self.assertEqual(response.status_code,403)
        self.save()
        with patch("redash.handlers.access_policy.csrf.protect"):
            response=self.make_request("post","/api/settings/data-access",user=self.admin,data={"revision":0,"policies":[self.policy]})
        self.assertEqual(response.status_code,409)
        self.assertIn(self.make_request("post","/api/settings/data-access/activation",user=self.admin,data={}).status_code,(404,405))

    def test_reader_keeps_legacy_and_broad_reports_without_editor_rules(self):
        legacy=models.QueryResult.store_result(self.factory.org.id,self.source,gen_query_hash(self.sql),self.sql,
            {"columns":[{"name":"secret","type":"string"}],"rows":[{"secret":"legacy"}]},0.1,utcnow())
        models.db.session.commit()
        self.save()
        admin_result=self.result(self.admin)
        query=self.query()
        self.assertEqual(len(admin_result.data["columns"]),3)
        dashboard=self.factory.create_dashboard(user=self.admin)
        visualization=self.factory.create_visualization(query_rel=query)
        self.factory.create_widget(dashboard=dashboard,visualization=visualization)
        models.db.session.commit()
        response=self.make_request("get",f"/api/dashboards/{dashboard.id}",user=self.reader)
        self.assertEqual(response.status_code,200,response.get_data()[:200])
        rendered_query=response.json["widgets"][0]["visualization"]["query"]
        self.assertEqual(rendered_query["latest_query_data_id"],admin_result.id)
        self.assertNotIn("query",rendered_query)
        for result in (legacy,admin_result):
            for suffix in ("", ".csv", ".tsv", ".xlsx"):
                response=self.make_request("get",f"/api/query_results/{result.id}{suffix}",user=self.reader)
                self.assertEqual(response.status_code,200,response.get_data()[:200])
            self.assertEqual(self.make_request("get",f"/api/query_results/{result.id}",user=self.editor).status_code,403)
        with patch("redash.handlers.query_results.enqueue_query") as enqueue:
            response=self.make_request("post",f"/api/queries/{query.id}/results",user=self.reader,data={"max_age":0})
        self.assertEqual(response.status_code,200,response.get_data()[:200])
        enqueue.assert_not_called()
        self.assertNotIn("query",response.json["query_result"])
        preview=self.make_request("get",f"/api/settings/data-access/effective?data_source_id={self.source.id}&user_id={self.reader.id}",user=self.admin)
        self.assertEqual(preview.status_code,200)
        self.assertFalse(preview.json["assigned"])
        with self.assertRaises(QueryExecutionError):
            enqueue_query(self.sql,self.source,self.reader.id)
        self.save([])
        self.assertEqual(self.make_request("get",f"/api/query_results/{admin_result.id}",user=self.reader).status_code,200)
        self.reader.group_ids=[]; models.db.session.commit()
        self.assertEqual(self.make_request("get",f"/api/query_results/{admin_result.id}",user=self.reader).status_code,403)

    def test_unprotected_full_privilege_connection_only_executes_select(self):
        with self.target.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM bidash_policy_app_sales.orders")
            self.assertEqual(cursor.fetchone()[0],1)
        for user in (self.admin,self.editor):
            data,error=self.source.query_runner.run_query(self.sql,user)
            self.assertIsNone(error)
            self.assertEqual(len(data["columns"]),3)
            for sql in ("DELETE FROM orders","UPDATE orders SET amount=99","DROP TABLE orders",
                        "CREATE TABLE forbidden(id INT)","SELECT LOAD_FILE('/etc/passwd')",
                        "SELECT id INTO OUTFILE '/tmp/qa-forbidden' FROM orders"):
                with self.subTest(user=user.id,sql=sql):
                    data,error=self.source.query_runner.run_query(sql,user)
                    self.assertIsNone(data)
                    self.assertTrue(error)
        data,error=self.source.query_runner.run_query(self.sql,self.reader)
        self.assertIsNone(data)
        self.assertTrue(error)
        with self.target.cursor() as cursor:
            cursor.execute("SELECT amount FROM bidash_policy_app_sales.orders")
            self.assertEqual(cursor.fetchall(),((42,),))

    def test_unprotected_full_privilege_actual_worker_select_and_write_denial(self):
        from rq import Queue, SimpleWorker
        from redash import rq_redis_connection
        query=self.query()
        job=enqueue_query(self.sql,self.source,self.editor.id,metadata={"query_id":query.id})
        queue=Queue(job.origin,connection=rq_redis_connection)
        SimpleWorker([queue],connection=rq_redis_connection).work(burst=True,with_scheduler=False)
        job.refresh()
        self.assertTrue(job.is_finished,job.exc_info)
        result=models.QueryResult.query.filter_by(id=job.result).one()
        self.assertEqual(len(result.data["columns"]),3)
        denied=enqueue_query("DELETE FROM orders",self.source,self.admin.id,metadata={"query_id":"adhoc"})
        SimpleWorker([queue],connection=rq_redis_connection).work(burst=True,with_scheduler=False)
        denied.refresh()
        self.assertIsInstance(denied.result,QueryExecutionError)
        with self.target.cursor() as cursor:
            cursor.execute("SELECT amount FROM bidash_policy_app_sales.orders")
            self.assertEqual(cursor.fetchall(),((42,),))
