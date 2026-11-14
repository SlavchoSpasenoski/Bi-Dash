"""Shared read-only connector helpers; per-policy DB grants are superseded."""
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

stub = ModuleType("redash.data_access_runtime")
stub.DataAccessDenied = type("DataAccessDenied", (ValueError,), {})
with patch.dict(sys.modules, {"redash": ModuleType("redash"), "redash.data_access_runtime": stub}):
    adapter = load("data_access_mysql")
    shared = load("data_access_shared")

class MySQLAccessTests(unittest.TestCase):
    def test_client_disables_files_multistatements_default_db_and_arbitrary_options(self):
        client = ModuleType("MySQLdb")
        client.connect = MagicMock()
        runners = ModuleType("redash.query_runner")
        runners.get_query_runner = MagicMock(return_value=SimpleNamespace(_get_ssl_parameters=lambda: {}))
        source = SimpleNamespace(type="mysql", uses_ssh_tunnel=False, options={"host": "fixture", "port": 3306,
            "user": "qa", "passwd": "synthetic-password", "db": "forbidden", "local_infile": 1,
            "multi_statements": True, "unix_socket": "/forbidden.sock", "init_command": "SET ROLE admin"})
        with patch.dict(sys.modules, {"MySQLdb": client, "redash.query_runner": runners}):
            adapter._connect(source, 7)
        values = client.connect.call_args.kwargs
        self.assertEqual(values["local_infile"], 0)
        self.assertIs(values["multi_statements"], False)
        for field in ("db", "database", "unix_socket", "init_command"):
            self.assertNotIn(field, values)
        self.assertEqual(values["read_timeout"], 7)

    def test_tunnels_and_other_connector_types_rejected_before_connect(self):
        for source in (SimpleNamespace(type="mysql", uses_ssh_tunnel=True), SimpleNamespace(type="pg", uses_ssh_tunnel=False)):
            with self.subTest(source=source), self.assertRaises(ValueError):
                adapter._connect(source, 7)

    def test_only_explicit_supported_versions_recognized(self):
        for version, comment, dialect in (("8.0.46", "MySQL Community Server - GPL", "mysql"),
                ("8.4.13", "MySQL Community Server - GPL", "mysql"),
                ("8.0.46-0ubuntu0.22.04.5", "(Ubuntu)", "mysql"),
                ("8.0.46-0ubuntu0.24.04.4", "Ubuntu", "mysql"),
                ("10.6.20-MariaDB-cll-lve-log", "MariaDB Server", "mariadb")):
            self.assertEqual(adapter.detect_server(version, comment), dialect)
        for version, comment in (("5.7.44", "MySQL"), ("8.1.0", "MySQL"), ("10.11.1-MariaDB", "MariaDB"),
                                 ("8.0.46", "Percona MySQL"), ("8.0.46", "Aurora MySQL"),
                                 ("8.0.46", "Ubuntu"), ("8.0.46-0ubuntu0.22.04.5", "Unknown"),
                                 ("8.0.46-0ubuntu0.22.04.5", "Percona MySQL Ubuntu"),
                                 ("8.1.0-0ubuntu0.22.04.5", "Ubuntu"),
                                 ("8.0.46-0ubuntu0.22.04.5\n", "Ubuntu")):
            with self.subTest(version=version), self.assertRaises(ValueError):
                adapter.detect_server(version, comment)


if __name__ == "__main__":
    unittest.main()
