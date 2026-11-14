"""Independent validation checks; no application, database or provider initialization."""
import copy
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("data_access", Path(__file__).parents[1] / "redash/data_access.py")
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


class DataAccessValidationTests(unittest.TestCase):
    def configuration(self):
        return {"revision": 0, "policies": [{
            "id": "sales-readers", "name": "Sales readers", "data_source_id": 1,
            "status": "draft", "default_effect": "deny", "user_ids": [2], "group_ids": [],
            "rules": [{"catalog": None, "schema": "Sales", "table": "Orders",
                       "column_mode": "explicit", "columns": ["OrderID", "Amount"]}],
        }]}

    def reject(self, body):
        before = copy.deepcopy(body)
        with self.assertRaises(ValueError):
            access.validate_configuration(body)
        self.assertEqual(body, before, "Rejected input must not be partially mutated")

    def test_valid_draft_preserves_identifiers_and_input(self):
        body = self.configuration()
        before = copy.deepcopy(body)
        actual = access.validate_configuration(body)
        self.assertEqual(actual, before)
        self.assertEqual(body, before)

    def test_empty_configuration_is_valid(self):
        self.assertEqual(access.validate_configuration({"revision": 0, "policies": []}),
                         {"revision": 0, "policies": []})

    def test_validated_configuration_is_detached_from_input(self):
        body = self.configuration()
        actual = access.validate_configuration(body)
        actual["policies"][0]["rules"][0]["columns"].append("Extra")
        actual["policies"][0]["user_ids"].append(3)
        self.assertEqual(body, self.configuration())

    def test_default_configuration_has_no_shared_mutable_state(self):
        first = access.default_configuration()
        first["policies"].append({"id": "changed"})
        self.assertEqual(access.default_configuration(), {"revision": 0, "policies": []})

    def test_all_current_records_fixed_column_snapshot(self):
        body = self.configuration()
        body["policies"][0]["rules"][0]["column_mode"] = "all_current"
        self.assertEqual(access.validate_configuration(body), body)

    def test_size_limits_and_malformed_containers(self):
        for body in (None, [], {}, {"revision": 0, "policies": {}},
                     {"revision": 0, "policies": [None]},
                     {"revision": 0, "policies": [None] * 51}):
            with self.subTest(body=body):
                self.reject(body)
        for field, value in (("rules", [None] * 101), ("user_ids", list(range(1, 502))),
                             ("group_ids", {}), ("data_source_id", 2147483648),
                             ("name", "x" * 257), ("id", "bad id")):
            with self.subTest(field=field):
                body = self.configuration()
                body["policies"][0][field] = value
                self.reject(body)
        body = self.configuration()
        body["policies"][0]["rules"][0]["columns"] = ["x"] * 1001
        self.reject(body)

    def test_activation_and_allow_defaults_are_rejected(self):
        for field, value in (("status", "active"), ("status", "enforced"),
                             ("status", True), ("default_effect", "allow")):
            with self.subTest(field=field, value=value):
                body = self.configuration()
                body["policies"][0][field] = value
                self.reject(body)

    def test_unknown_fields_cannot_smuggle_enforcement(self):
        for level in ("configuration", "policy", "rule"):
            with self.subTest(level=level):
                body = self.configuration()
                target = body if level == "configuration" else body["policies"][0]
                if level == "rule":
                    target = target["rules"][0]
                target["enforced"] = True
                self.reject(body)

    def test_ids_and_revision_require_integers(self):
        for value in (True, False, "1", 1.0, -1, None):
            with self.subTest(value=value):
                body = self.configuration()
                body["revision"] = value
                self.reject(body)
        for field in ("data_source_id", "user_ids", "group_ids"):
            for value in (True, 0, -1, "2", 2.0, None):
                with self.subTest(field=field, value=value):
                    body = self.configuration()
                    body["policies"][0][field] = value if field == "data_source_id" else [value]
                    self.reject(body)

    def test_identifiers_reject_wildcard_empty_and_control_characters(self):
        for field in ("catalog", "schema", "table", "columns"):
            for value in ("*", "sales.*", "", "   ", "private\x00data"):
                with self.subTest(field=field, value=value):
                    body = self.configuration()
                    body["policies"][0]["rules"][0][field] = [value] if field == "columns" else value
                    self.reject(body)

    def test_paths_require_namespace_and_explicit_column_snapshot(self):
        body = self.configuration()
        body["policies"][0]["rules"][0].update(catalog=None, schema=None)
        self.reject(body)
        for mode in ("explicit", "all_current", "all_future", "*"):
            with self.subTest(mode=mode):
                body = self.configuration()
                body["policies"][0]["rules"][0].update(column_mode=mode, columns=[])
                self.reject(body)

    def test_duplicate_principals_columns_paths_and_policy_ids_rejected(self):
        for field in ("user_ids", "group_ids"):
            body = self.configuration()
            body["policies"][0][field] = [2, 2]
            self.reject(body)
        body = self.configuration()
        body["policies"][0]["rules"][0]["columns"] = ["Amount", "Amount"]
        self.reject(body)
        body = self.configuration()
        body["policies"][0]["rules"] *= 2
        self.reject(body)
        body = self.configuration()
        body["policies"] *= 2
        self.reject(body)

    def test_overlap_for_same_source_and_principal_rejected(self):
        for field in ("user_ids", "group_ids"):
            body = self.configuration()
            body["policies"][0][field] = [2]
            second = copy.deepcopy(body["policies"][0])
            second["id"] = "second-policy"
            body["policies"].append(second)
            self.reject(body)

    def test_only_mysql_advertises_shared_enforcement(self):
        for connector in ("mysql", "rds_mysql", "pg", "mssql", "oracle", "unknown", None):
            with self.subTest(connector=connector):
                capabilities = access.connector_capabilities(connector)
                self.assertIsInstance(capabilities, dict)
                self.assertIs(capabilities["enforcement_available"], connector == "mysql")
                self.assertIs(capabilities["draft_supported"], connector in ("mysql", "rds_mysql", "pg", "mssql", "oracle"))

    def test_group_configuration_is_immediately_active_and_detached(self):
        body = self.configuration()
        body["policies"][0].update(status="active", user_ids=[], group_ids=[3])
        original = copy.deepcopy(body)
        result = access.validate_group_configuration(body)
        self.assertEqual(result, original)
        result["policies"][0]["rules"][0]["columns"].append("changed")
        self.assertEqual(body, original)

    def test_group_rules_require_groups_nonempty_rules_and_no_direct_users(self):
        for changes in ({"group_ids": []}, {"user_ids": [2]}, {"rules": []}, {"status": "draft"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                body = self.configuration()
                body["policies"][0].update(status="active", user_ids=[], group_ids=[3])
                body["policies"][0].update(changes)
                access.validate_group_configuration(body)


if __name__ == "__main__":
    unittest.main()
