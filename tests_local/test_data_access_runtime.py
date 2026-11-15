"""Group-union authorization and result scopes on a shared connection."""
import copy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / ("redash/" + name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

validation = load("data_access")
runtime = load("data_access_runtime")

class RuntimeAccessTests(unittest.TestCase):
    def setUp(self):
        self.source = SimpleNamespace(id=1, org_id=1, type="mysql", paused=False, options={"db": "Sales"})
        self.permissions = {"execute_query"}
        self.user = SimpleNamespace(id=2, org_id=1, role="editor", is_disabled=False, group_ids=[3], is_api_user=lambda: False,
                                    has_permission=lambda name: name in self.permissions)
        self.policy = {"id": "sales", "name": "Sales", "data_source_id": 1, "status": "active",
            "default_effect": "deny", "user_ids": [], "group_ids": [3], "rules": [
                {"catalog": None, "schema": "Sales", "table": "Orders", "column_mode": "explicit", "columns": ["ID", "Amount"]}]}
        self.settings = {"data_access_group_rules": {"revision": 1, "policies": [self.policy]}, "data_access_protected_sources": [1]}
        setting_patch = patch.object(runtime, "_settings", side_effect=lambda source: self.settings)
        setting_patch.start()
        self.addCleanup(setting_patch.stop)
        self.groups = ModuleType("redash.permissions")
        self.groups.has_access_to_groups = MagicMock(return_value=True)
        self.runners = ModuleType("redash.query_runner")
        self.runners.get_query_runner = MagicMock()
        self.shared = ModuleType("redash.data_access_shared")
        self.shared.run_shared_query = MagicMock(return_value=({"rows": []}, None))
        package = ModuleType("redash")
        package.settings = SimpleNamespace(DATASOURCE_SECRET_KEY="synthetic-test-scope-key")
        imports = patch.dict(sys.modules, {"redash": package, "redash.data_access": validation,
            "redash.permissions": self.groups, "redash.query_runner": self.runners, "redash.data_access_shared": self.shared})
        imports.start()
        self.addCleanup(imports.stop)

    def test_unprotected_source_preserves_legacy_behavior(self):
        source = SimpleNamespace(id=99, org_id=1)
        self.assertIsNone(runtime.can_access_source(source, self.user))
        self.assertIsNone(runtime.access_scope(source, self.user))
        self.assertIs(runtime.resolve_source(source, self.user), source)

    def test_group_rule_uses_same_source_without_private_connection(self):
        self.assertTrue(runtime.can_access_source(self.source, self.user))
        self.assertIs(runtime.resolve_source(self.source, self.user), self.source)
        self.assertEqual(len(runtime.access_scope(self.source, self.user)), 64)
        self.user.group_ids = [9]
        self.assertFalse(runtime.can_access_source(self.source, self.user))

    def test_matching_groups_union_exact_table_columns(self):
        other = copy.deepcopy(self.policy)
        other.update(id="other", group_ids=[4])
        other["rules"][0]["columns"] = ["Secret", "ID"]
        self.settings["data_access_group_rules"]["policies"].append(other)
        self.user.group_ids = [3, 4]
        effective = runtime.resolved_policy(self.source, self.user)
        self.assertEqual(len(effective["rules"]), 1)
        self.assertEqual(set(effective["rules"][0]["columns"]), {"ID", "Amount", "Secret"})

    def test_one_column_difference_on_another_table_separates_result_scopes(self):
        # The report itself may use only Orders.ID. Scope is source-wide:
        # a difference on Employees.Salary still separates cached results.
        self.policy["rules"].append({"catalog": None, "schema": "Sales", "table": "Employees",
                                     "column_mode": "explicit", "columns": ["ID", "Name", "Salary"]})
        broader = runtime.access_scope(self.source, self.user)
        broad_result = SimpleNamespace(data_source=self.source, policy_scope=broader)
        self.assertTrue(runtime.result_access_allowed(broad_result, self.user))
        self.policy["rules"][1]["columns"].remove("Salary")
        narrower = runtime.access_scope(self.source, self.user)
        self.assertNotEqual(broader, narrower)
        self.assertFalse(runtime.result_access_allowed(broad_result, self.user))
        narrow_result = SimpleNamespace(data_source=self.source, policy_scope=narrower)
        self.policy["rules"][1]["columns"].append("Salary")
        self.assertEqual(runtime.access_scope(self.source, self.user), broader)
        self.assertFalse(runtime.result_access_allowed(narrow_result, self.user))

    def test_admin_is_unrestricted_but_result_scope_is_separate(self):
        restricted = runtime.access_scope(self.source, self.user)
        self.permissions.add("admin")
        self.user.group_ids = [99]
        effective = runtime.resolved_policy(self.source, self.user)
        self.assertTrue(effective["unrestricted"])
        self.assertNotEqual(effective["fingerprint"], restricted)

    def test_removing_last_rule_restores_source_group_authorization(self):
        self.settings["data_access_group_rules"]["policies"] = []
        self.assertFalse(runtime.is_protected(self.source))
        self.assertIsNone(runtime.can_access_source(self.source, self.user))
        self.assertIsNone(runtime.access_scope(self.source, self.user))

    def test_rules_for_another_source_do_not_restrict_this_source(self):
        self.settings["data_access_group_rules"]["policies"][0]["data_source_id"] = 2
        self.assertFalse(runtime.is_protected(self.source))
        self.assertIsNone(runtime.can_access_source(self.source, self.user))

    def test_retired_private_ids_are_denied_even_to_admin(self):
        self.settings["data_access_internal_sources"] = [10]
        self.permissions.add("admin")
        source = SimpleNamespace(id=10, org_id=1)
        self.assertTrue(runtime.is_internal(source))
        self.assertFalse(runtime.can_access_source(source, self.user))

    def test_invalid_identity_never_inherits_group_access(self):
        for changes in ({"is_disabled": True}, {"org_id": 2}, {"is_api_user": lambda: True}):
            with self.subTest(changes=changes):
                self.assertFalse(runtime.can_access_source(self.source, SimpleNamespace(**{**vars(self.user), **changes})))
        self.assertFalse(runtime.can_access_source(self.source, None))

    def test_source_group_execution_role_and_pause_still_apply(self):
        self.groups.has_access_to_groups.return_value = False
        self.assertFalse(runtime.can_access_source(self.source, self.user))
        self.groups.has_access_to_groups.return_value = True
        self.permissions.remove("execute_query")
        with self.assertRaises(runtime.DataAccessDenied):
            runtime.ensure_execution(self.source, self.user)
        self.permissions.add("execute_query")
        self.source.paused = True
        with self.assertRaises(runtime.DataAccessDenied):
            runtime.ensure_execution(self.source, self.user)

    def test_results_require_current_exact_scope(self):
        result = SimpleNamespace(data_source=self.source, policy_scope=runtime.access_scope(self.source, self.user))
        self.assertTrue(runtime.result_access_allowed(result, self.user))
        for scope in (None, "forged"):
            result.policy_scope = scope
            self.assertFalse(runtime.result_access_allowed(result, self.user))
        result.policy_scope = runtime.access_scope(self.source, self.user)
        self.policy["rules"][0]["columns"] = ["ID"]
        self.assertFalse(runtime.result_access_allowed(result, self.user))

    def test_admin_reads_legacy_and_restricted_results_without_changing_execution_scope(self):
        result = SimpleNamespace(data_source=self.source, policy_scope=runtime.access_scope(self.source, self.user))
        self.permissions.add("admin")
        self.assertTrue(runtime.result_access_allowed(result, self.user))
        result.policy_scope = None
        self.assertTrue(runtime.result_access_allowed(result, self.user))
        self.assertIsNotNone(runtime.access_scope(self.source, self.user))
        self.settings["data_access_internal_sources"] = [self.source.id]
        self.assertFalse(runtime.result_access_allowed(result, self.user))

    def test_metadata_snapshot_is_effective_and_detached(self):
        names = runtime.schema_for(self.source, self.user)
        self.assertEqual(names[0]["name"], "Sales.Orders")
        self.assertEqual(set(names[0]["columns"]), {"ID", "Amount"})
        names[0]["columns"].append("Injected")
        self.assertNotIn("Injected", self.policy["rules"][0]["columns"])

    def test_policy_change_denies_queued_runner_before_execution(self):
        runner = runtime.ensure_execution(self.source, self.user)
        self.policy["rules"][0]["columns"] = ["ID"]
        with self.assertRaises(runtime.DataAccessDenied):
            runner.run_query("SELECT * FROM Sales.Orders", self.user)
        self.shared.run_shared_query.assert_not_called()

    def test_unrelated_revision_keeps_scope_but_connection_change_invalidates(self):
        scope = runtime.access_scope(self.source, self.user)
        self.settings["data_access_group_rules"]["revision"] += 1
        self.assertEqual(runtime.access_scope(self.source, self.user), scope)
        self.source.options["host"] = "different-endpoint"
        self.assertNotEqual(runtime.access_scope(self.source, self.user), scope)

    def test_runner_only_delegates_safe_hashing_and_format_helpers(self):
        self.runners.get_query_runner.return_value = SimpleNamespace(gen_query_hash=MagicMock(return_value="hash"))
        runner = runtime.ensure_execution(self.source, self.user)
        self.assertEqual(runner.gen_query_hash("SELECT 1"), "hash")
        for name in ("get_schema", "configuration", "test_connection"):
            with self.subTest(name=name), self.assertRaises(runtime.DataAccessDenied):
                getattr(runner, name)

    def test_malformed_state_fails_closed(self):
        for changes in ({"data_access_group_rules": {}}, {"data_access_internal_sources": [True]},
                        {"data_access_protected_sources": "all"}):
            with self.subTest(changes=changes):
                self.settings.update(changes)
                self.assertFalse(runtime.can_access_source(self.source, self.user))
                self.assertTrue(runtime.is_protected(self.source))

    def test_query_results_and_python_remain_quarantined(self):
        for connector in ("results", "python"):
            with self.subTest(connector=connector):
                self.assertFalse(runtime.can_access_source(SimpleNamespace(id=90, org_id=1, type=connector), self.user))

    def test_reader_without_matching_rules_preserves_report_access(self):
        self.user.role = "reader"
        self.user.group_ids = [99]
        self.settings["data_access_group_rules"]["policies"] = []
        self.assertIsNone(runtime.can_access_source(self.source, self.user))
        self.assertIsNone(runtime.access_scope(self.source, self.user))
        for scope in (None, "admin-scope", "editor-scope"):
            self.assertTrue(runtime.result_access_allowed(SimpleNamespace(data_source=self.source, policy_scope=scope), self.user))

    def test_reader_cannot_execute_even_on_unprotected_source(self):
        self.user.role = "reader"
        for source in (self.source, SimpleNamespace(id=99, org_id=1)):
            with self.subTest(source=source.id), self.assertRaises(runtime.DataAccessDenied):
                runtime.ensure_execution(source, self.user)
        self.runners.get_query_runner.assert_not_called()

    def test_reader_still_needs_source_groups_and_valid_identity(self):
        self.user.role = "reader"
        result = SimpleNamespace(data_source=self.source, policy_scope=None)
        self.groups.has_access_to_groups.return_value = False
        self.assertFalse(runtime.result_access_allowed(result, self.user))
        self.groups.has_access_to_groups.return_value = True
        self.user.is_disabled = True
        self.assertFalse(runtime.result_access_allowed(result, self.user))
        self.user.is_disabled = False
        self.user.org_id = 2
        self.assertFalse(runtime.result_access_allowed(result, self.user))

    def test_editor_cannot_read_broad_reader_or_admin_results(self):
        for scope in (None, "admin-scope"):
            with self.subTest(scope=scope):
                self.assertFalse(runtime.result_access_allowed(SimpleNamespace(data_source=self.source, policy_scope=scope), self.user))

if __name__ == "__main__":
    unittest.main()
