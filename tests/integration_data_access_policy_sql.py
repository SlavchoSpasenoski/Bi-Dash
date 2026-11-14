"""Application SQL isolation with genuinely full-privilege MySQL credentials.

Only explicit disposable access-test-mysql/mariadb hosts accepted. No app metadata reset.
"""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import MySQLdb

os.environ.setdefault("REDASH_COOKIE_SECRET", "disposable-policy-qa-cookie-secret")
os.environ.setdefault("REDASH_SECRET_KEY", "disposable-policy-qa-source-secret")
sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location("data_access_sql", Path(__file__).parents[1] / "redash/data_access_sql.py")
compiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compiler)


class ApplicationSQLIsolationTests(unittest.TestCase):
    SALES = "bidash_policy_test_sales"
    PRIVATE = "bidash_policy_test_private"

    @classmethod
    def setUpClass(cls):
        host = os.environ.get("POLICY_SQL_TEST_HOST", "")
        if not host.startswith(("access-test-mysql", "access-test-mariadb")):
            raise RuntimeError("Explicit disposable access-test-mysql/mariadb host required")
        cls.host = host
        cls.admin = MySQLdb.connect(host=host, user="root", passwd=os.environ["POLICY_SQL_TEST_ROOT_PASSWORD"])
        cls.admin.autocommit(True)
        with cls.admin.cursor() as cursor:
            cursor.execute("SELECT VERSION(),@@version_comment")
            print("Disposable server:", cursor.fetchone(), flush=True)

    @classmethod
    def tearDownClass(cls):
        cls.admin.close()

    def admin_sql(self, statement):
        with self.admin.cursor() as cursor:
            cursor.execute(statement)

    def setUp(self):
        for schema in (self.SALES, self.PRIVATE):
            self.admin_sql("DROP DATABASE IF EXISTS `" + schema + "`")
            self.admin_sql("CREATE DATABASE `" + schema + "`")
        self.admin_sql("DROP USER IF EXISTS 'qa_policy_broad'@'%'")
        self.admin_sql("CREATE USER 'qa_policy_broad'@'%' IDENTIFIED BY 'test-only-policy-password'")
        self.admin_sql("CREATE TABLE `" + self.SALES + "`.orders(id INT, amount INT, secret VARCHAR(100)) ENGINE=InnoDB")
        self.admin_sql("INSERT INTO `" + self.SALES + "`.orders VALUES(1,42,'sensitive-value')")
        self.admin_sql("CREATE TABLE `" + self.SALES + "`.depts(id INT,label VARCHAR(100)) ENGINE=InnoDB")
        self.admin_sql("INSERT INTO `" + self.SALES + "`.depts VALUES(1,'Sales')")
        self.admin_sql("CREATE TABLE `" + self.PRIVATE + "`.payroll(id INT,salary INT) ENGINE=InnoDB")
        self.admin_sql("INSERT INTO `" + self.PRIVATE + "`.payroll VALUES(1,999)")
        self.admin_sql("GRANT ALL PRIVILEGES ON *.* TO 'qa_policy_broad'@'%'")
        self.connection = MySQLdb.connect(host=self.host, user="qa_policy_broad", passwd="test-only-policy-password", db=self.SALES)
        self.connection.autocommit(True)
        self.source = SimpleNamespace(type="mysql", uses_ssh_tunnel=False, options={"host": self.host,
            "user": "qa_policy_broad", "passwd": "test-only-policy-password", "db": self.SALES, "port": 3306})
        self.policy = {"rules": [
            {"catalog": None, "schema": self.SALES, "table": "orders", "column_mode": "explicit", "columns": ["id", "amount"]},
            {"catalog": None, "schema": self.SALES, "table": "depts", "column_mode": "explicit", "columns": ["id", "label"]},
        ]}
        self.business_executions = 0

    def tearDown(self):
        self.connection.close()
        self.admin_sql("DROP USER IF EXISTS 'qa_policy_broad'@'%'")
        for schema in (self.SALES, self.PRIVATE):
            self.admin_sql("DROP DATABASE IF EXISTS `" + schema + "`")

    def execute_scoped(self, query):
        rewritten = compiler.compile_policy_sql(query, self.policy, connector="mysql", default_schema=self.SALES)
        self.business_executions += 1
        with self.connection.cursor() as cursor:
            cursor.execute(rewritten)
            return [item[0] for item in cursor.description], cursor.fetchall()

    def test_fixture_credentials_really_can_read_forbidden_data_without_policy(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT secret FROM orders")
            self.assertEqual(cursor.fetchall(), (("sensitive-value",),))
            cursor.execute("SELECT salary FROM `" + self.PRIVATE + "`.payroll")
            self.assertEqual(cursor.fetchall(), ((999,),))
            cursor.execute("UPDATE orders SET amount=99")
            cursor.execute("SELECT amount FROM orders")
            self.assertEqual(cursor.fetchall(), ((99,),))
            cursor.execute("CREATE TABLE direct_privilege_probe(id INT)")
            cursor.execute("DROP TABLE direct_privilege_probe")

    def test_star_and_alias_star_return_only_two_allowed_columns(self):
        for query in ("SELECT * FROM orders", "SELECT o.* FROM orders o", "SELECT * FROM `" + self.SALES + "`.orders"):
            with self.subTest(query=query):
                columns, rows = self.execute_scoped(query)
                self.assertEqual(columns, ["id", "amount"])
                self.assertEqual(rows, ((1, 42),))

    def test_new_database_column_does_not_expand_application_snapshot(self):
        self.admin_sql("ALTER TABLE `" + self.SALES + "`.orders ADD COLUMN future_secret VARCHAR(100) DEFAULT 'new-secret'")
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT future_secret FROM orders")
            self.assertEqual(cursor.fetchall(), (("new-secret",),))
        self.assertEqual(self.execute_scoped("SELECT * FROM orders"), (["id", "amount"], ((1, 42),)))

    def test_denied_references_never_reach_business_execution(self):
        for query in ("SELECT secret FROM orders", "SELECT id FROM orders WHERE secret='sensitive-value'",
                      "SELECT id AS secret FROM orders WHERE secret>0",
                      "SELECT id FROM orders ORDER BY secret", "SELECT id FROM orders GROUP BY secret,id",
                      "SELECT id FROM orders HAVING MAX(secret)='sensitive-value'",
                      "SELECT SUM(LENGTH(secret)) FROM orders", "SELECT (SELECT secret FROM orders) FROM orders",
                      "WITH x AS (SELECT * FROM orders) SELECT secret FROM x",
                      "SELECT id FROM orders UNION ALL SELECT secret FROM orders",
                      "SELECT * FROM `" + self.PRIVATE + "`.payroll", "SELECT LOAD_FILE('/etc/passwd')",
                      "SELECT SLEEP(3)", "SELECT @@hostname"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.execute_scoped(query)
        self.assertEqual(self.business_executions, 0)

    def test_allowed_join_derived_cte_union_and_aggregate_execute(self):
        for query, expected in (("SELECT o.id,d.label FROM orders o JOIN depts d ON o.id=d.id", ((1, "Sales"),)),
                                ("SELECT x.id FROM (SELECT * FROM orders) x", ((1,),)),
                                ("WITH x AS (SELECT * FROM orders) SELECT id FROM x", ((1,),)),
                                ("SELECT id FROM orders UNION ALL SELECT id FROM depts", ((1,), (1,))),
                                ("SELECT SUM(amount) AS total FROM orders", ((42,),)),
                                ("SELECT DATE_FORMAT('2026-11-09','%Y') AS year_value FROM orders", (("2026",),)),
                                ("SELECT COUNT(*) AS total FROM orders", ((1,),))):
            with self.subTest(query=query):
                self.assertEqual(self.execute_scoped(query)[1], expected)

    def test_reduced_policy_changes_star_without_changing_database_grants(self):
        self.assertEqual(self.execute_scoped("SELECT * FROM orders")[0], ["id", "amount"])
        self.policy["rules"][0]["columns"] = ["id"]
        self.assertEqual(self.execute_scoped("SELECT * FROM orders"), (["id"], ((1,),)))
        with self.assertRaises(ValueError):
            self.execute_scoped("SELECT amount FROM orders")

    def test_actual_shared_runner_accepts_full_privileges_and_projects_policy(self):
        from redash.data_access_shared import run_shared_query, validate_shared_source
        self.assertTrue(validate_shared_source(self.source))
        data, error = run_shared_query(self.source, self.policy, "SELECT * FROM orders")
        self.assertIsNone(error)
        self.assertEqual([column["name"] for column in data["columns"]], ["id", "amount"])
        self.assertEqual(data["rows"], [{"id": 1, "amount": 42}])

    def test_shared_runner_rejects_table_replaced_by_definer_view(self):
        from redash.data_access_shared import run_shared_query
        self.admin_sql("DROP TABLE `" + self.SALES + "`.orders")
        self.admin_sql("CREATE SQL SECURITY DEFINER VIEW `" + self.SALES + "`.orders AS "
                       "SELECT id,salary AS amount,salary AS secret FROM `" + self.PRIVATE + "`.payroll")
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT amount FROM orders")
            self.assertEqual(cursor.fetchall(), ((999,),))
        with self.assertRaises(ValueError):
            run_shared_query(self.source, self.policy, "SELECT * FROM orders")

    def test_unprotected_connector_and_admin_path_block_user_writes(self):
        from redash.data_access_shared import run_shared_query
        from redash.query_runner import get_query_runner
        runner = get_query_runner("mysql", self.source.options)
        data, error = runner.run_query("SELECT * FROM orders", None)
        self.assertIsNone(error)
        self.assertEqual(len(data["columns"]), 3)
        for statement in ("DELETE FROM orders", "UPDATE orders SET amount=99", "DROP TABLE orders",
                          "CREATE TABLE injected(id INT)", "SELECT id INTO OUTFILE '/tmp/qa-leak' FROM orders",
                          "SELECT LOAD_FILE('/etc/passwd')", "CALL injected()", "SELECT SLEEP(1)"):
            with self.subTest(sql=statement):
                with self.assertRaises(ValueError):
                    run_shared_query(self.source, {"unrestricted": True}, statement)
                try:
                    data, error = runner.run_query(statement, None)
                except ValueError:
                    continue
                self.assertIsNone(data)
                self.assertTrue(error)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT amount FROM orders")
            self.assertEqual(cursor.fetchall(), ((42,),))

    def test_generated_column_tables_are_denied_without_executing_them(self):
        from redash.data_access_shared import run_shared_query
        from redash.query_runner import get_query_runner
        self.admin_sql("ALTER TABLE `" + self.SALES + "`.orders ADD generated_value INT AS (LENGTH(secret)) STORED")
        with self.assertRaises(ValueError):
            run_shared_query(self.source, self.policy, "SELECT id FROM orders")
        data,error=get_query_runner("mysql",self.source.options).run_query("SELECT id FROM orders",None)
        self.assertIsNone(data)
        self.assertTrue(error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
