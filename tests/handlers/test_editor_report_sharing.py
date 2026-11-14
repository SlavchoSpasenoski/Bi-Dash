"""Cross-profile Editor definitions with recipient-scoped execution/results."""
import copy
import os
from unittest.mock import patch
from rq import SimpleWorker
from sqlalchemy.engine import make_url
from redash import models, rq_redis_connection
from redash.data_access_runtime import access_scope
from redash.tasks.worker import Queue
from redash.utils import utcnow
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase


class TestEditorReportSharing(BaseTestCase):
    def setUp(self):
        if not (make_url(os.environ.get("REDASH_DATABASE_URL", "postgresql:///unset")).database or "").startswith("bidash_access_app_test"):
            self.skipTest("Requires explicitly disposable metadata database")
        host = os.environ.get("EDITOR_SHARING_MYSQL_HOST")
        if host != "bidash-report-sharing-mysql":
            self.skipTest("Requires isolated MariaDB target")
        super().setUp()
        import MySQLdb
        self.target = MySQLdb.connect(host=host, user="root", passwd="disposable-sharing-target", autocommit=True)
        with self.target.cursor() as c:
            c.execute("CREATE DATABASE IF NOT EXISTS bidash_report_sharing")
            c.execute("DROP TABLE IF EXISTS bidash_report_sharing.orders")
            c.execute("DROP TABLE IF EXISTS bidash_report_sharing.employees")
            c.execute("CREATE TABLE bidash_report_sharing.orders(id INT,total INT,created_at DATE) ENGINE=InnoDB")
            c.execute("CREATE TABLE bidash_report_sharing.employees(id INT,name VARCHAR(50),salary INT) ENGINE=InnoDB")
            c.execute("INSERT INTO bidash_report_sharing.orders VALUES(1,42,'2026-01-15')")
            c.execute("INSERT INTO bidash_report_sharing.employees VALUES(1,'synthetic',900)")
        self.admin = self.factory.create_admin()
        self.wide = self.factory.create_user(details={"role":"editor"})
        self.narrow = self.factory.create_user(details={"role":"editor"})
        self.gw = self.factory.create_group(name="Wide")
        self.gn = self.factory.create_group(name="Narrow")
        models.db.session.flush()
        self.wide.group_ids = self.wide.group_ids + [self.gw.id]
        self.narrow.group_ids = self.narrow.group_ids + [self.gn.id]
        self.source = self.factory.data_source
        self.source.type = "mysql"
        self.source.queue_name = "editor_sharing_test"
        self.source.options = ConfigurationContainer({"host":host,"port":3306,"db":"bidash_report_sharing","user":"root","passwd":"disposable-sharing-target"})
        rules = [{"catalog":None,"schema":"bidash_report_sharing","table":"orders","column_mode":"explicit","columns":["id","total","created_at"]},
                 {"catalog":None,"schema":"bidash_report_sharing","table":"employees","column_mode":"explicit","columns":["id","name","salary"]}]
        policy = {"id":"wide","name":"Wide","data_source_id":self.source.id,"status":"active","default_effect":"deny","user_ids":[],"group_ids":[self.gw.id],"rules":rules}
        narrower = copy.deepcopy(policy);narrower.update(id="narrow",group_ids=[self.gn.id]);narrower["rules"][1]["columns"].remove("salary")
        self.factory.org.settings["data_access_group_rules"]={"revision":1,"policies":[policy,narrower]}
        self.factory.org.settings["data_access_protected_sources"]=[self.source.id]
        models.db.session.commit()
        self.queue = Queue(self.source.queue_name, connection=rq_redis_connection)
        self.queue.empty()

    def tearDown(self):
        if hasattr(self,"target"):self.target.close()
        super().tearDown()

    def query(self, sql, user=None, parameters=None):
        user = models.User.get_by_id((user or self.wide).id)
        self.factory.org = models.Organization.get_by_id(self.source.org_id)
        self.source = models.DataSource.get_by_id(self.source.id)
        q = self.factory.create_query(user=user,data_source=self.source,query_text=sql,is_draft=False,
            options={"parameters":parameters or [],"data_access_scope":access_scope(self.source,user)})
        models.db.session.commit()
        return q

    def get(self,q,user):
        return self.make_request("get",f"/api/queries/{q.id}",user=user)

    def execute(self,q,user,parameters=None):
        response=self.make_request("post",f"/api/queries/{q.id}/results",data={"parameters":parameters or {},"max_age":0},user=user)
        self.assertEqual(response.status_code,200,response.json)
        job_id=response.json["job"]["id"]
        SimpleWorker([self.queue],connection=rq_redis_connection,job_class=self.queue.job_class).work(burst=True,with_scheduler=False,logging_level="ERROR")
        response=self.make_request("get",f"/api/jobs/{job_id}",user=user)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json["job"]["status"],3,response.json)
        return models.db.session.get(models.QueryResult,response.json["job"]["query_result_id"])

    def test_both_directions_read_explicit_unrelated_columns_but_not_edit(self):
        for owner,viewer in ((self.wide,self.narrow),(self.narrow,self.wide)):
            q=self.query("SELECT id,total FROM orders",owner)
            response=self.get(q,viewer)
            self.assertEqual(response.status_code,200,response.json)
            self.assertFalse(response.json["can_edit"])
            response=self.make_request("post",f"/api/queries/{q.id}",data={"name":"unauthorized edit"},user=viewer)
            self.assertEqual(response.status_code,403)

    def test_all_dependencies_include_predicates_joins_aggregates_and_subqueries(self):
        for sql in ("SELECT salary FROM employees", "SELECT name FROM employees WHERE salary>0",
                    "SELECT SUM(salary) AS total FROM employees", "SELECT name FROM employees ORDER BY salary",
                    "SELECT COUNT(*) AS count FROM employees HAVING SUM(salary)>0",
                    "SELECT o.id FROM orders o JOIN employees e ON o.total=e.salary",
                    "SELECT id FROM orders WHERE total IN (SELECT salary FROM employees)"):
            with self.subTest(sql=sql):
                q=self.query(sql)
                self.assertEqual(self.get(q,self.narrow).status_code,403)
                self.assertEqual(self.get(q,self.wide).status_code,200)
        q=self.query("SELECT COUNT(*) AS count FROM employees")
        self.assertEqual(self.get(q,self.narrow).status_code,200)

    def test_recipient_executes_in_own_scope_and_cannot_read_author_cache(self):
        q=self.query("SELECT id,total FROM orders")
        author_result=self.execute(q,self.wide)
        response=self.get(q,self.narrow)
        self.assertIsNone(response.json["latest_query_data_id"])
        recipient_result=self.execute(q,self.narrow)
        self.assertEqual(recipient_result.data["rows"],[{"id":1,"total":42}])
        self.assertNotEqual(author_result.policy_scope,recipient_result.policy_scope)
        for user,result in ((self.narrow,author_result),(self.wide,recipient_result)):
            for suffix in ("", ".csv"):
                response=self.make_request("get",f"/api/query_results/{result.id}{suffix}",user=user)
                self.assertEqual(response.status_code,403)

    def test_dashboard_restricts_only_forbidden_widget_without_metadata_leak(self):
        allowed=self.query("SELECT id,total FROM orders")
        denied=self.query("SELECT salary FROM employees")
        d=self.factory.create_dashboard(user=self.wide,is_draft=False)
        for q in (allowed,denied):
            self.factory.create_widget(dashboard=d,visualization=self.factory.create_visualization(query_rel=q),
                options={"position":{"col":0,"row":0,"sizeX":6,"sizeY":8},"parameterMappings":{"secret":{"value":"do-not-expose"}}})
        models.db.session.commit()
        response=self.make_request("get",f"/api/dashboards/{d.id}",user=self.narrow)
        self.assertEqual(response.status_code,200,response.json)
        widgets=response.json["widgets"]
        self.assertEqual(sum(bool(w.get("restricted")) for w in widgets),1)
        blocked=next(w for w in widgets if w.get("restricted"))
        self.assertNotIn("visualization",blocked)
        self.assertNotIn("parameterMappings",blocked["options"])

    def test_date_parameters_work_and_star_requires_explicit_shared_projection(self):
        parameters=[{"name":"period","type":"date-range","value":{"start":"2026-01-01","end":"2026-01-31"}}]
        q=self.query("SELECT id,total FROM orders WHERE created_at BETWEEN '{{period.start}}' AND '{{period.end}}'",parameters=parameters)
        self.assertEqual(self.get(q,self.narrow).status_code,200)
        result=self.execute(q,self.narrow,{"period":{"start":"2026-01-01","end":"2026-01-31"}})
        self.assertEqual(len(result.data["rows"]),1)
        for owner,viewer in ((self.wide,self.narrow),(self.narrow,self.wide)):
            star=self.query("SELECT * FROM employees",owner)
            self.assertEqual(self.get(star,viewer).status_code,403)
            self.assertEqual(self.get(star,owner).status_code,200)

    def test_queue_rechecks_revoked_permission_and_does_not_reuse_cached_result(self):
        q=self.query("SELECT id,total FROM orders")
        response=self.make_request("post",f"/api/queries/{q.id}/results",data={"max_age":0},user=self.narrow)
        self.assertEqual(response.status_code,200)
        org=models.Organization.get_by_id(self.source.org_id)
        configuration=copy.deepcopy(org.settings["data_access_group_rules"])
        configuration["policies"][1]["rules"][0]["columns"].remove("total")
        org.settings["data_access_group_rules"]=configuration
        models.db.session.commit()
        SimpleWorker([self.queue],connection=rq_redis_connection,job_class=self.queue.job_class).work(burst=True,with_scheduler=False,logging_level="ERROR")
        self.assertEqual(models.QueryResult.query.count(),0)
        self.assertEqual(self.get(q,self.narrow).status_code,403)

    def test_query_list_count_and_pages_match_authorized_definitions(self):
        self.query("SELECT id,total FROM orders")
        self.query("SELECT name FROM employees",self.narrow)
        self.query("SELECT salary FROM employees")
        response=self.make_request("get","/api/queries?page_size=1",user=self.narrow)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json["count"],2)
        self.assertEqual(len(response.json["results"]),1)
        response=self.make_request("get","/api/queries?page_size=1&page=2",user=self.narrow)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(len(response.json["results"]),1)

    def test_cross_scope_parameters_are_values_not_dynamic_column_selectors(self):
        select_column=[{"name":"column","type":"enum","enumOptions":"id\nsalary","value":"id"}]
        q=self.query("SELECT {{column}} FROM employees",parameters=select_column)
        self.assertEqual(self.get(q,self.narrow).status_code,403)
        quoted_value=[{"name":"person","type":"enum","enumOptions":"synthetic\nother","value":"synthetic"}]
        q=self.query("SELECT name FROM employees WHERE name='{{person}}'",parameters=quoted_value)
        self.assertEqual(self.get(q,self.narrow).status_code,200)
        self.assertEqual(models.Query.get_by_id(q.id).parameters[0]["enumOptions"],"synthetic\nother")
