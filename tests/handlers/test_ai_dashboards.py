import json
import uuid
from unittest.mock import patch

from redash import models, redis_connection
from redash.utils import json_dumps
from tests import BaseTestCase


class TestAiDashboards(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.editor = self.factory.create_user(details={"role": "editor"})
        self.admin = self.factory.create_admin()
        self.reader = self.factory.create_user(details={"role": "reader"})
        self.source = self.factory.data_source
        models.db.session.commit()
        redis_connection.set(self.source._schema_key, json_dumps([
            {"name": "mdl_orders", "columns": ["month", "total"]}]))
        self.body = {"request_id": str(uuid.uuid4()), "data_source_id": self.source.id,
                     "tables": ["mdl_orders"], "prompt": "Monthly sales as a bar chart"}
        self.plan = {"title": "Sales dashboard", "description": "A draft sales report", "widgets": [{
            "title": "Monthly sales", "sql": "SELECT month, SUM(total) AS revenue FROM mdl_orders GROUP BY month",
            "visualization": "bar", "x": "month", "y": ["revenue"]}]}

    def build(self, plan=None, user=None, body=None):
        with patch("redash.handlers.ai.csrf.protect"), patch("redash.handlers.ai_dashboards.ask_assistant",
                                                           return_value=json.dumps(plan or self.plan)) as ask:
            response = self.make_request("post", "/api/ai/dashboards", data=body or self.body,
                                         user=user or self.editor)
        return response, ask

    def test_creates_real_unpublished_resources_and_chart_mapping(self):
        response, ask = self.build()
        self.assertEqual(response.status_code, 200, response.json)
        dashboard = models.db.session.get(models.Dashboard, response.json["dashboard_id"])
        self.assertTrue(dashboard.is_draft)
        self.assertEqual(dashboard.user_id, self.editor.id)
        self.assertEqual(dashboard.widgets.count(), 1)
        widget = dashboard.widgets[0]
        self.assertEqual(widget.visualization.type, "CHART")
        self.assertEqual(widget.visualization.options["columnMapping"], {"month": "x", "revenue": "y"})
        query = widget.visualization.query_rel
        self.assertTrue(query.is_draft)
        self.assertTrue(query.options["ai_generated"])
        self.assertEqual(query.query_text, self.plan["widgets"][0]["sql"])
        self.assertFalse(query.options["apply_auto_limit"])
        self.assertIsNone(query.schedule)
        self.assertTrue(ask.call_args.kwargs["dashboard"])
        self.assertIn("mdl_orders(month, total)", ask.call_args.args[3])

    def test_retry_does_not_duplicate_dashboard_or_call_provider_again(self):
        first, _ = self.build()
        second, ask = self.build()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json["dashboard_id"], second.json["dashboard_id"])
        ask.assert_not_called()
        self.assertEqual(models.Dashboard.query.count(), 1)

    def test_reader_and_source_without_query_access_are_denied(self):
        response, ask = self.build(user=self.reader)
        self.assertEqual(response.status_code, 403)
        ask.assert_not_called()
        other = self.factory.create_data_source()
        response, ask = self.build(body={**self.body, "data_source_id": other.id})
        self.assertEqual(response.status_code, 403)
        ask.assert_not_called()

    def test_missing_schema_and_invented_tables_do_not_call_ai(self):
        response, ask = self.build(body={**self.body, "tables": ["orders"]})
        self.assertEqual(response.status_code, 400)
        ask.assert_not_called()
        redis_connection.delete(self.source._schema_key)
        response, ask = self.build()
        self.assertEqual(response.status_code, 409)
        ask.assert_not_called()

    def test_invalid_plan_or_clarification_creates_no_resources(self):
        bad = {**self.plan, "widgets": [{**self.plan["widgets"][0], "sql": "DELETE FROM mdl_orders"}]}
        response, _ = self.build(plan=bad)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(models.Query.query.count(), 0)
        self.assertEqual(models.Dashboard.query.count(), 0)
        response, _ = self.build(plan={"clarification": "Which date column should be used?"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("clarification", response.json)
        self.assertEqual(models.Dashboard.query.count(), 0)

    def test_permissions_are_rechecked_after_provider_response(self):
        def demote(*args, **kwargs):
            user = models.db.session.get(models.User, self.editor.id)
            user.details = {"role": "reader"}
            models.db.session.commit()
            return json.dumps(self.plan)
        with patch("redash.handlers.ai.csrf.protect"), patch("redash.handlers.ai_dashboards.ask_assistant", side_effect=demote):
            response = self.make_request("post", "/api/ai/dashboards", data=self.body, user=self.editor)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.Dashboard.query.count(), 0)

    def test_browser_posts_require_csrf(self):
        response = self.make_request("post", "/api/ai/dashboards", data=self.body, user=self.editor)
        self.assertEqual(response.status_code, 400)
        self.assertIn("CSRF", response.get_data(as_text=True))
