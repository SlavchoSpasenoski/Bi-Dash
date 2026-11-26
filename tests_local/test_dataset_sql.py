"""Dataset compiler contracts and opt-in independent disposable-engine oracles."""

import importlib.util
import os
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import Mock

import sqlglot
from sqlglot import exp


spec = importlib.util.spec_from_file_location(
    "dataset_sql", Path(__file__).parents[1] / "redash" / "dataset_sql.py"
)
compiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compiler)

BASE = "SELECT id, revenue, cost, quantity, label FROM dataset_fixture WHERE enabled = 1"
COLUMNS = {
    "id": "integer",
    "revenue": "decimal",
    "cost": "decimal",
    "quantity": "integer",
    "label": "text",
}
FIELDS = {
    "sale_id": {"field": "id"},
    "profit": {"op": "-", "left": {"field": "revenue"}, "right": {"field": "cost"}},
    "unit_revenue": {"op": "/", "left": {"field": "revenue"}, "right": {"field": "quantity"}},
    "half_quantity": {"op": "/", "left": {"field": "quantity"}, "right": {"decimal": "2"}},
    "label": {"field": "label"},
}
DIALECTS = ("postgres", "mysql", "mariadb")


class DatasetCompilerTests(unittest.TestCase):
    def test_source_field_picker_uses_saved_projection_order_and_aliases(self):
        names = compiler.dataset_source_fields(
            "SELECT o.region, o.gross AS revenue, o.cost FROM orders o WHERE o.active = 1"
        )
        self.assertEqual(names, ["region", "revenue", "cost"])
        for sql in ("SELECT * FROM orders", "SELECT a AS amount, b AS Amount FROM orders"):
            with self.subTest(sql=sql), self.assertRaises(compiler.DatasetSqlError):
                compiler.dataset_source_fields(sql)

    def test_catalog_resolves_native_column_names_and_binds_schema(self):
        cursor = Mock()
        cursor.fetchall.return_value = [("Amount", "numeric", 38, 10)]
        sql = compiler.compile_dataset_from_catalog(
            cursor, 'SELECT "Amount" AS TOTAL FROM Sales', "postgres", "reporting",
            {"output": {"field": "total"}},
        )
        self.assertEqual(cursor.execute.call_args.args[1], ("reporting", "sales", "Amount"))
        self.assertIn('FROM "reporting".Sales', sql)

    def test_catalog_rejects_unsupported_numeric_metadata(self):
        cursor = Mock()
        for rows in ([], [("amount", "numeric", None, None)], [("amount", "numeric", 39, 10)],
                     [("amount", "numeric", 20, 11)], [("amount", "numeric", 10, -1)],
                     [("amount", "double precision", 53, None)]):
            with self.subTest(rows=rows), self.assertRaises(compiler.DatasetSqlError):
                cursor.fetchall.return_value = rows
                compiler.compile_dataset_from_catalog(
                    cursor, "SELECT amount FROM sales", "postgres", "reporting",
                    {"total": {"field": "amount"}},
                )

    def test_report_rejects_text_sum_and_wrong_filter_type(self):
        cursor = Mock()
        cursor.fetchall.return_value = [("amount", "numeric", 20, 10), ("region", "text", None, None)]
        fields = {"amount": {"field": "amount"}, "region": {"field": "region"}}
        for metric, filters in (
            ({"field": "region", "aggregation": "sum"}, []),
            ({"field": "amount", "aggregation": "avg"}, [{"field": "amount", "value": "ten"}]),
        ):
            with self.subTest(metric=metric, filters=filters), self.assertRaises(compiler.DatasetSqlError):
                compiler.compile_dataset_from_catalog(
                    cursor, "SELECT amount, region FROM sales", "postgres", "reporting", fields,
                    report={"group_by": ["region"], "metrics": {"metric": metric}, "filters": filters},
                )

    def test_outer_projection_preserves_requested_order_and_base_filter(self):
        for dialect in DIALECTS:
            with self.subTest(dialect=dialect):
                sql = compiler.compile_dataset_sql(BASE, dialect, COLUMNS, FIELDS)
                ast_dialect = "mysql" if dialect == "mariadb" else dialect
                query = sqlglot.parse_one(sql, read=ast_dialect)
                self.assertIsInstance(query, exp.Select)
                self.assertEqual(query.named_selects, list(FIELDS))
                base = query.args["from_"].this.this
                self.assertEqual(base, sqlglot.parse_one(BASE, read=ast_dialect))
                divisions = list(query.find_all(exp.Div))
                self.assertEqual(len(divisions), 2)
                for division in divisions:
                    self.assertIsInstance(division.expression, exp.Nullif)

    def test_rejects_unresolved_or_untyped_formula_dependencies(self):
        for expression in (
            {"field": "missing"},
            {"op": "-", "left": {"field": "label"}, "right": {"decimal": "3"}},
            {"op": "SUM", "left": {"field": "cost"}, "right": {"decimal": "3"}},
            {"decimal": "NaN"},
            {"decimal": "Infinity"},
            {"decimal": True},
            {"field": "cost", "op": "+"},
            {"op": "+"},
        ):
            with self.subTest(expression=expression):
                with self.assertRaises(compiler.DatasetSqlError):
                    compiler.compile_dataset_sql(BASE, "postgres", COLUMNS, {"out": expression})

    def test_rejects_missing_duplicate_or_wildcard_output_identity(self):
        for base in (
            "SELECT * FROM dataset_fixture",
            "SELECT x.* FROM dataset_fixture x",
            "SELECT id, id FROM dataset_fixture",
            "SELECT ABS(id) FROM dataset_fixture",
        ):
            with self.subTest(base=base):
                with self.assertRaises(compiler.DatasetSqlError):
                    compiler.compile_dataset_sql(base, "postgres", {"id": "integer"}, {"out": {"field": "id"}})

    def test_rejects_unsupported_base_shapes_unresolved_params_and_dialect(self):
        for base in (
            "SELECT id FROM dataset_fixture UNION ALL SELECT id FROM dataset_fixture",
            "SELECT id FROM dataset_fixture WHERE id = {{id}}",
            "SELECT id FROM dataset_fixture WHERE id = $1",
            "SELECT id FROM dataset_fixture; SELECT id FROM dataset_fixture",
        ):
            with self.subTest(base=base):
                with self.assertRaises(compiler.DatasetSqlError):
                    compiler.compile_dataset_sql(base, "postgres", {"id": "integer"}, {"out": {"field": "id"}})
        with self.assertRaises(compiler.DatasetSqlError):
            compiler.compile_dataset_sql(BASE, "sqlite", COLUMNS, FIELDS)

    def test_rejects_case_colliding_output_fields_and_formula_cycles(self):
        for fields in (
            {"Profit": {"field": "cost"}, "profit": {"field": "revenue"}},
            {"a": {"field": "b"}, "b": {"field": "a"}},
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(compiler.DatasetSqlError):
                    compiler.compile_dataset_sql(BASE, "postgres", COLUMNS, fields)


class DatasetEngineOracleTests(unittest.TestCase):
    """Set BIDASH_DATASET_QA_<DIALECT> to the name of a disposable Docker container.

    Use default local superuser for a PostgreSQL fixture container or an empty
    root password for a disposable MySQL/MariaDB fixture container. Each test
    creates its own session-local fixture and never accesses app metadata.
    """

    FIXTURE = """
        CREATE TEMPORARY TABLE dataset_fixture (
            id INTEGER, revenue DECIMAL(24, 10), cost DECIMAL(24, 10),
            quantity INTEGER, label VARCHAR(20), enabled INTEGER
        );
        INSERT INTO dataset_fixture VALUES
            (1, 10.1250000001, 2.0000000001, 2, 'ordinary', 1),
            (2, 3.2, NULL, 0, 'zero', 1),
            (3, -9.8, 1.2, -4, 'negative', 1),
            (4, NULL, 2, 3, 'null', 1),
            (5, 99.99, 0.99, 1, 'filtered', 0),
            (6, 90071992547409.1234567890, 90071992547409.1000000000, 1, 'money', 1),
            (6, 90071992547409.1234567890, 90071992547409.1000000000, 1, 'money', 1);
    """
    EXPECTED = [
        "1|8.1250000000|5.0625000001|1.0000000000|ordinary",
        "2|NULL|NULL|0.0000000000|zero",
        "3|-11.0000000000|2.4500000000|-2.0000000000|negative",
        "4|NULL|NULL|1.5000000000|null",
        "6|0.0234567890|90071992547409.1234567890|0.5000000000|money",
        "6|0.0234567890|90071992547409.1234567890|0.5000000000|money",
    ]

    def check_engine(self, dialect):
        container = os.environ.get(f"BIDASH_DATASET_QA_{dialect.upper()}")
        if not container:
            self.skipTest(f"Disposable {dialect} fixture container is unavailable")
        if not re.fullmatch(r"bidash-dataset-qa-[A-Za-z0-9_-]+", container):
            self.fail("Dataset oracle requires a named disposable fixture container")

        compiled = compiler.compile_dataset_sql(BASE, dialect, COLUMNS, FIELDS)
        reference = """
            SELECT id, CAST(revenue - cost AS DECIMAL(38,10)),
                CAST(CASE WHEN quantity = 0 THEN NULL ELSE revenue / quantity END AS DECIMAL(38,10)),
                CAST(quantity * 0.5 AS DECIMAL(38,10)), label
            FROM dataset_fixture WHERE enabled = 1 ORDER BY id
        """
        command = ["docker", "exec", "-i", container]
        if dialect == "postgres":
            command += ["psql", "-XAtq", "-U", "postgres", "-d", "postgres", "-P", "null=NULL", "-v", "ON_ERROR_STOP=1"]
        else:
            command += ["mariadb" if dialect == "mariadb" else "mysql", "-uroot", "-NBr", "mysql"]

        sql = f"{self.FIXTURE}\nSELECT * FROM ({compiled}) AS actual ORDER BY sale_id;\n{reference};"
        self.assertEqual(self.run_sql(command, sql), self.EXPECTED * 2)

        boundary = compiler.compile_dataset_sql(
            "SELECT id, numerator, denominator FROM dataset_boundary",
            dialect,
            {"id": "integer", "numerator": "decimal", "denominator": "decimal"},
            {
                "id": {"field": "id"},
                "ratio": {
                    "op": "/",
                    "left": {"field": "numerator"},
                    "right": {"field": "denominator"},
                },
            },
        )
        boundary_sql = f"""
            CREATE TEMPORARY TABLE dataset_boundary (
                id INTEGER, numerator DECIMAL(38,10), denominator DECIMAL(38,10)
            );
            INSERT INTO dataset_boundary VALUES
                (1, 1, 20000000000.0000000001),
                (2, 1, 20000000000),
                (3, 1, 19999999999.9999999999),
                (4, -1, 20000000000),
                (5, 1, -20000000000),
                (6, 1, 0),
                (7, NULL, 3),
                (8, 9999999999999999999999999999.9999999999, 1);
            SELECT * FROM ({boundary}) AS actual ORDER BY id;
        """
        expected = [
            "1|0.0000000000",
            "2|0.0000000001",
            "3|0.0000000001",
            "4|-0.0000000001",
            "5|-0.0000000001",
            "6|NULL",
            "7|NULL",
            "8|9999999999999999999999999999.9999999999",
        ]
        if dialect != "postgres":
            boundary_sql = "SET SESSION div_precision_increment = 0;\n" + boundary_sql
        self.assertEqual(self.run_sql(command, boundary_sql), expected)

    def run_sql(self, command, sql):
        run = subprocess.run(command, input=sql, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout.replace("\t", "|").splitlines()

    def test_postgresql_matches_independent_oracle(self):
        self.check_engine("postgres")

    def test_mysql_matches_independent_oracle(self):
        self.check_engine("mysql")

    def test_mariadb_matches_independent_oracle(self):
        self.check_engine("mariadb")
