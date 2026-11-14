"""Metadata selection contracts without a Flask application or database."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock


stub = ModuleType("redash.data_access_runtime")
stub.DataAccessDenied = type("DataAccessDenied", (ValueError,), {})
spec = importlib.util.spec_from_file_location("data_access_metadata", Path(__file__).parents[1] / "redash/data_access_metadata.py")
metadata = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"redash": ModuleType("redash"), "redash.data_access_runtime": stub}):
    spec.loader.exec_module(metadata)


class DataAccessMetadataTests(unittest.TestCase):
    def test_stages_return_one_name_level_and_bounded_queries(self):
        for connector in ("mysql", "pg"):
            for schema, table, expected in ((None, None, "schemas"), ("Sales", None, "tables"), ("Sales", "Orders", "columns")):
                with self.subTest(connector=connector, stage=expected):
                    key, query, params = metadata.metadata_query(connector, schema, table)
                    self.assertEqual(key, expected)
                    self.assertIn("SELECT", query.upper())
                    self.assertIn("LIMIT %s", query)
                    self.assertEqual(params[-1], metadata.MAX_METADATA_ITEMS + 1)
                    self.assertNotIn("SELECT *", query.upper())

    def test_identifiers_are_exact_bound_values_not_interpolated_sql(self):
        schema = "Sales.`Odd'; DROP TABLE secrets;--"
        table = "Orders.Табела"
        for connector in ("mysql", "pg"):
            with self.subTest(connector=connector):
                key, query, params = metadata.metadata_query(connector, schema, table)
                self.assertEqual(key, "columns")
                self.assertNotIn(schema, query)
                self.assertNotIn(table, query)
                self.assertIn(schema, params)
                self.assertIn(table, params)

    def test_table_requires_schema(self):
        for connector in ("mysql", "pg"):
            with self.subTest(connector=connector), self.assertRaises(ValueError):
                metadata.metadata_query(connector, table="Orders")

    def test_names_are_strict_and_bounded(self):
        for value in ("", "   ", "x" * 257, "a\x00b", "a\nb", [], {}, True, 1):
            for connector in ("mysql", "pg"):
                with self.subTest(value=value, connector=connector), self.assertRaises(ValueError):
                    metadata.metadata_query(connector, value)
                with self.subTest(table=value, connector=connector), self.assertRaises(ValueError):
                    metadata.metadata_query(connector, "Sales", value)

    def test_maximum_length_names_are_preserved(self):
        for connector in ("mysql", "pg"):
            _, _, params = metadata.metadata_query(connector, "S" * 256, "T" * 256)
            self.assertIn("S" * 256, params)
            self.assertIn("T" * 256, params)

    def test_other_connectors_fail_closed(self):
        for connector in ("mssql", "oracle", "rds_mysql", "unknown", None, []):
            with self.subTest(connector=connector), self.assertRaises(ValueError):
                metadata.metadata_query(connector)

    def test_system_schemas_cannot_be_selected_explicitly(self):
        for connector, schemas in (("mysql", ("mysql", "MySQL", "sys", "information_schema", "performance_schema")),
                                   ("pg", ("pg_catalog", "information_schema", "pg_temp_9"))):
            for schema in schemas:
                with self.subTest(connector=connector, schema=schema), self.assertRaises(ValueError):
                    metadata.metadata_query(connector, schema)

    def test_names_only_response_is_bounded_and_marks_truncation(self):
        for connector, helper in (("mysql", "_mysql_rows"), ("pg", "_pg_rows")):
            source = SimpleNamespace(type=connector, uses_ssh_tunnel=False)
            rows = [("Name " + str(index),) for index in range(metadata.MAX_METADATA_ITEMS + 1)]
            with self.subTest(connector=connector), patch.object(metadata, helper, return_value=rows):
                result = metadata.fetch_metadata(source, "Sales", "Orders")
                self.assertEqual(result["columns"], [row[0] for row in rows[:-1]])
                self.assertIs(result["truncated"], True)
            with patch.object(metadata, helper, return_value=rows[:-1]):
                self.assertIs(metadata.fetch_metadata(source)["truncated"], False)

    def test_errors_and_invalid_rows_never_expose_driver_details(self):
        source = SimpleNamespace(type="mysql", uses_ssh_tunnel=False)
        with patch.object(metadata, "_mysql_rows", side_effect=RuntimeError("password=synthetic-secret host=private")):
            with self.assertRaises(metadata.MetadataUnavailable) as error:
                metadata.fetch_metadata(source)
            self.assertNotIn("synthetic-secret", str(error.exception))
            self.assertNotIn("private", str(error.exception))
        for rows in ([(None,)], [(12,)], [()], None):
            with self.subTest(rows=rows), patch.object(metadata, "_mysql_rows", return_value=rows):
                with self.assertRaises(metadata.MetadataUnavailable):
                    metadata.fetch_metadata(source)

    def test_ssh_fails_before_connector_io(self):
        with patch.object(metadata, "_mysql_rows") as fetch, self.assertRaises(ValueError):
            metadata.fetch_metadata(SimpleNamespace(type="mysql", uses_ssh_tunnel=True))
        fetch.assert_not_called()

    def test_mysql_metadata_rolls_back_and_closes_on_query_failure(self):
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value = cursor
        cursor.fetchone.return_value = ("8.4.13", "MySQL Community Server - GPL")
        cursor.fetchmany.side_effect = RuntimeError("driver failure")
        driver = ModuleType("redash.data_access_mysql")
        driver._connect = MagicMock(return_value=connection)
        driver.detect_server = MagicMock(return_value="mysql")
        with patch.dict(sys.modules, {"redash.data_access_mysql": driver}):
            with self.assertRaises(RuntimeError):
                metadata._mysql_rows(SimpleNamespace(), "SELECT fixed", (2001,))
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()
        cursor.close.assert_called_once()
        cursor.fetchmany.assert_called_once_with(metadata.MAX_METADATA_ITEMS + 1)
        cursor.execute.assert_any_call("START TRANSACTION READ ONLY")


if __name__ == "__main__":
    unittest.main()
