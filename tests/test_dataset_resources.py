"""Dataset resource authorization and response boundaries."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from flask import Flask, session
from werkzeug.exceptions import HTTPException

from redash.handlers import datasets


class DatasetResourceTests(TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "dataset-test-key"
        self.user = SimpleNamespace(
            is_authenticated=True, is_api_user=lambda: False, is_disabled=False,
            role="editor", org_id=1, get_id=lambda: "2",
        )
        self.org = SimpleNamespace(id=1)
        self.resource = datasets.DatasetDetailResource()
        self.enterContext(patch.object(datasets.DatasetResourceBase, "current_user", self.user))
        self.enterContext(patch.object(datasets.DatasetResourceBase, "current_org", self.org))

    def test_disabled_feature_returns_404_before_dataset_lookup(self):
        with patch.object(datasets.settings, "DATASETS_ENABLED", False), self.assertRaises(HTTPException) as error:
            self.resource.dispatch_request(dataset_id=1)
        self.assertEqual(error.exception.code, 404)

    def test_readers_and_mixed_credentials_cannot_open_detail(self):
        with patch.object(datasets.settings, "DATASETS_ENABLED", True):
            for role, authorization in (("reader", None), ("editor", "Key test")):
                self.user.role = role
                headers = {"Authorization": authorization} if authorization else {}
                with self.app.test_request_context("/api/datasets/1", headers=headers):
                    session["_user_id"] = "2"
                    with self.assertRaises(HTTPException) as error:
                        self.resource.dispatch_request(dataset_id=1)
                    self.assertEqual(error.exception.code, 403)

    def test_assigned_editor_only_receives_published_snapshot(self):
        dataset = SimpleNamespace(
            id=10, name="Unpublished rename", base_query_id=6, draft_version=3,
            draft={"fields": {"secret_draft": {"field": "draft_source"}}},
        )
        published = SimpleNamespace(revision=1, snapshot={
            "name": "Published Sales", "fields": {"total": {"field": "amount"}},
        })
        with (patch.object(datasets, "can_view_dataset", return_value=True),
              patch.object(datasets, "can_edit_dataset", return_value=False),
              patch.object(datasets, "_latest", return_value=published),
              patch.object(datasets, "_published_if_current", return_value=published) as current):
            result = self.resource._serialize(dataset, include_fields=True)
            self.assertEqual(result, {
                "id": 10, "name": "Published Sales", "can_edit": False, "published_revision": 1,
                "fields": {"total": {"field": "amount"}},
                "published_fields": ["total"],
            })
            current.return_value = None
            self.assertIsNone(self.resource._serialize(dataset, include_fields=True))
