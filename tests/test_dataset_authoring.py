"""Dataset authoring transaction contracts without application metadata."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from redash import datasets
from redash.dataset_sql import DatasetSqlError


class DatasetAuthoringTests(TestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=2, org_id=1)
        self.dataset = SimpleNamespace(id=10, user_id=2, org_id=1, draft_version=1, name="Sales",
                                       draft={"fields": {"total": {"field": "amount"}}})
        self.models = Mock()
        query = self.models.Dataset.query.filter_by.return_value
        locked_query = query.populate_existing.return_value.with_for_update.return_value
        locked_query.first.return_value = self.dataset
        self.models.User.query.filter_by.return_value.filter.return_value.all.return_value = []
        self.models.Group.query.filter_by.return_value.filter.return_value.all.return_value = []
        self.enterContext(patch.object(datasets, "models", self.models))
        self.editor = self.enterContext(patch.object(datasets, "can_edit_dataset", return_value=True))
        self.enterContext(patch.object(datasets, "can_manage_dataset_access", return_value=True))

    def test_draft_save_locks_row_and_rejects_stale_writer(self):
        fields = {"total": {"field": "revenue"}}
        datasets.save_dataset_draft(10, self.user, 1, " Net sales ", fields)
        self.assertEqual(self.dataset.draft_version, 2)
        self.assertEqual(self.dataset.name, "Net sales")
        self.models.Dataset.query.filter_by.assert_called_with(id=10, org_id=1)
        with self.assertRaises(datasets.DatasetConflict):
            datasets.save_dataset_draft(10, self.user, 1, "Wrong name", fields)
        self.assertEqual(self.dataset.name, "Net sales")

    def test_draft_rejects_invalid_formulas_and_denied_edits(self):
        with self.assertRaises(DatasetSqlError):
            datasets.save_dataset_draft(10, self.user, 1, "Sales", {"total": {"sql": "1"}})
        self.editor.return_value = False
        with self.assertRaises(PermissionError):
            datasets.save_dataset_draft(10, self.user, 1, "Sales", self.dataset.draft["fields"])
        self.assertEqual(self.dataset.draft_version, 1)

    def test_grants_reject_unpublished_dataset_and_foreign_recipients(self):
        published = self.models.DatasetRevision.query.filter_by.return_value.first
        published.return_value = None
        with self.assertRaisesRegex(DatasetSqlError, "Publish"):
            datasets.set_dataset_grants(10, self.user, 1, [4], [])
        published.return_value = object()
        with self.assertRaisesRegex(DatasetSqlError, "active Editors"):
            datasets.set_dataset_grants(10, self.user, 1, [4], [])
        with self.assertRaisesRegex(DatasetSqlError, "groups in this organization"):
            datasets.set_dataset_grants(10, self.user, 1, [], [5])
        self.models.db.session.add.assert_not_called()
        self.models.db.session.delete.assert_not_called()

    def test_grant_changes_revoke_and_assign_in_same_version(self):
        old = SimpleNamespace(user_id=3)
        self.models.DatasetReadGrant.query.filter_by.return_value = [old]
        self.models.DatasetGroupReadGrant.query.filter_by.return_value = []
        self.models.User.query.filter_by.return_value.filter.return_value.all.return_value = [
            SimpleNamespace(id=4, role="editor", is_disabled=False)
        ]
        datasets.set_dataset_grants(10, self.user, 1, [4], [])
        self.models.db.session.delete.assert_called_once_with(old)
        self.models.DatasetReadGrant.assert_called_once_with(dataset_id=10, user_id=4)
        self.assertEqual(self.dataset.draft_version, 2)
        self.models.db.session.commit.assert_not_called()

    def test_publish_pins_validated_query_revision_and_keeps_snapshot_separate(self):
        self.models.DatasetRevision.query.filter_by.return_value.order_by.return_value.first.return_value = None
        with patch.object(datasets, "_preview_locked_draft", return_value=(SimpleNamespace(id=19), {})):
            datasets.publish_dataset(10, self.user, 1)
        self.models.DatasetRevision.assert_called_once_with(
            dataset_id=10, revision=1, base_revision_id=19, actor_user_id=2,
            snapshot={"name": "Sales", **self.dataset.draft},
        )
        self.assertIsNot(self.models.DatasetRevision.call_args.kwargs["snapshot"]["fields"],
                         self.dataset.draft["fields"])
        self.assertEqual(self.dataset.draft_version, 2)

    def test_failed_preview_creates_no_published_revision(self):
        with patch.object(datasets, "_preview_locked_draft", side_effect=DatasetSqlError("Rejected")):
            with self.assertRaisesRegex(DatasetSqlError, "Rejected"):
                datasets.publish_dataset(10, self.user, 1)
        self.models.db.session.add.assert_not_called()
        self.assertEqual(self.dataset.draft_version, 1)
