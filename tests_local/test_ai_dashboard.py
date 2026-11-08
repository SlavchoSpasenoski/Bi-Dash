import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("ai_dashboard", Path(__file__).parents[1] / "redash/ai_dashboard.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class DashboardPlanTests(unittest.TestCase):
    def widget(self, **updates):
        return {"title": "Monthly sales", "sql": "SELECT month, SUM(total) AS revenue FROM mdl_orders GROUP BY month",
                "visualization": "bar", "x": "month", "y": ["revenue"], **updates}

    def test_plan_maps_real_chart_options(self):
        plan = builder.parse_plan(json.dumps({"title": "Sales", "widgets": [self.widget()]}))
        kind, options = builder.visualization_options(plan["widgets"][0])
        self.assertEqual(kind, "CHART")
        self.assertEqual(options["columnMapping"], {"month": "x", "revenue": "y"})
        self.assertEqual(options["globalSeriesType"], "column")

    def test_select_and_cte_with_exact_table_prefixes_are_allowed(self):
        for sql in ("SELECT COUNT(*) AS users FROM mdl_user;",
                    "WITH totals AS (SELECT SUM(total) AS amount FROM mdl_orders) SELECT amount FROM totals",
                    "SELECT DATE_FORMAT(created_at, '%Y-%m') AS month, SUM(total) AS revenue FROM mdl_orders GROUP BY month",
                    "SELECT * FROM (SELECT COUNT(*) AS users FROM mdl_user) AS bidash_ai_result LIMIT 1000"):
            with self.subTest(sql=sql):
                self.assertTrue(builder.validate_readonly_sql(sql))

    def test_unsafe_or_nonreporting_sql_is_rejected(self):
        for sql in ("DELETE FROM orders", "SELECT 1; DROP TABLE orders", "SELECT * INTO secret FROM orders",
                    "WITH changed AS (DELETE FROM orders RETURNING *) SELECT * FROM changed",
                    "SELECT pg_read_file('/etc/passwd')", "SELECT LOAD_FILE('/etc/passwd')", "SELECT SLEEP(60)",
                    "SELECT nextval('sequence')", "SELECT pg_notify('test','x')", "SELECT custom_function()",
                    "SELECT * FROM orders FOR UPDATE", "SELECT * FROM orders FOR SHARE",
                    "SELECT 1 /*! INTO OUTFILE '/tmp/leak' */", "SELECT 1 -- comment", "SELECT @x := 1"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                builder.validate_readonly_sql(sql)

    def test_invalid_and_overlarge_plans_leave_no_usable_plan(self):
        for plan in ([], {}, {"title": "Sales", "widgets": []},
                     {"title": "Sales", "widgets": [self.widget()] * 7},
                     {"title": "Sales", "widgets": [self.widget(visualization="javascript")]},
                     {"title": "Sales", "widgets": [self.widget(y=["bad alias"])]},
                     {"title": "Sales", "widgets": [self.widget(sql="DROP TABLE orders")]}):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                builder.parse_plan(json.dumps(plan))

    def test_clarification_and_fenced_json(self):
        self.assertEqual(builder.parse_plan('{"clarification":"Which date column?"}'),
                         {"clarification": "Which date column?"})
        self.assertEqual(builder.parse_plan('```json\n{"title":"Report","widgets":[' +
                                            json.dumps(self.widget()) + ']}\n```')["title"], "Report")

    def test_schema_only_contains_selected_table_definitions(self):
        schema = [{"name": "mdl_user", "columns": ["id", {"name": "created_at"}]},
                  {"name": "secret_other", "columns": ["private"]}]
        reference = builder.reference_schema(schema, ["mdl_user"], "mysql")
        self.assertIn("mdl_user(id, created_at)", reference)
        self.assertNotIn("secret_other", reference)
        with self.assertRaises(ValueError):
            builder.reference_schema(schema, ["user"], "mysql")


if __name__ == "__main__":
    unittest.main()
