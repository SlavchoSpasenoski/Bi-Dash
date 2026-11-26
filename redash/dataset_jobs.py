# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Dataset previews and publication using ordinary query admission and RQ."""

import hashlib
import hmac

from rq.job import JobStatus

from redash import datasets, models, redis_connection, rq_redis_connection, settings
from redash.access_policy import query_limits
from redash.dataset_access import can_edit_dataset
from redash.dataset_sql import DatasetSqlError, preserve_dataset_numbers
from redash.tasks.queries.execution import active_query_jobs, query_admission_key
from redash.tasks.worker import Queue
from redash.utils import json_dumps


class DatasetQuotaExceeded(ValueError):
    pass


def _binding(dataset, user, *, lock=False):
    source, revision, policy = datasets.authoring_source(dataset, user, lock=lock)
    key = settings.DATASOURCE_SECRET_KEY
    if isinstance(key, str):
        key = key.encode()
    options = source.options.to_dict() if hasattr(source.options, "to_dict") else dict(source.options)
    context = json_dumps([source.id, options, policy.get("fingerprint"), revision.id], sort_keys=True)
    return source, hmac.new(key, context.encode(), hashlib.sha256).hexdigest()


def enqueue_dataset_job(dataset, user, version, operation):
    if not isinstance(operation, str) or operation not in {"preview", "publish"}:
        raise DatasetSqlError("Invalid Dataset job.")
    if not can_edit_dataset(dataset, user):
        raise PermissionError("Full access to the saved base Query is required.")
    if type(version) is not int or version != dataset.draft_version:
        raise datasets.DatasetConflict("The Dataset changed. Reload it before continuing.")
    source, binding = _binding(dataset, user)
    limits = query_limits(user, source.org)
    deployment_limit = settings.dynamic_settings.query_time_limit(None, user.id, user.org_id)
    timeout = min(60, deployment_limit) if deployment_limit and deployment_limit > 0 else 60
    if limits:
        timeout = min(timeout, limits["query_timeout_seconds"])
    meta = {
        "dataset_authoring": True, "operation": operation, "dataset_id": dataset.id, "version": version,
        "binding": binding, "user_id": user.id, "org_id": user.org_id, "data_source_id": source.id,
    }
    queue = Queue(source.queue_name, connection=rq_redis_connection)

    def enqueue():
        return queue.enqueue_call(
            execute_dataset_job, args=(meta,), timeout=timeout, ttl=120,
            result_ttl=120, failure_ttl=120, meta=meta,
        )

    if limits is None:
        return enqueue()
    key = query_admission_key(user.org_id, user.id)
    with redis_connection.lock(key + ":lock", timeout=15, blocking_timeout=5):
        active = active_query_jobs(key)
        for job in active:
            if job.meta == meta:
                return job
        if len(active) >= limits["max_concurrent_queries"]:
            raise DatasetQuotaExceeded("Query limit reached. Wait for a running or queued query to finish.")
        job = enqueue()
        redis_connection.sadd(key, job.id)
        return job


def execute_dataset_job(meta):
    if not settings.DATASETS_ENABLED:
        raise PermissionError("Dataset authoring is disabled.")
    user = models.User.query.filter_by(id=meta["user_id"], org_id=meta["org_id"]).first()
    if user is None:
        raise PermissionError("Dataset authoring requires an active organization user.")
    try:
        dataset = datasets._lock_dataset(meta["dataset_id"], user, meta["version"], can_edit_dataset)
        _, binding = _binding(dataset, user, lock=True)
        if binding != meta["binding"]:
            raise datasets.DatasetConflict("The base Query or source access changed while the job was queued.")
        if meta["operation"] == "publish":
            revision = datasets.publish_dataset(dataset.id, user, meta["version"])
            result = {"published_revision": revision.revision}
        elif meta["operation"] == "preview":
            data = preserve_dataset_numbers(datasets.preview_dataset_draft(dataset.id, user, meta["version"]))
            if len(json_dumps(data).encode()) > 256_000:
                raise DatasetSqlError("Dataset preview exceeded the response size limit.")
            result = {"preview": data}
        else:
            raise DatasetSqlError("Invalid Dataset job.")
        models.db.session.refresh(user)
        _, current_binding = _binding(dataset, user, lock=True)
        if current_binding != meta["binding"]:
            raise datasets.DatasetConflict("The base Query or source access changed during execution.")
        models.db.session.commit()
        return {"version": dataset.draft_version, **result}
    except Exception:
        models.db.session.rollback()
        raise


def dataset_job_response(job, dataset, user, status=None):
    meta = job.meta
    if (not meta.get("dataset_authoring") or meta["user_id"] != user.id or meta["org_id"] != user.org_id
            or meta["dataset_id"] != dataset.id or not can_edit_dataset(dataset, user)):
        raise PermissionError("You do not have access to this Dataset job.")
    _, binding = _binding(dataset, user)
    if binding != meta["binding"]:
        raise datasets.DatasetConflict("The base Query or source access changed. Run the request again.")
    status = job.get_status() if status is None else status
    result = job.return_value() if status == JobStatus.FINISHED else None
    if result is not None and dataset.draft_version != result["version"]:
        raise datasets.DatasetConflict("The Dataset changed. Run the request again.")
    return {"id": job.id, "status": status.value, "result": result}
