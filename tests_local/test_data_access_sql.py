"""Independent adversarial contracts for application-enforced SQL column scopes."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / ("redash/" + name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

runtime = ModuleType("redash.data_access_runtime")
runtime.DataAccessDenied = type("DataAccessDenied", (ValueError,), {})
with patch.dict(sys.modules, {"redash": ModuleType("redash"), "redash.data_access_runtime": runtime}):
    compiler = load("data_access_sql")
    mysql = load("data_access_mysql")
    reporting = load("ai_dashboard")


class PolicySQLTests(unittest.TestCase):
    def setUp(self):
        imports = patch.dict(sys.modules, {"redash": ModuleType("redash"), "redash.data_access_runtime": runtime,
            "redash.data_access_mysql": mysql, "redash.ai_dashboard": reporting})
        imports.start()
        self.addCleanup(imports.stop)
        self.policy = {"rules": [
            {"catalog": None, "schema": "Sales", "table": "Orders", "column_mode": "explicit", "columns": ["ID", "Amount"]},
            {"catalog": None, "schema": "Sales", "table": "Depts", "column_mode": "explicit", "columns": ["ID", "Label"]},
        ]}

    def compile(self, query, **kwargs):
        return compiler.compile_policy_sql(query, self.policy, connector="mysql", default_schema="Sales", **kwargs)

    def test_star_is_rewritten_to_only_approved_columns(self):
        for query in ("SELECT * FROM Sales.Orders", "SELECT o.* FROM Sales.Orders AS o", "SELECT * FROM Orders"):
            with self.subTest(query=query):
                actual = self.compile(query)
                self.assertIsInstance(actual, str)
                self.assertNotIn("*", actual)
                self.assertIn("ID", actual)
                self.assertIn("Amount", actual)
                self.assertNotIn("Secret", actual)

    def test_ordinary_allowed_reporting_queries_compile(self):
        for query in ("SELECT ID, Amount FROM Sales.Orders WHERE Amount > 1 ORDER BY ID",
                      "SELECT SUM(Amount) AS Total FROM Sales.Orders",
                      "SELECT SUM(Amount) AS Total FROM Sales.Orders HAVING Total > 1",
                      "SELECT DATE_FORMAT('2026-11-09','%Y') AS YearValue FROM Sales.Orders",
                      "SELECT Amount FROM Sales.Orders GROUP BY ID,Amount HAVING MAX(ID)>0",
                      "SELECT COUNT(*) AS Total FROM Sales.Orders",
                      "SELECT o.ID,d.Label FROM Sales.Orders o JOIN Sales.Depts d ON o.ID=d.ID",
                      "SELECT x.ID FROM (SELECT ID FROM Sales.Orders) x",
                      "WITH x AS (SELECT ID FROM Sales.Orders) SELECT ID FROM x",
                      "SELECT ID FROM Sales.Orders UNION ALL SELECT ID FROM Sales.Depts",
                      "SELECT ID AS Secret FROM Sales.Orders",
                      "SELECT 'Secret' AS TextValue, ID FROM Sales.Orders"):
            with self.subTest(query=query):
                self.assertTrue(self.compile(query))

    def test_forbidden_references_are_denied_in_every_expression(self):
        for query in ("SELECT Secret FROM Sales.Orders", "SELECT ID FROM Sales.Orders WHERE Secret='x'",
                      "SELECT ID AS Secret FROM Sales.Orders WHERE Secret>0",
                      "SELECT ID FROM Sales.Orders ORDER BY Secret", "SELECT ID FROM Sales.Orders GROUP BY Secret,ID",
                      "SELECT ID FROM Sales.Orders HAVING MAX(Secret)='x'", "SELECT COUNT(Secret) FROM Sales.Orders",
                      "SELECT (SELECT Secret FROM Sales.Orders LIMIT 1) AS value FROM Sales.Orders",
                      "SELECT ID FROM Sales.Orders WHERE EXISTS(SELECT 1 FROM Sales.Orders x WHERE x.Secret='x')",
                      "SELECT o.ID FROM Sales.Orders o WHERE EXISTS(SELECT 1 FROM Sales.Depts d WHERE d.ID=o.Secret)",
                      "WITH x AS (SELECT * FROM Sales.Orders) SELECT Secret FROM x",
                      "SELECT ID FROM Sales.Orders UNION ALL SELECT Secret FROM Sales.Orders",
                      "SELECT ID FROM Sales.Orders WHERE FALSE AND Secret='x'"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.compile(query)

    def test_unapproved_tables_and_system_metadata_are_denied(self):
        for query in ("SELECT * FROM Private.Payroll", "SELECT * FROM Sales.Unapproved",
                      "SELECT * FROM information_schema.COLUMNS", "SELECT * FROM mysql.user",
                      "SELECT o.ID FROM Sales.Orders o JOIN Private.Payroll p ON o.ID=p.ID"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.compile(query)

    def test_mutations_external_access_and_unknown_functions_are_denied(self):
        for query in ("DELETE FROM Sales.Orders", "UPDATE Sales.Orders SET Amount=1",
                      "SELECT ID INTO OUTFILE '/tmp/leak' FROM Sales.Orders", "SELECT LOAD_FILE('/etc/passwd')",
                      "SELECT SLEEP(3)", "SELECT Private.secret_fn(1)", "SELECT @@hostname", "SELECT @secret",
                      "SELECT ID FROM Sales.Orders; SELECT Secret FROM Sales.Orders", "CALL Private.secret_proc()"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.compile(query)

    def test_case_sensitive_exact_policy_names(self):
        with self.assertRaises(ValueError):
            self.compile("SELECT ID FROM sales.orders")

    def test_empty_policy_and_unknown_connector_fail_closed(self):
        with self.assertRaises(ValueError):
            compiler.compile_policy_sql("SELECT * FROM Sales.Orders", {"rules": []}, connector="mysql")
        for connector in ("oracle", "mssql", "unknown"):
            with self.subTest(connector=connector), self.assertRaises(ValueError):
                compiler.compile_policy_sql("SELECT * FROM Sales.Orders", self.policy, connector=connector)

    def test_group_output_alias_resolves_but_where_alias_does_not(self):
        sql = self.compile("SELECT DATE_FORMAT('2026-01-01', '%Y-%m') AS month, SUM(Amount) AS total FROM Sales.Orders GROUP BY month ORDER BY month")
        self.assertIn("DATE_FORMAT", sql)
        with self.assertRaises(ValueError):
            self.compile("SELECT ID AS Secret FROM Sales.Orders WHERE Secret>0 GROUP BY Secret")


if __name__ == "__main__":
    unittest.main()
