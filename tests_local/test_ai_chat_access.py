"""Scoped AI identity and SQL response contracts, without Flask/DB startup."""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
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
    access = load("ai_access")
    compiler = load("data_access_sql")
    reporting = load("ai_dashboard")
    mysql = load("data_access_mysql")

class AiChatAccessTests(unittest.TestCase):
    def setUp(self):
        imports = patch.dict(sys.modules, {"redash": ModuleType("redash"),
            "redash.data_access_runtime": runtime, "redash.data_access_sql": compiler,
            "redash.ai_dashboard": reporting, "redash.data_access_mysql": mysql})
        imports.start(); self.addCleanup(imports.stop)
        self.org = SimpleNamespace(id=1)
        self.user = SimpleNamespace(org_id=1, role="editor", is_disabled=False,
            is_api_user=lambda: False, has_permission=lambda p: p == "use_ai")
        self.snapshot = {"default_schema": "sales", "policy": {"rules": [{
            "catalog": None, "schema": "sales", "table": "orders", "columns": ["id", "total"]}]}}

    def test_active_admin_and_editor_are_allowed(self):
        for role in ("admin", "editor"):
            self.user.role = role
            access.require_ai_user(self.org, self.user)

    def test_reader_api_disabled_foreign_or_policy_disabled_are_denied(self):
        for changes in ({"role": "reader"}, {"is_api_user": lambda: True},
                        {"is_disabled": True}, {"org_id": 2}, {"has_permission": lambda p: False}):
            original = vars(self.user).copy(); vars(self.user).update(changes)
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValueError):
                access.require_ai_user(self.org, self.user)
            vars(self.user).update(original)
        with self.assertRaises(ValueError):
            access.require_ai_user(self.org, None)

    def test_allowed_structured_sql_is_returned_as_sql_only(self):
        answer = access.editor_chat_answer(json.dumps({"sql": "SELECT id, total FROM orders"}), self.snapshot)
        self.assertTrue(answer.startswith("```sql\nSELECT"))
        self.assertTrue(answer.endswith("\n```"))

    def test_forbidden_dependencies_are_rejected(self):
        for sql in ("SELECT salary FROM orders", "SELECT id FROM orders WHERE salary > 0",
                    "SELECT id FROM orders ORDER BY salary", "SELECT SUM(salary) FROM orders",
                    "SELECT id FROM other_schema.orders", "DELETE FROM orders"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                access.editor_chat_answer(json.dumps({"sql": sql}), self.snapshot)

    def test_provider_prose_and_extra_fields_are_not_exposed(self):
        for answer in ("Sensitive prose", 'Explanation\n```sql\nSELECT id FROM orders\n```',
                       json.dumps({"sql": "SELECT id FROM orders", "description": "secret"})):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                access.editor_chat_answer(answer, self.snapshot)

    def test_clarification_does_not_echo_unvalidated_provider_text(self):
        answer = access.editor_chat_answer(json.dumps({"clarification": "sensitive provider prose"}), self.snapshot)
        self.assertNotIn("sensitive provider prose", answer)

    def test_fenced_sql_passes_same_dependency_checks(self):
        self.assertIn("SELECT id", access.editor_chat_answer("```sql\nSELECT id FROM orders\n```", self.snapshot))
        with self.assertRaises(ValueError):
            access.editor_chat_answer("```sql\nSELECT salary FROM orders\n```", self.snapshot)

    def test_fenced_json_from_local_model_keeps_strict_sql_validation(self):
        answer = '```json\n' + json.dumps({'sql': 'SELECT id,total FROM orders'}) + '\n```'
        self.assertIn('SELECT id,total', access.editor_chat_answer(answer, self.snapshot))
        for body in ({'sql': 'SELECT salary FROM orders'},
                     {'sql': 'SELECT id FROM orders', 'explanation': 'unvalidated text'}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                access.editor_chat_answer('```json\n' + json.dumps(body) + '\n```', self.snapshot)
        with self.assertRaises(ValueError):
            access.editor_chat_answer('Prose\n' + answer, self.snapshot)
