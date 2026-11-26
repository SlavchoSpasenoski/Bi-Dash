# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Signed-in Dataset metadata and authoring endpoints."""

from contextlib import nullcontext

from flask import request, session
from flask_restful import abort
from redis.exceptions import LockError
from rq.exceptions import InvalidJobOperation, NoSuchJobError

from redash import dataset_jobs, dataset_reports, datasets, limiter, models, redis_connection, rq_redis_connection, settings
from redash.access_policy import query_limits
from redash.data_access_runtime import DataAccessDenied, resolved_policy
from redash.data_access_shared import run_shared_query
from redash.dataset_access import can_edit_dataset, can_manage_dataset_access, can_view_dataset
from redash.dataset_sql import DatasetSqlError, DatasetWildcard, dataset_source_fields, preserve_dataset_numbers
from redash.handlers.base import BaseResource, get_object_or_404, json_response
from redash.handlers.query_versions import snapshot_readable
from redash.query_revisions import snapshot_query
from redash.security import csrf
from redash.tasks.queries.execution import active_query_jobs, query_admission_key
from redash.tasks.worker import Job


def _body(keys):
    if request.content_length is None or request.content_length > 128_000:
        abort(413, message="Dataset request is too large.")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != set(keys):
        abort(400, message="Invalid Dataset request fields.")
    return payload


def _latest(dataset):
    return (models.DatasetRevision.query.filter_by(dataset_id=dataset.id)
            .order_by(models.DatasetRevision.revision.desc()).first())


def _published_if_current(dataset, published, user, org):
    if published is None:
        return None
    base = models.Query.query.filter_by(id=dataset.base_query_id, org_id=dataset.org_id).first()
    revision = models.QueryRevision.query.filter_by(
        id=published.base_revision_id, query_id=dataset.base_query_id, org_id=dataset.org_id,
    ).first()
    if (base is not None and revision is not None and snapshot_query(base) == revision.snapshot
            and snapshot_readable(revision.snapshot, user, org)):
        return published
    return None


class DatasetResourceBase(BaseResource):
    def dispatch_request(self, *args, **kwargs):
        if not settings.DATASETS_ENABLED:
            abort(404)
        user = self.current_user
        if (not user.is_authenticated or user.is_api_user() or user.is_disabled
                or user.role not in {"admin", "editor"} or user.org_id != self.current_org.id
                or session.get("_user_id") != user.get_id()
                or "api_key" in request.args or request.headers.get("Authorization")):
            abort(403, message="An active signed-in Admin or Editor is required.")
        if request.method in {"POST", "PUT"}:
            csrf.protect()
        response = super().dispatch_request(*args, **kwargs)
        response.headers["Cache-Control"] = "private, no-store"
        return response

    def _dataset(self, dataset_id):
        return get_object_or_404(models.Dataset.query.filter_by(
            id=dataset_id, org_id=self.current_org.id,
        ).first)

    def _serialize(self, dataset, include_fields=False):
        user = self.current_user
        if not can_view_dataset(dataset, user):
            return None
        editable = can_edit_dataset(dataset, user)
        published = _published_if_current(dataset, _latest(dataset), user, self.current_org)
        if not editable and published is None:
            return None
        name = dataset.name if editable else published.snapshot["name"]
        result = {"id": dataset.id, "name": name, "can_edit": editable,
                  "published_revision": published.revision if published else None}
        if editable:
            result.update(base_query_id=dataset.base_query_id, draft_version=dataset.draft_version)
        if include_fields:
            result["fields"] = dataset.draft["fields"] if editable else published.snapshot["fields"]
            if published:
                result["published_fields"] = list(published.snapshot["fields"])
        return result

    def _write(self, operation, *args):
        try:
            value = operation(*args)
            models.db.session.commit()
        except (datasets.DatasetConflict, DatasetSqlError, PermissionError) as error:
            models.db.session.rollback()
            status = 409 if isinstance(error, datasets.DatasetConflict) else 403 if isinstance(error, PermissionError) else 400
            abort(status, message=str(error))
        return value


class DatasetListResource(DatasetResourceBase):
    @limiter.limit("30/minute")
    def get(self):
        after = request.args.get("after", default=0, type=int)
        query = models.Dataset.query.filter_by(org_id=self.current_org.id, archived=False)
        results = []
        for dataset in query.filter(models.Dataset.id > after).order_by(models.Dataset.id).yield_per(50):
            item = self._serialize(dataset)
            if item is not None:
                results.append(item)
            if len(results) == 51:
                break
        return json_response({"results": results[:50], "next_after": results[49]["id"] if len(results) == 51 else None})

    def post(self):
        body = _body(("base_query_id", "name", "fields"))
        dataset = self._write(
            datasets.create_dataset, self.current_user, body["base_query_id"], body["name"], body["fields"],
        )
        return json_response(self._serialize(dataset, include_fields=True))


class DatasetSourceFieldsResource(DatasetResourceBase):
    @limiter.limit("60/minute")
    def get(self, query_id):
        base = get_object_or_404(models.Query.query.filter_by(
            id=query_id, org_id=self.current_org.id, is_archived=False,
        ).first)
        user = self.current_user
        draft = models.Dataset(org_id=self.current_org.id, user_id=user.id, base_query_id=base.id)
        if not can_edit_dataset(draft, user) or not snapshot_readable(snapshot_query(base), user, self.current_org):
            abort(403, message="Full access to the saved source Query is required.")
        source = base.data_source
        if source.type != "mysql" or source.uses_ssh_tunnel or (base.options or {}).get("dataset_report"):
            abort(400, message="Choose a saved Query on a direct MySQL or MariaDB source.")
        try:
            return json_response({"fields": dataset_source_fields(base.query_text)})
        except DatasetWildcard:
            try:
                policy = resolved_policy(source, user) or {"unrestricted": True}
                data, _ = run_shared_query(source, policy, base.query_text, dataset_source_fields=True)
                return json_response(data)
            except DatasetSqlError as error:
                abort(400, message=str(error))
            except ValueError:
                abort(400, message="Could not load supported fields from the saved Query's table.")
        except DatasetSqlError as error:
            abort(400, message=str(error))


class DatasetDetailResource(DatasetResourceBase):
    def get(self, dataset_id):
        return json_response(get_object_or_404(self._serialize, self._dataset(dataset_id), include_fields=True))

    def post(self, dataset_id):
        body = _body(("version", "name", "fields"))
        dataset = self._write(datasets.save_dataset_draft, dataset_id, self.current_user,
                              body["version"], body["name"], body["fields"])
        return json_response(self._serialize(dataset, include_fields=True))


class DatasetAccessResource(DatasetResourceBase):
    def _access(self, dataset):
        if not can_manage_dataset_access(dataset, self.current_user):
            abort(403, message="Only the owner or an administrator can manage Dataset access.")
        return json_response({
            "version": dataset.draft_version,
            "read_user_ids": [grant.user_id for grant in models.DatasetReadGrant.query.filter_by(dataset_id=dataset.id)],
            "read_group_ids": [grant.group_id for grant in models.DatasetGroupReadGrant.query.filter_by(dataset_id=dataset.id)],
            "users": [{"id": user.id, "name": user.name} for user in
                      models.User.query.filter_by(org_id=dataset.org_id).order_by(models.User.name)
                      if user.role == "editor" and not user.is_disabled and user.id != dataset.user_id],
            "groups": [{"id": group.id, "name": group.name} for group in
                       models.Group.query.filter_by(org_id=dataset.org_id).order_by(models.Group.name)],
        })

    def get(self, dataset_id):
        return self._access(self._dataset(dataset_id))

    def post(self, dataset_id):
        body = _body(("version", "read_user_ids", "read_group_ids"))
        dataset = self._write(datasets.set_dataset_grants, dataset_id, self.current_user,
                              body["version"], body["read_user_ids"], body["read_group_ids"])
        return self._access(dataset)


class DatasetJobResource(DatasetResourceBase):
    def _step(self, operation, *args):
        try:
            return operation(*args)
        except dataset_jobs.DatasetQuotaExceeded as error:
            abort(429, message=str(error))
        except datasets.DatasetConflict as error:
            abort(409, message=str(error))
        except (DataAccessDenied, PermissionError):
            abort(403, message="Dataset authoring access is unavailable.")
        except DatasetSqlError as error:
            abort(400, message=str(error))

    @limiter.limit("30/minute")
    def post(self, dataset_id):
        body = _body(("version", "operation"))
        job = self._step(dataset_jobs.enqueue_dataset_job, self._dataset(dataset_id), self.current_user,
                         body["version"], body["operation"])
        response = json_response({"id": job.id})
        response.status_code = 202
        return response

    @limiter.limit("120/minute")
    def get(self, dataset_id, job_id):
        try:
            job = Job.fetch(job_id, connection=rq_redis_connection)
            status = job.get_status()
            dataset = self._dataset(dataset_id)
            return json_response(self._step(dataset_jobs.dataset_job_response, job, dataset, self.current_user, status))
        except (NoSuchJobError, InvalidJobOperation):
            abort(404)


class DatasetReportResource(DatasetResourceBase):
    @limiter.limit("10/minute")
    def put(self, dataset_id):
        body = _body(("revision", "spec"))
        try:
            user = self.current_user
            _, published, source, base, policy, spec = dataset_reports.resolve_dataset_report(
                dataset_id, user, body["revision"], body["spec"],
            )
            limits = query_limits(user, self.current_org)
            key = query_admission_key(user.org_id, user.id)
            lock = redis_connection.lock(key + ":lock", timeout=120, blocking_timeout=5) if limits else nullcontext()
            deployment_timeout = settings.dynamic_settings.query_time_limit(None, user.id, user.org_id)
            timeout = min(5, deployment_timeout) if deployment_timeout and deployment_timeout > 0 else 5
            if limits:
                timeout = min(timeout, limits["query_timeout_seconds"])
            with lock:
                if limits and len(active_query_jobs(key)) >= limits["max_concurrent_queries"]:
                    abort(429, message="Query limit reached. Wait for a running or queued query to finish.")
                data, error = run_shared_query(
                    source, policy, base.snapshot["query"], dataset_report=(published.snapshot["fields"], spec, None),
                    dataset_report_preview=True, dataset_preview_timeout=timeout,
                )
                models.db.session.refresh(user)
                _, _, _, _, updated_policy, _ = dataset_reports.resolve_dataset_report(
                    dataset_id, user, body["revision"], body["spec"],
                )
                if policy != updated_policy:
                    raise PermissionError("The source access rules changed during preview.")
            if error or data is None:
                raise DatasetSqlError("Dataset query preview failed.")
            return json_response(preserve_dataset_numbers(data))
        except datasets.DatasetConflict as error:
            abort(409, message=str(error))
        except LockError:
            abort(429, message="Another query request is being processed. Please retry.")
        except (DataAccessDenied, PermissionError):
            abort(403, message="Dataset query preview is unavailable for this user.")
        except ValueError as error:
            abort(400, message=str(error))
    @limiter.limit("10/minute")
    def post(self, dataset_id):
        body = _body(("revision", "name", "spec"))
        value = self._write(self._create_report, dataset_id, body)
        response = json_response(value)
        response.status_code = 201
        return response

    def _create_report(self, dataset_id, body):
        from redash.data_access_shared import run_shared_query

        name = body["name"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 255:
            raise DatasetSqlError("Provide a report name of at most 255 characters.")
        user = self.current_user
        if not user.has_permission("create_query"):
            raise PermissionError("You are not permitted to create reports.")
        dataset, published, source, base, policy, spec = dataset_reports.resolve_dataset_report(
            dataset_id, user, body["revision"], body["spec"],
        )
        try:
            prepared, error = run_shared_query(
                source, policy, base.snapshot["query"],
                dataset_report=(published.snapshot["fields"], spec, None),
            )
        except ValueError as error:
            raise DatasetSqlError("This Dataset report could not be validated against the source.") from error
        if error or not prepared:
            raise DatasetSqlError("This Dataset report could not be validated against the source.")
        query = models.Query.create(
            org=self.current_org, user=user, data_source=source, name=name.strip(),
            query_text=prepared["sql"], is_draft=False, options={"dataset_report": True, "apply_auto_limit": False},
        )
        models.db.session.add(query)
        models.db.session.flush()
        dataset_reports.attach_dataset_report(query, dataset, published, spec)
        visualization_id = None
        if spec["group_by"] and spec["metrics"]:
            from redash.ai_dashboard import visualization_options
            chart_type, options = visualization_options({
                "visualization": "bar", "x": spec["group_by"][0], "y": list(spec["metrics"]),
            })
            chart = models.Visualization(
                query_rel=query, name=name.strip(), description="", type=chart_type, options=options,
            )
            models.db.session.add(chart)
            models.db.session.flush()
            visualization_id = chart.id
        models.db.session.add(models.Event(
            org_id=user.org_id, user_id=user.id, object_type="dataset", object_id=str(dataset.id),
            action="create_report", additional_properties={"query_id": query.id},
        ))
        return {"query_id": query.id, "visualization_id": visualization_id}
