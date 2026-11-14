"""Exercise the real AI access guard and chat handlers without initializing Flask/DB."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class AiChatAccessTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).parents[1] / 'redash/handlers/ai.py').read_text(encoding='utf-8'))
        guard = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'require_unprotected_ai')
        chat = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AssistantResource')
        methods = [n for n in chat.body if isinstance(n, ast.FunctionDef)]
        for method in methods:
            method.decorator_list = []
        self.abort = Mock(side_effect=PermissionError('denied'))
        self.ask = Mock(return_value='test answer')
        self.ns = {'abort': self.abort, 'ai_configuration': Mock(return_value={
            'provider': 'ollama', 'model': 'test', 'enabled': True, 'timeout_seconds': 1,
            'base_url': 'http://ollama:11434', 'api_key': ''}),
            'json_body': lambda: {'messages': [{'role': 'user', 'content': 'hello'}]},
            'validate_messages': lambda value: value, 'ask_assistant': self.ask,
            'os': SimpleNamespace(environ={})}
        exec(compile(ast.Module(body=[guard, *methods], type_ignores=[]), '<actual AI handlers>', 'exec'), self.ns)
        self.org = SimpleNamespace(id=1, settings={'data_access_protected_sources': [1]})
        self.user = SimpleNamespace(org_id=1, is_disabled=False, is_api_user=lambda: False,
                                    has_permission=lambda p: p == 'admin')
        self.resource = SimpleNamespace(current_org=self.org, current_user=self.user)

    def test_admin_can_get_configuration_and_chat(self):
        self.assertTrue(self.ns['get'](self.resource)['enabled'])
        self.assertEqual(self.ns['post'](self.resource), {'answer': 'test answer'})
        self.ask.assert_called_once()
        self.abort.assert_not_called()

    def test_editor_and_reader_remain_denied_before_provider_call(self):
        self.user.has_permission = lambda p: False
        for method in ('get', 'post'):
            with self.assertRaises(PermissionError):
                self.ns[method](self.resource)
        self.ask.assert_not_called()

    def test_api_admin_disabled_and_foreign_org_denied(self):
        for changes in ({'is_api_user': lambda: True}, {'is_disabled': True}, {'org_id': 2}):
            original = vars(self.user).copy()
            vars(self.user).update(changes)
            with self.assertRaises(PermissionError):
                self.ns['require_unprotected_ai'](self.org, ai_user=self.user)
            vars(self.user).update(original)

    def test_guard_without_trusted_user_remains_denied(self):
        with self.assertRaises(PermissionError):
            self.ns['require_unprotected_ai'](self.org)

    def test_unprotected_organization_preserves_access(self):
        self.org.settings = {}
        self.user.has_permission = lambda p: False
        self.assertTrue(self.ns['get'](self.resource)['enabled'])


if __name__ == '__main__':
    unittest.main()
