"""Dataset job admission, execution and result authorization."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from rq.job import JobStatus

from redash import dataset_jobs


class DatasetJobTests(TestCase):
    def setUp(self):
        self.dataset = SimpleNamespace(id=10, draft_version=3)
        self.user = SimpleNamespace(id=2, org_id=1)
        self.source = SimpleNamespace(id=5, org=object(), queue_name="queries")
        self.binding = self.enterContext(patch.object(
            dataset_jobs, "_binding", return_value=(self.source, "current-binding")
        ))
        self.enterContext(patch.object(dataset_jobs, "can_edit_dataset", return_value=True))

    def test_preview_shares_query_quota_and_respects_timeout(self):
        limits = {"query_timeout_seconds": 8, "max_concurrent_queries": 1}
        with (patch.object(dataset_jobs, "query_limits", return_value=limits),
              patch.object(dataset_jobs.settings.dynamic_settings, "query_time_limit", return_value=20),
              patch.object(dataset_jobs, "redis_connection") as redis,
              patch.object(dataset_jobs, "active_query_jobs", return_value=[Mock(meta={})]) as active,
              patch.object(dataset_jobs, "Queue") as queue):
            redis.lock.return_value = nullcontext()
            with self.assertRaises(dataset_jobs.DatasetQuotaExceeded):
                dataset_jobs.enqueue_dataset_job(self.dataset, self.user, 3, "preview")
            key = dataset_jobs.query_admission_key(1, 2)
            active.assert_called_with(key)
            queue.return_value.enqueue_call.assert_not_called()
            active.return_value = []
            queued = dataset_jobs.enqueue_dataset_job(self.dataset, self.user, 3, "preview")
            parameters = queue.return_value.enqueue_call.call_args.kwargs
            self.assertEqual(parameters["timeout"], 8)
            self.assertEqual(parameters["meta"]["binding"], "current-binding")
            redis.sadd.assert_called_once_with(key, queued.id)

    def test_poll_denies_non_submitter_and_stale_draft(self):
        job = Mock(id="job-id", meta={
            "dataset_authoring": True, "dataset_id": 10, "version": 3,
            "user_id": 2, "org_id": 1, "binding": "current-binding",
        })
        job.get_status.return_value = JobStatus.FINISHED
        job.return_value.return_value = {"version": 3, "preview": {"rows": [{"total": 42}]}}
        self.assertEqual(dataset_jobs.dataset_job_response(job, self.dataset, self.user)["status"], "finished")
        with self.assertRaises(PermissionError):
            dataset_jobs.dataset_job_response(job, self.dataset, SimpleNamespace(id=8, org_id=1))
        self.dataset.draft_version = 4
        with self.assertRaises(dataset_jobs.datasets.DatasetConflict):
            dataset_jobs.dataset_job_response(job, self.dataset, self.user)

    def test_worker_rejects_changed_source_before_execution(self):
        meta = {"dataset_id": 10, "user_id": 2, "org_id": 1, "version": 3,
                "operation": "preview", "binding": "old-binding"}
        with (patch.object(dataset_jobs.settings, "DATASETS_ENABLED", True),
              patch.object(dataset_jobs, "models") as models,
              patch.object(dataset_jobs.datasets, "_lock_dataset", return_value=self.dataset),
              patch.object(dataset_jobs.datasets, "preview_dataset_draft") as preview):
            models.User.query.filter_by.return_value.first.return_value = self.user
            with self.assertRaises(dataset_jobs.datasets.DatasetConflict):
                dataset_jobs.execute_dataset_job(meta)
            preview.assert_not_called()
            models.db.session.rollback.assert_called_once()
