import hashlib
import signal
import sys
import time
from collections import deque

import redis
from rq import get_current_job
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import JobStatus
from rq.timeouts import JobTimeoutException

from redash import models, redis_connection, rq_redis_connection, settings
from redash.query_runner import InterruptException
from redash.tasks.alerts import check_alerts_for_query
from redash.tasks.failure_report import track_failure
from redash.tasks.worker import Job, Queue
from redash.utils import gen_query_hash, utcnow
from redash.worker import get_job_logger

logger = get_job_logger(__name__)
TIMEOUT_MESSAGE = "Query exceeded Bi-Dash query execution time limit."


def _job_lock_id(query_hash, data_source_id, scope=None):
    key = "query_hash_job:%s:%s" % (data_source_id, query_hash)
    return key + ":" + scope if scope else key


def _unlock(query_hash, data_source_id, scope=None):
    redis_connection.delete(_job_lock_id(query_hash, data_source_id, scope))


def enqueue_query(query, data_source, user_id, is_api_key=False, scheduled_query=None, metadata=None):
    from redash.access_policy import query_limits
    from redash.ai_dashboard import SUPPORTED_SOURCES, validate_readonly_sql

    metadata = dict(metadata or {})
    if metadata.get("query_id") not in (None, "adhoc"):
        try:
            metadata["query_id"] = int(metadata["query_id"])
        except (TypeError, ValueError):
            raise QueryExecutionError("A valid saved query ID is required.")
    metadata.pop("policy_scope", None)
    report = metadata.get("reader_report")
    dashboard_report = metadata.get("dashboard_report")
    if dashboard_report:
        from redash.reader_reports import authorize_editor_report
        try:
            if is_api_key or scheduled_query or user_id is None:
                raise ValueError("Dashboard reports require an interactive user.")
            actor = models.User.get_by_id(user_id)
            source, _, _ = authorize_editor_report(actor, dashboard_report, expected_sql=query)
            if source.id != data_source.id or metadata.get("query_id") != dashboard_report["query_id"]:
                raise ValueError("Mismatched dashboard report source.")
        except ValueError as error:
            raise QueryExecutionError(str(error)) from error
    if report:
        from redash.reader_reports import authorize_report
        try:
            if is_api_key or scheduled_query or user_id is None:
                raise ValueError("Reader reports require an interactive user.")
            actor = models.User.get_by_id(user_id)
            source, _, report_scope = authorize_report(actor, report, expected_sql=query)
            if source.id != data_source.id or metadata.get("query_id") != report["query_id"]:
                raise ValueError("Mismatched saved report source.")
            metadata["policy_scope"] = report_scope
        except ValueError as error:
            raise QueryExecutionError(str(error)) from error
    from redash.data_access_runtime import DataAccessDenied, access_scope, is_protected, is_reader, resolve_source
    if is_protected(data_source) and not report:
        if is_api_key or user_id is None or scheduled_query:
            raise QueryExecutionError("Protected sources do not support shared tokens or schedules.")
        try:
            actor = models.User.get_by_id(user_id)
            if is_reader(actor):
                raise QueryExecutionError("Readers cannot execute queries.")
            data_source = resolve_source(data_source, actor)
            metadata["policy_scope"] = access_scope(data_source, actor)
            metadata["access_scope"] = "policy:" + metadata["policy_scope"]
        except DataAccessDenied as error:
            raise QueryExecutionError(str(error)) from error
    metadata.pop("ai_read_only", None)
    saved_query_id = metadata.get("query_id")
    if saved_query_id not in (None, "adhoc"):
        saved = models.Query.query.filter_by(id=saved_query_id, org_id=data_source.org_id).first()
        if saved and (saved.options or {}).get("ai_generated"):
            if data_source.type not in SUPPORTED_SOURCES:
                raise QueryExecutionError("Automatic AI report execution supports PostgreSQL and MySQL/MariaDB.")
            try:
                validate_readonly_sql(query)
            except ValueError as error:
                raise QueryExecutionError(str(error)) from error
            metadata["ai_read_only"] = True
            metadata["query_timeout_seconds"] = 60

    user = None if is_api_key or user_id is None else models.User.get_by_id(user_id)
    if is_api_key:
        raise QueryExecutionError("Anonymous query execution is unavailable; sign in with assigned dashboard access.")
    if saved_query_id not in (None, "adhoc"):
        from redash.permissions import has_access, view_only
        saved = models.Query.query.filter_by(id=saved_query_id, org_id=data_source.org_id, is_archived=False).first()
        if (saved is None or saved.data_source_id != data_source.id or user is None
                or not has_access(saved, user, view_only)):
            raise QueryExecutionError("This saved query is no longer accessible.")
    if is_reader(user) and not report:
        raise QueryExecutionError("Readers cannot execute queries.")
    if not report:
        from redash.permissions import has_access, not_view_only
        if (user is None or user.org_id != data_source.org_id
                or not has_access(data_source, user, not_view_only)):
            raise QueryExecutionError("Full access to this data source is required to execute queries.")
    if report:
        metadata["ai_read_only"] = True
        metadata["query_timeout_seconds"] = min(60, metadata.get("query_timeout_seconds", 60))
    public_owner = None
    if is_api_key and metadata and metadata.get("query_id") not in (None, "adhoc"):
        public_owner = models.Query.get_by_id_and_org(metadata["query_id"], data_source.org).user
        if is_reader(public_owner) or public_owner.is_disabled or not public_owner.has_permission("execute_query"):
            raise QueryExecutionError("This report's owner can no longer refresh it.")
    if user is not None and (user.is_disabled or (not report and not user.has_permission("execute_query"))):
        raise QueryExecutionError("Your role cannot execute queries.")
    if scheduled_query and user is not None and not user.has_permission("schedule_query"):
        raise QueryExecutionError("Scheduling is disabled for this user.")
    limits = query_limits(user, data_source.org)
    if public_owner is not None:
        owner_limits = query_limits(public_owner, data_source.org)
        if owner_limits:
            for field in ("query_timeout_seconds", "max_concurrent_queries"):
                limits[field] = min(limits[field], owner_limits[field])
    if limits is None:
        if dashboard_report:
            metadata["access_scope"] = (metadata.get("access_scope") or "dashboard") + ":" + dashboard_report["authorization_scope"]
        return _enqueue_query(query, data_source, user_id, is_api_key, scheduled_query, metadata)
    actor_id = public_owner.id if public_owner is not None else user_id
    quota_scope = hashlib.sha256(str(actor_id).encode()).hexdigest()[:24]
    scope = (quota_scope + ":api:" + hashlib.sha256(str(user_id).encode()).hexdigest()[:24]) if is_api_key else quota_scope
    key = f"access:queries:{data_source.org_id}:{quota_scope}"
    metadata["access_scope"] = scope
    if metadata.get("policy_scope"):
        metadata["access_scope"] += ":" + metadata["policy_scope"]
    if dashboard_report:
        metadata["access_scope"] += ":dashboard:" + dashboard_report["authorization_scope"]
    metadata["query_timeout_seconds"] = min(limits["query_timeout_seconds"], metadata.get("query_timeout_seconds", 3600))
    # The distributed lock makes simultaneous requests share the same admission limit.
    with redis_connection.lock(key + ":lock", timeout=15, blocking_timeout=5):
        active = []
        for job_id in redis_connection.smembers(key):
            try:
                existing = Job.fetch(job_id, connection=rq_redis_connection)
                if existing.get_status() in (JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED):
                    active.append(existing)
                else:
                    redis_connection.srem(key, job_id)
            except (NoSuchJobError, InvalidJobOperation):
                redis_connection.srem(key, job_id)
        query_hash = gen_query_hash(query)
        shared_id = redis_connection.get(_job_lock_id(query_hash, data_source.id, metadata["access_scope"]))
        for existing in active:
            if shared_id and existing.id == (shared_id.decode() if isinstance(shared_id, bytes) else shared_id):
                return existing
        if len(active) >= limits["max_concurrent_queries"]:
            raise QueryExecutionError("Query limit reached. Wait for a running or queued query to finish.")
        job = _enqueue_query(query, data_source, user_id, is_api_key, scheduled_query, metadata)
        if job:
            redis_connection.sadd(key, job.id)
        return job


def _enqueue_query(query, data_source, user_id, is_api_key=False, scheduled_query=None, metadata=None):
    metadata = dict(metadata or {})
    query_hash = gen_query_hash(query)
    lock_id = _job_lock_id(query_hash, data_source.id, metadata.get("access_scope"))
    logger.info("Inserting job for %s with metadata=%s", query_hash, metadata)
    try_count = 0
    job = None

    while try_count < 5:
        try_count += 1

        pipe = redis_connection.pipeline()
        try:
            pipe.watch(lock_id)
            job_id = pipe.get(lock_id)
            if job_id:
                logger.info("[%s] Found existing job: %s", query_hash, job_id)
                job_complete = None
                job_cancelled = None

                try:
                    job = Job.fetch(job_id, connection=rq_redis_connection)
                    job_exists = True
                    status = job.get_status()
                    job_complete = status in [JobStatus.FINISHED, JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELED]
                    job_cancelled = job.is_cancelled

                    if job_complete:
                        message = "job found is complete (%s)" % status
                    elif job_cancelled:
                        message = "job found has been cancelled"
                except (NoSuchJobError, InvalidJobOperation):
                    message = "job found has expired"
                    job_exists = False

                lock_is_irrelevant = job_complete or job_cancelled or not job_exists

                if lock_is_irrelevant:
                    logger.info("[%s] %s, removing lock", query_hash, message)
                    redis_connection.delete(lock_id)
                    job = None

            if not job:
                pipe.multi()

                if scheduled_query:
                    queue_name = data_source.scheduled_queue_name
                    scheduled_query_id = scheduled_query.id
                else:
                    queue_name = data_source.queue_name
                    scheduled_query_id = None

                time_limit = settings.dynamic_settings.query_time_limit(scheduled_query, user_id, data_source.org_id)
                role_timeout = metadata.get("query_timeout_seconds")
                if role_timeout:
                    time_limit = min(time_limit, role_timeout) if time_limit and time_limit > 0 else role_timeout
                metadata["Queue"] = queue_name

                queue = Queue(queue_name, connection=rq_redis_connection)
                enqueue_kwargs = {
                    "user_id": user_id,
                    "scheduled_query_id": scheduled_query_id,
                    "is_api_key": is_api_key,
                    "job_timeout": time_limit,
                    "failure_ttl": settings.JOB_DEFAULT_FAILURE_TTL,
                    "meta": {
                        "data_source_id": data_source.id,
                        "org_id": data_source.org_id,
                        "scheduled": scheduled_query_id is not None,
                        "query_id": metadata.get("query_id"),
                        "user_id": user_id,
                        "ai_read_only": metadata.get("ai_read_only", False),
                        "reader_report": metadata.get("reader_report"),
                        "dashboard_report": metadata.get("dashboard_report"),
                        "policy_scope": metadata.get("policy_scope"),
                    },
                }

                if not scheduled_query:
                    enqueue_kwargs["result_ttl"] = settings.JOB_EXPIRY_TIME

                job = queue.enqueue(execute_query, query, data_source.id, metadata, **enqueue_kwargs)

                logger.info("[%s] Created new job: %s", query_hash, job.id)
                pipe.set(
                    lock_id,
                    job.id,
                    settings.JOB_EXPIRY_TIME,
                )
                pipe.execute()
            break

        except redis.WatchError:
            continue
        finally:
            pipe.reset()

    if not job:
        logger.error("[Manager][%s] Failed adding job for query.", query_hash)

    return job


def signal_handler(*args):
    raise InterruptException


class QueryExecutionError(Exception):
    pass


def _resolve_user(user_id, is_api_key, query_id):
    if user_id is not None:
        if is_api_key:
            api_key = user_id
            if query_id is not None:
                q = models.Query.get_by_id(query_id)
            else:
                q = models.Query.by_api_key(api_key)

            return models.ApiUser(api_key, q.org, q.groups, name="ApiKey: Query {}".format(q.id))
        else:
            return models.User.get_by_id(user_id)
    else:
        return None


def _get_size_iterative(dict_obj):
    """Iteratively finds size of objects in bytes"""
    seen = set()
    size = 0
    objects = deque([dict_obj])

    while objects:
        current = objects.popleft()
        if id(current) in seen:
            continue
        seen.add(id(current))
        size += sys.getsizeof(current)

        if isinstance(current, dict):
            objects.extend(current.keys())
            objects.extend(current.values())
        elif hasattr(current, "__dict__"):
            objects.append(current.__dict__)
        elif hasattr(current, "__iter__") and not isinstance(current, (str, bytes, bytearray)):
            objects.extend(current)

    return size


class QueryExecutor:
    def __init__(self, query, data_source_id, user_id, is_api_key, metadata, is_scheduled_query):
        self.job = get_current_job()
        self.query = query
        self.data_source_id = data_source_id
        self.metadata = metadata
        self.data_source = self._load_data_source()
        self.query_id = metadata.get("query_id")
        self.user = _resolve_user(user_id, is_api_key, metadata.get("query_id"))
        if self.user is not None and not self.user.is_api_user():
            # Permissions and runner limits must remain available after session.close().
            self.user.org
        self.query_model = (
            models.Query.query.get(self.query_id)
            if self.query_id and self.query_id != "adhoc"
            else None
        )  # fmt: skip
        if self.query_model is not None and self.user is not None and self.user.is_api_user():
            self.query_model.user.org

        # Close DB connection to prevent holding a connection for a long time while the query is executing.
        models.db.session.close()
        self.query_hash = gen_query_hash(self.query)
        self.is_scheduled_query = is_scheduled_query
        if self.is_scheduled_query:
            # Load existing tracker or create a new one if the job was created before code update:
            models.scheduled_queries_executions.update(self.query_model.id)

    def run(self):
        from redash.data_access_runtime import DataAccessDenied, access_scope, is_protected, is_reader, resolve_source
        report = self.metadata.get("reader_report")
        dashboard_report = self.metadata.get("dashboard_report")
        if self.user is not None and self.user.is_api_user():
            raise QueryExecutionError("Anonymous query execution is unavailable.")
        if self.data_source is None:
            raise QueryExecutionError("The data source is no longer available.")
        org_id = self.data_source.org_id
        self.user = (models.User.query.filter_by(id=self.user.id, org_id=org_id).populate_existing().first()
                     if self.user is not None else None)
        self.data_source = models.DataSource.query.filter_by(id=self.data_source_id,
            org_id=org_id).populate_existing().first()
        if self.user is None or self.data_source is None or self.user.is_disabled or self.data_source.paused:
            raise QueryExecutionError("The execution user or data source is no longer available.")
        if not report:
            from redash.permissions import has_access, not_view_only
            if not has_access(self.data_source, self.user, not_view_only):
                raise QueryExecutionError("Full access to the data source was revoked while this query was queued.")
        if self.query_id not in (None, "adhoc"):
            from redash.permissions import has_access, view_only
            self.user = (models.User.query.filter_by(id=self.user.id).populate_existing().first()
                         if self.user is not None else None)
            self.query_model = models.Query.query.filter_by(id=self.query_id,
                org_id=self.data_source.org_id, is_archived=False).populate_existing().first()
            if (self.query_model is None or self.user is None
                    or self.query_model.data_source_id != self.data_source_id
                    or not has_access(self.query_model, self.user, view_only)):
                raise QueryExecutionError("This saved query is no longer accessible.")
        if dashboard_report:
            from redash.reader_reports import authorize_editor_report
            try:
                source, _, _ = authorize_editor_report(self.user, dict(dashboard_report), expected_sql=self.query)
                if source.id != self.data_source_id:
                    raise ValueError("Dashboard source changed while refresh was queued.")
                self.data_source = source
            except ValueError as error:
                raise QueryExecutionError(str(error)) from error
        if report:
            from redash.reader_reports import authorize_report
            try:
                if self.user is None or self.user.is_api_user() or self.is_scheduled_query:
                    raise ValueError("Reader reports require an interactive user.")
                self.user = models.User.query.filter_by(id=self.user.id).populate_existing().one_or_none()
                source, _, scope = authorize_report(self.user, report, expected_sql=self.query)
                if source.id != self.data_source_id or scope != self.metadata.get("policy_scope"):
                    raise ValueError("Report access changed while refresh was queued.")
                self.data_source = source
            except ValueError as error:
                raise QueryExecutionError(str(error)) from error
        if is_reader(self.user) and not report:
            raise QueryExecutionError("Readers cannot execute queries.")
        if is_protected(self.data_source) and not report:
            if self.user is None or self.user.is_api_user() or self.is_scheduled_query:
                raise QueryExecutionError("Protected execution requires an assigned interactive user.")
            # __init__ releases the session. Reattach fresh rows before checking
            # relationships, and do not authorize with detached queued identity.
            org_id = self.data_source.org_id
            self.user = models.User.query.filter_by(id=self.user.id, org_id=org_id).populate_existing().one_or_none()
            self.data_source = models.DataSource.query.filter_by(
                id=self.data_source_id, org_id=org_id).populate_existing().one_or_none()
            if self.user is None or self.data_source is None:
                raise QueryExecutionError("The protected user or source is no longer available.")
            self.user.org
            if self.query_id not in (None, "adhoc"):
                self.query_model = models.Query.query.filter_by(
                    id=self.query_id, org_id=org_id).populate_existing().one_or_none()
                if self.query_model is None:
                    raise QueryExecutionError("The saved query is no longer available.")
            try:
                self.data_source = resolve_source(self.data_source, self.user)
                if self.metadata.get("policy_scope") != access_scope(self.data_source, self.user):
                    raise QueryExecutionError("Group data access changed while the query was queued.")
            except DataAccessDenied as error:
                raise QueryExecutionError(str(error)) from error
            if self.query_model is not None:
                from redash.permissions import has_access, view_only
                if (not has_access(self.query_model, self.user, view_only)
                        or resolve_source(self.query_model.data_source, self.user).id != self.data_source.id):
                    raise QueryExecutionError("The saved query belongs to a different access profile.")
            # Never attach protected results to shared latest-result pointers or alerts.
            if self.query_model is not None and self.query_model.alerts:
                raise QueryExecutionError("Protected sources do not support alerts.")
        if self.metadata.get("ai_read_only"):
            from redash.ai_dashboard import SUPPORTED_SOURCES, validate_readonly_sql
            if self.data_source.type not in SUPPORTED_SOURCES:
                raise QueryExecutionError("This connector does not support automatic AI report execution.")
            try:
                validate_readonly_sql(self.query)
            except ValueError as error:
                raise QueryExecutionError(str(error)) from error
        if self.user is not None and self.user.is_api_user() and self.query_model is not None:
            owner = self.query_model.user
            if is_reader(owner) or owner.is_disabled or not owner.has_permission("execute_query"):
                raise QueryExecutionError("The report owner can no longer refresh this report.")
        if self.user is not None and not self.user.is_api_user():
            if self.user.is_disabled or (not report and not self.user.has_permission("execute_query")):
                raise QueryExecutionError("This user can no longer execute queries.")
            if self.is_scheduled_query and not self.user.has_permission("schedule_query"):
                raise QueryExecutionError("Scheduling is disabled for this user.")
        models.db.session.close()
        signal.signal(signal.SIGINT, signal_handler)
        started_at = time.time()

        logger.debug("Executing query:\n%s", self.query)
        self._log_progress("executing_query")

        if report:
            from redash.query_runner import get_query_runner
            query_runner = get_query_runner(self.data_source.type, self.data_source.options)
        else:
            query_runner = self.data_source.query_runner
        # The restricted adapter validates the original single statement and
        # intentionally rejects comments, including generated SQL annotations.
        annotated_query = self.query if (report or is_protected(self.data_source) or self.data_source.type == "mysql") else self._annotate_query(query_runner)

        try:
            if report and self.data_source.type == "mysql":
                from redash.data_access_shared import run_shared_query
                data, error = run_shared_query(self.data_source, {"unrestricted": True}, self.query)
            else:
                data, error = query_runner.run_query(annotated_query, self.user)
        except Exception as e:
            if isinstance(e, JobTimeoutException):
                error = TIMEOUT_MESSAGE
            else:
                error = str(e)

            data = None
            logger.warning("Unexpected error while running query:", exc_info=1)

        run_time = time.time() - started_at

        logger.info(
            "job=execute_query query_hash=%s ds_id=%d data_length=%s error=[%s]",
            self.query_hash,
            self.data_source_id,
            data and _get_size_iterative(data),
            error,
        )

        _unlock(self.query_hash, self.data_source.id, self.metadata.get("access_scope"))

        if error is not None and data is None:
            result = QueryExecutionError(error)
            if self.is_scheduled_query:
                self.query_model = models.db.session.get(models.Query, self.query_model.id)
                track_failure(self.query_model, error)
            raise result
        else:
            if self.query_model and self.query_model.schedule_failures > 0:
                self.query_model = models.db.session.get(models.Query, self.query_model.id)
                self.query_model.schedule_failures = 0
                self.query_model.skip_updated_at = True
                models.db.session.add(self.query_model)

            query_result = models.QueryResult.store_result(
                self.data_source.org_id,
                self.data_source,
                self.query_hash,
                self.query,
                data,
                run_time,
                utcnow(),
                policy_scope=self.metadata.get("policy_scope"),
                report_context=({**report, "kind": "reader"} if report else
                                {**dashboard_report, "kind": "editor"} if dashboard_report else
                                {"kind": "saved", "query_id": int(self.query_id)}
                                if self.query_id not in (None, "adhoc") else
                                {"kind": "adhoc", "user_id": self.user.id} if self.user is not None else None),
            )

            updated_query_ids = [] if (report or is_protected(self.data_source)) else models.Query.update_latest_result(query_result)

            models.db.session.commit()  # make sure that alert sees the latest query result
            self._log_progress("checking_alerts")
            for query_id in updated_query_ids:
                check_alerts_for_query.delay(query_id, self.metadata)
            self._log_progress("finished")

            result = query_result.id
            models.db.session.commit()
            return result

    def _annotate_query(self, query_runner):
        # Fill in missing identity for older jobs; keep metadata supplied by newer producers.
        # ApiUser.id contains the secret key; get_actual_user() returns its safe label.
        if "user_id" not in self.metadata and self.user is not None:
            self.metadata["user_id"] = self.user.get_actual_user() if self.user.is_api_user() else self.user.id
        self.metadata["Job ID"] = self.job.id
        self.metadata["Query Hash"] = self.query_hash
        self.metadata["Scheduled"] = self.is_scheduled_query

        return query_runner.annotate_query(self.query, self.metadata)

    def _log_progress(self, state):
        logger.info(
            "job=execute_query state=%s query_hash=%s type=%s ds_id=%d "
            "job_id=%s queue=%s query_id=%s user_id=%s",  # fmt: skip
            state,
            self.query_hash,
            self.data_source.type,
            self.data_source.id,
            self.job.id,
            self.metadata.get("Queue", "unknown"),
            self.metadata.get("query_id", "unknown"),
            self.metadata.get("user_id", "unknown"),
        )

    def _load_data_source(self):
        logger.info("job=execute_query state=load_ds ds_id=%d", self.data_source_id)
        return models.DataSource.query.get(self.data_source_id)


# user_id is added last as a keyword argument for backward compatability -- to support executing previously submitted
# jobs before the upgrade to this version.
def execute_query(
    query,
    data_source_id,
    metadata,
    user_id=None,
    scheduled_query_id=None,
    is_api_key=False,
):
    try:
        return QueryExecutor(
            query,
            data_source_id,
            user_id,
            is_api_key,
            metadata,
            scheduled_query_id is not None,
        ).run()
    except QueryExecutionError as e:
        models.db.session.rollback()
        return e
