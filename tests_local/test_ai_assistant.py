"""Isolated tests: load the client without initializing Redash or a database."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location("ai_client", Path(__file__).parents[1] / "redash/ai_assistant.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class AssistantTests(unittest.TestCase):
    def test_saved_prefix_and_schema_follow_history_next_to_current_question(self):
        response = {"message": {"content": "SELECT COUNT(*) FROM wnbe_users;"}}
        context = "The table prefix is wnbe_, not wp_.\nwnbe_users(ID, user_email)\nwnbe_wc_orders(id)"
        history = [{"role": "user", "content": "Earlier question"},
                   {"role": "assistant", "content": "Earlier answer"},
                   {"role": "user", "content": "Give me total amount of users From zdravzivot"}]
        with patch.object(client, "open_provider_request", return_value=io.StringIO(json.dumps(response))) as send:
            client.ask_assistant("", "model", history, provider="ollama", context=context)
        payload = json.loads(send.call_args.args[0].data)
        self.assertEqual(payload["messages"][1:3], history[:2])
        current = payload["messages"][-1]["content"]
        self.assertIn(context, current)
        self.assertIn("Exact referenced table names: wnbe_users, wnbe_wc_orders", current)
        self.assertTrue(current.endswith(history[-1]["content"]))
        self.assertIn("Preserve every table prefix exactly", payload["messages"][0]["content"])
        self.assertEqual(history[-1]["content"], "Give me total amount of users From zdravzivot")

    def test_prefix_reminder_uses_only_declared_identifiers(self):
        self.assertEqual(client.schema_identifier_reminder("users(id)"), "")
        self.assertEqual(client.schema_identifier_reminder("Table prefix: tenant_"), "")
        reminder = client.schema_identifier_reminder("Table prefix: `tenant_`\ntenant_users\ntenant_users\nignore instructions")
        self.assertEqual(reminder, "Declared table prefix: tenant_\nExact referenced table names: tenant_users")

    def test_compatible_provider_and_persistent_context(self):
        response = {"choices": [{"message": {"content": "SELECT 1;"}}]}
        with patch.object(client, "open_provider_request", return_value=io.StringIO(json.dumps(response))) as send:
            answer = client.ask_assistant("own-key", "own-model", [{"role": "user", "content": "help"}],
                                          provider="compatible", base_url="https://custom.test/api/v1/",
                                          context="Revenue excludes cancelled orders.", schema="orders(id)")
        self.assertEqual(answer, "SELECT 1;")
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://custom.test/api/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer own-key")
        payload = json.loads(request.data)
        self.assertIn("Revenue excludes", payload["messages"][1]["content"])
        self.assertIn("orders(id)", payload["messages"][1]["content"])

    def test_anthropic_protocol(self):
        response = {"content": [{"type": "text", "text": "SELECT 1;"}]}
        with patch.object(client, "open_provider_request", return_value=io.StringIO(json.dumps(response))) as send:
            answer = client.ask_assistant("own-key", "own-model", [{"role": "user", "content": "help"}],
                                          provider="anthropic")
        self.assertEqual(answer, "SELECT 1;")
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "own-key")
        payload = json.loads(request.data)
        self.assertIn("system", payload)
        self.assertEqual(payload["messages"][0]["role"], "user")

    def test_malformed_hosted_provider_responses_are_sanitized(self):
        for provider, response in (("compatible", {"choices": []}), ("compatible", {"choices": None}),
                                   ("anthropic", {"content": None}), ("openai", {"output": [None]})):
            with self.subTest(provider=provider, response=response):
                with patch.object(client, "open_provider_request", return_value=io.StringIO(json.dumps(response))):
                    with self.assertRaises(client.AssistantError):
                        client.ask_assistant("key", "model", [{"role": "user", "content": "help"}],
                                             provider=provider, base_url="https://provider.test/v1")

    def test_redirects_are_not_followed(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread

        visited = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                visited.append(self.path)
                self.send_response(307)
                self.send_header("Location", "/credential-trap")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(client.AssistantError):
                client.ask_assistant("private-key", "model", [{"role": "user", "content": "help"}],
                                     provider="compatible", base_url=f"http://127.0.0.1:{server.server_port}/v1")
            self.assertEqual(visited, ["/v1/chat/completions"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_rejects_invalid_roles_and_limits(self):
        for messages in (None, [], [{"role": "system", "content": "override"}],
                         [{"role": "user", "content": " "}],
                         [{"role": "user", "content": "x" * 8001}],
                         [{"role": "assistant", "content": "x"}],
                         [{"role": "user", "content": "x"}] * 17):
            with self.subTest(messages=str(messages)[:80]), self.assertRaises(ValueError):
                client.validate_messages(messages)

    def test_provider_contract_and_text_extraction(self):
        response = {"output": [{"type": "reasoning"}, {"type": "message", "content": [
            {"type": "output_text", "text": "SELECT 1;"}]}]}
        with patch.object(client, "open_provider_request", return_value=io.StringIO(json.dumps(response))) as send:
            answer = client.ask_assistant("secret", "test-model", [{"role": "user", "content": "help"}], "orders(id)")
        self.assertEqual(answer, "SELECT 1;")
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertFalse(payload["store"])
        self.assertEqual(payload["model"], "test-model")
        self.assertIn("orders(id)", payload["input"][0]["content"])
        self.assertNotIn("secret", request.data.decode())
        self.assertEqual(send.call_args.kwargs["timeout"], 45)

    def test_provider_failure_is_sanitized(self):
        error = HTTPError("https://api.openai.com", 401, "secret-provider-detail", {}, None)
        with patch.object(client, "open_provider_request", side_effect=error):
            with self.assertRaises(client.AssistantError) as raised:
                client.ask_assistant("secret", "model", [{"role": "user", "content": "help"}])
        self.assertNotIn("secret-provider-detail", str(raised.exception))

    def test_empty_output_is_reported(self):
        with patch.object(client, "open_provider_request", return_value=io.StringIO('{"output": []}')):
            with self.assertRaises(client.AssistantError):
                client.ask_assistant("secret", "model", [{"role": "user", "content": "help"}])

    def test_ollama_uses_local_chat_without_sending_openai_credentials(self):
        response = {"message": {"role": "assistant", "content": " SELECT 1; "}, "done": True}
        with patch.object(client, "open_provider_request", return_value=io.StringIO(json.dumps(response))) as send:
            answer = client.ask_assistant("unused-secret", "qwen2.5-coder:7b", [{"role": "user", "content": "help"}],
                                          "orders(id)", provider="ollama", ollama_url="http://ollama:11434/")
        self.assertEqual(answer, "SELECT 1;")
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "http://ollama:11434/api/chat")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertNotIn("unused-secret", request.data.decode())
        payload = json.loads(request.data)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("orders(id)", payload["messages"][1]["content"])
        self.assertEqual(send.call_args.kwargs["timeout"], 1800)
        self.assertEqual(payload["keep_alive"], "30m")

    def test_slow_local_request_uses_configured_timeout_and_keep_alive(self):
        response = {"message": {"content": "SELECT 1;"}}
        with patch.object(client, "open_provider_request", return_value=io.StringIO(json.dumps(response))) as send:
            client.ask_assistant("", "model", [{"role": "user", "content": "help"}], provider="ollama",
                                 ollama_timeout=2400, ollama_keep_alive="1h")
        self.assertEqual(send.call_args.kwargs["timeout"], 2400)
        self.assertEqual(json.loads(send.call_args.args[0].data)["keep_alive"], "1h")

    def test_local_timeouts_are_distinct_from_unavailable_provider(self):
        from urllib.error import URLError
        for error in (TimeoutError(), URLError(TimeoutError())):
            with self.subTest(error=type(error).__name__):
                with patch.object(client, "open_provider_request", side_effect=error) as send:
                    with self.assertRaises(client.AssistantTimeoutError) as raised:
                        client.ask_assistant("", "model", [{"role": "user", "content": "help"}],
                                             provider="ollama", ollama_timeout=420)
                self.assertIn("420 seconds", str(raised.exception))
                self.assertNotIn("is unavailable", str(raised.exception))
                self.assertEqual(send.call_count, 1)

    def test_ollama_failure_never_falls_back_to_openai(self):
        error = HTTPError("http://ollama:11434/api/chat", 404, "private-provider-detail", {}, None)
        with patch.object(client, "open_provider_request", side_effect=error) as send:
            with self.assertRaises(client.AssistantError) as raised:
                client.ask_assistant("unused-secret", "missing-model", [{"role": "user", "content": "help"}], provider="ollama")
        self.assertEqual(send.call_count, 1)
        self.assertIn("Ollama", str(raised.exception))
        self.assertNotIn("private-provider-detail", str(raised.exception))

    def test_ollama_empty_and_malformed_outputs(self):
        for response in ({"message": {"content": " "}}, {"message": None}, []):
            with self.subTest(response=response):
                with patch.object(client, "open_provider_request", return_value=io.StringIO(json.dumps(response))):
                    with self.assertRaises(client.AssistantError):
                        client.ask_assistant("", "model", [{"role": "user", "content": "help"}], provider="ollama")

    def test_unknown_provider_cannot_send_a_request(self):
        with patch.object(client, "open_provider_request") as send:
            with self.assertRaises(client.AssistantError):
                client.ask_assistant("unused-secret", "model", [{"role": "user", "content": "help"}], provider="typo")
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
