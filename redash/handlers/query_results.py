import unicodedata
from urllib.parse import quote

import regex
from redis.exceptions import LockError
from flask import make_response, request
from flask_login import current_user
from flask_restful import abort

from redash import models, rq_redis_connection, settings
from redash.handlers.base import BaseResource, get_object_or_404, record_event
from redash.models.parameterized_query import (
    InvalidParameterError,
    ParameterizedQuery,
    QueryDetachedFromDataSourceError,
    dropdown_values,
)
from redash.permissions import (
    has_access,
    not_view_only,
    require_access,
    require_any_of_permission,
    require_permission,
    view_only,
)
from redash.query_runner.query_results import (
    extract_cached_query_ids,
    extract_query_ids,
    extract_query_params,
)
from redash.serializers import (
    serialize_job,
    serialize_query_result,
    serialize_query_result_to_dsv,
    serialize_query_result_to_html,
    serialize_query_result_to_xlsx,
)
from redash.tasks import Job
from redash.tasks.queries import enqueue_query
from redash.tasks.queries.execution import QueryExecutionError
from redash.utils import (
    collect_parameters_from_request,
    json_dumps,
    to_filename,
)


def error_response(message, http_status=400):
    return {"job": {"status": 4, "error": message}}, http_status


error_messages = {
    "unsafe_when_shared": error_response(
        "This query contains potentially unsafe parameters and cannot be executed on a shared dashboard or an embedded visualization.",
        403,
    ),
    "unsafe_on_view_only": error_response(
        "This query contains potentially unsafe parameters and cannot be executed with read-only access to this data source.",
        403,
    ),
    "no_permission": error_response("You do not have permission to run queries with this data source.", 403),
    "select_data_source": error_response("Please select data source to run this query.", 401),
    "no_data_source": error_response("Target data source not available.", 401),
}


def _require_result_access(result, user):
    from redash.data_access_runtime import result_access_allowed
    from redash.reader_reports import result_dashboard_allowed
    if not result_access_allowed(result, user) or not result_dashboard_allowed(result, user):
        abort(403, message="This result is outside your current group access scope.")


def _require_job_access(job, user):
    if job.meta.get("dashboard_report"):
        from redash.reader_reports import authorize_editor_report
        try:
            authorize_editor_report(user, dict(job.meta["dashboard_report"]))
        except ValueError as error:
            abort(403, message=str(error))
    query_id = job.meta.get("query_id")
    if query_id not in (None, "adhoc") and not job.meta.get("reader_report"):
        query = models.Query.query.filter_by(id=query_id, org_id=user.org_id).first()
        if query is None or not has_access(query, user, view_only):
            abort(403, message="This saved query is no longer accessible.")
    if job.meta.get("reader_report"):
        from redash.reader_reports import authorize_report
        from redash.data_access_runtime import DataAccessDenied
        try:
            _, _, scope = authorize_report(user, job.meta["reader_report"])
            if scope != job.meta.get("policy_scope"):
                abort(403, message="Report access changed while refresh was queued.")
        except DataAccessDenied as error:
            abort(403, message=str(error))
        return
    source_id = job.meta.get("data_source_id")
    if source_id:
        source = models.DataSource.query.get(source_id)
        if source is not None:
            from redash.data_access_runtime import access_scope, can_access_source, is_protected
            if can_access_source(source, user) is False:
                abort(403, message="This job's data source is no longer accessible.")
            require_access(source, user, view_only)
            if is_protected(source):
                if job.meta.get("schema") or job.meta.get("policy_scope") != access_scope(source, user):
                    abort(403, message="Legacy schema jobs are unavailable for protected sources.")


def run_query(query, parameters, data_source, query_id, should_apply_auto_limit, max_age=0, dashboard_report=None):
    if not data_source:
        return error_messages["no_data_source"]

    from redash.data_access_runtime import can_read_all_scopes, DataAccessDenied, access_scope, is_internal, is_reader, resolve_source
    # Internal profile IDs are execution identities, never user-selectable sources.
    if is_internal(data_source):
        abort(403, message="Internal execution profiles cannot be selected directly.")
    try:
        data_source = resolve_source(data_source, current_user)
    except DataAccessDenied as error:
        abort(403, message=str(error))

    if data_source.paused:
        if data_source.pause_reason:
            message = "{} is paused ({}). Please try later.".format(data_source.name, data_source.pause_reason)
        else:
            message = "{} is paused. Please try later.".format(data_source.name)

        return error_response(message)

    try:
        query.apply(parameters)
    except (InvalidParameterError, QueryDetachedFromDataSourceError) as e:
        abort(400, message=str(e))

    query_text = data_source.query_runner.apply_auto_limit(query.text, should_apply_auto_limit)

    if query.missing_params:
        return error_response("Missing parameter value for: {}".format(", ".join(query.missing_params)))

    reader = is_reader(current_user) or (not current_user.is_api_user() and not current_user.has_permission("execute_query"))
    if reader:
        max_age = -1
    if max_age == 0:
        query_result = None
    else:
        query_result = models.QueryResult.get_latest(data_source, query_text, max_age,
            policy_scope=access_scope(data_source, current_user), any_policy_scope=can_read_all_scopes(current_user))
        if query_result is not None:
            from redash.reader_reports import result_dashboard_allowed
            if not result_dashboard_allowed(query_result, current_user):
                query_result = None
            elif dashboard_report:
                context = getattr(query_result, "report_context", None) or {}
                if (context.get("kind") != "editor"
                        or context.get("dashboard_id") != dashboard_report["dashboard_id"]
                        or context.get("authorization_scope") != dashboard_report["authorization_scope"]):
                    query_result = None

    if reader and query_result is None:
        return error_response("No saved result is available for these parameters. Ask an editor to refresh the report.", 403)

    record_event(
        current_user.org,
        current_user,
        {
            "action": "execute_query",
            "cache": "hit" if query_result else "miss",
            "object_id": data_source.id,
            "object_type": "data_source",
            "query": query_text,
            "query_id": query_id,
            "parameters": parameters,
        },
    )

    if query_result:
        return {"query_result": serialize_query_result(query_result, current_user.is_api_user())}
    else:
        # SQL annotations must use numeric IDs or server-generated API labels, never user generated content.
        try:
            job = enqueue_query(
                query_text,
                data_source,
                current_user.id,
                current_user.is_api_user(),
                metadata={
                    "user_id": current_user.get_actual_user() if current_user.is_api_user() else current_user.id,
                    "query_id": query_id,
                    "saved_parameters": parameters,
                    "saved_auto_limit": should_apply_auto_limit,
                    **({"dashboard_report": dashboard_report} if dashboard_report else {}),
                },
            )
        except (QueryExecutionError, LockError) as error:
            return error_response(str(error) if isinstance(error, QueryExecutionError)
                                  else "Another query request is being processed. Please retry.", 429)
        return serialize_job(job)


def get_download_filename(query_result, query, filetype):
    retrieved_at = query_result.retrieved_at.strftime("%Y_%m_%d")
    if query:
        query_name = regex.sub(r"\p{C}", "", query.name)
        filename = to_filename(query_name) if query_name != "" else str(query.id)
    else:
        filename = str(query_result.id)
    return "{}_{}.{}".format(filename, retrieved_at, filetype)


def content_disposition_filenames(attachment_filename):
    if not isinstance(attachment_filename, str):
        attachment_filename = attachment_filename.decode("utf-8")

    if attachment_filename.isascii():
        filenames = {"filename": attachment_filename}
    else:
        filenames = {
            "filename": unicodedata.normalize("NFKD", attachment_filename).encode("ascii", "ignore").decode("ascii"),
            "filename*": "UTF-8''%s" % quote(attachment_filename, safe=""),
        }

    return filenames


class QueryResultListResource(BaseResource):
    @require_permission("execute_query")
    def post(self):
        """
        Execute a query (or retrieve recent results).

        :qparam string query: The query text to execute
        :qparam number query_id: The query object to update with the result (optional)
        :qparam number max_age: If query results less than `max_age` seconds old are available,
                                return them, otherwise execute the query; if omitted or -1, returns
                                any cached result, or executes if not available. Set to zero to
                                always execute.
        :qparam number data_source_id: ID of data source to query
        :qparam object parameters: A set of parameter values to apply to the query.
        """
        from redash.data_access_runtime import is_reader
        if is_reader(self.current_user):
            abort(403, message="Readers cannot execute queries.")
        params = request.get_json(force=True)

        query = params["query"]
        max_age = params.get("max_age", -1)
        # max_age might have the value of None, in which case calling int(None) will fail
        if max_age is None:
            max_age = -1
        max_age = int(max_age)
        query_id = params.get("query_id", "adhoc")
        parameters = params.get("parameters", collect_parameters_from_request(request.args))

        parameterized_query = ParameterizedQuery(query, org=self.current_org)
        should_apply_auto_limit = params.get("apply_auto_limit", False)

        data_source_id = params.get("data_source_id")
        if data_source_id:
            data_source = models.DataSource.get_by_id_and_org(params.get("data_source_id"), self.current_org)
        else:
            return error_messages["select_data_source"]

        if not has_access(data_source, self.current_user, not_view_only):
            return error_messages["no_permission"]

        if query_id not in (None, "adhoc"):
            saved = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
            if saved.user_id != self.current_user.id or saved.data_source_id != data_source.id:
                abort(403, message="Use the saved report endpoint to refresh another owner's query.")
            query_id = saved.id
            if query != saved.query_text:
                # Running unsaved SQL is ad-hoc work, never saved-report provenance.
                query_id = "adhoc"

        return run_query(
            parameterized_query,
            parameters,
            data_source,
            query_id,
            should_apply_auto_limit,
            max_age,
        )


ONE_YEAR = 60 * 60 * 24 * 365.25


def _require_dropdown_result_access(query):
    from redash.data_access_runtime import can_read_all_scopes, is_protected
    if is_protected(query.data_source) and can_read_all_scopes(current_user):
        text = query.data_source.query_runner.apply_auto_limit(query.query_text,
                query.options.get("apply_auto_limit", False))
        result = models.QueryResult.get_latest(query.data_source, text, -1, any_policy_scope=True)
    else:
        result = models.QueryResult.query.filter_by(id=query.latest_query_data_id, org_id=query.org_id).first()
    if result is None:
        abort(403, message="No authorized saved dropdown result is available.")
    _require_result_access(result, current_user)


class QueryResultDropdownResource(BaseResource):
    def get(self, query_id):
        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
        require_access(query, current_user, view_only)
        from redash.data_access_runtime import can_read_all_scopes, is_protected
        if is_protected(query.data_source) and not can_read_all_scopes(current_user):
            abort(403, message="Protected sources do not support query dropdowns.")
        try:
            _require_dropdown_result_access(query)
            return dropdown_values(query_id, self.current_org)
        except QueryDetachedFromDataSourceError as e:
            abort(400, message=str(e))


class QueryDropdownsResource(BaseResource):
    def get(self, query_id, dropdown_query_id):
        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
        require_access(query, current_user, view_only)
        from redash.data_access_runtime import can_read_all_scopes, is_protected
        dropdown_query = get_object_or_404(models.Query.get_by_id_and_org, dropdown_query_id, self.current_org)
        require_access(dropdown_query, current_user, view_only)
        if is_protected(dropdown_query.data_source) and not can_read_all_scopes(current_user):
            abort(403, message="Protected sources do not support query dropdowns.")

        related_queries_ids = [p["queryId"] for p in query.parameters if p["type"] == "query"]
        if int(dropdown_query_id) not in related_queries_ids:
            # Only regular users may preview dropdowns before adding them to the query.
            if current_user.is_api_user():
                abort(403)
            dropdown_query = get_object_or_404(models.Query.get_by_id_and_org, dropdown_query_id, self.current_org)
            require_access(dropdown_query, current_user, view_only)

        _require_dropdown_result_access(dropdown_query)
        return dropdown_values(dropdown_query_id, self.current_org)


class QueryResultResource(BaseResource):
    @staticmethod
    def add_cors_headers(headers):
        if "Origin" in request.headers:
            origin = request.headers["Origin"]

            if set(["*", origin]) & settings.ACCESS_CONTROL_ALLOW_ORIGIN:
                headers["Access-Control-Allow-Origin"] = origin
                headers["Access-Control-Allow-Credentials"] = str(settings.ACCESS_CONTROL_ALLOW_CREDENTIALS).lower()

    @require_any_of_permission(("view_query", "execute_query", "view_results"))
    def options(self, query_id=None, query_result_id=None, filetype="json"):
        headers = {}
        self.add_cors_headers(headers)

        if settings.ACCESS_CONTROL_REQUEST_METHOD:
            headers["Access-Control-Request-Method"] = settings.ACCESS_CONTROL_REQUEST_METHOD

        if settings.ACCESS_CONTROL_ALLOW_HEADERS:
            headers["Access-Control-Allow-Headers"] = settings.ACCESS_CONTROL_ALLOW_HEADERS

        return make_response("", 200, headers)

    @require_any_of_permission(("view_query", "execute_query", "view_results"))
    def post(self, query_id):
        """
        Execute a saved query.

        :param number query_id: The ID of the query whose results should be fetched.
        :param object parameters: The parameter values to apply to the query.
        :qparam number max_age: If query results less than `max_age` seconds old are available,
                                return them, otherwise execute the query; if omitted or -1, returns
                                any cached result, or executes if not available. Set to zero to
                                always execute.
        """
        params = request.get_json(force=True, silent=True) or {}
        parameter_values = params.get("parameters", {})

        max_age = params.get("max_age", -1)
        # max_age might have the value of None, in which case calling int(None) will fail
        if max_age is None:
            max_age = -1
        max_age = int(max_age)

        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)

        from redash.data_access_runtime import is_reader
        if is_reader(self.current_user) and params.get("dashboard_id") is not None:
            from redash.reader_reports import authorize_report
            from redash.data_access_runtime import DataAccessDenied
            report = {"dashboard_id": params["dashboard_id"], "query_id": query.id, "parameters": parameter_values}
            try:
                source, sql, scope = authorize_report(self.current_user, report)
                if max_age != 0:
                    cached = models.QueryResult.get_latest(source, sql, max_age, policy_scope=scope)
                    if cached:
                        _require_result_access(cached, self.current_user)
                        return {"query_result": serialize_query_result(cached, False)}
                job = enqueue_query(sql, source, self.current_user.id,
                                    metadata={"query_id": query.id, "reader_report": report})
                return serialize_job(job)
            except DataAccessDenied as error:
                abort(403, message=str(error))
            except (QueryExecutionError, LockError) as error:
                return error_response(str(error), 429)

        if is_reader(self.current_user):
            abort(403, message="Refresh saved Reader reports from an assigned dashboard.")

        dashboard_report = None
        if params.get("dashboard_id") is not None:
            from redash.reader_reports import authorize_editor_report
            dashboard_report = {"kind": "editor", "dashboard_id": params["dashboard_id"],
                                "query_id": query.id, "parameters": parameter_values}
            try:
                authorize_editor_report(self.current_user, dashboard_report)
            except ValueError as error:
                abort(403, message=str(error))

        allow_executing_with_view_only_permissions = query.parameterized.is_safe
        if "apply_auto_limit" in params:
            should_apply_auto_limit = params.get("apply_auto_limit", False)
        else:
            should_apply_auto_limit = query.options.get("apply_auto_limit", False)

        if has_access(query, self.current_user, allow_executing_with_view_only_permissions):
            return run_query(
                query.parameterized,
                parameter_values,
                query.data_source,
                query.id,
                should_apply_auto_limit,
                max_age,
                dashboard_report=dashboard_report,
            )
        else:
            if not query.parameterized.is_safe:
                if current_user.is_api_user():
                    return error_messages["unsafe_when_shared"]
                else:
                    return error_messages["unsafe_on_view_only"]
            else:
                return error_messages["no_permission"]

    @require_any_of_permission(("view_query", "execute_query", "view_results"))
    def get(self, query_id=None, query_result_id=None, filetype="json"):
        from redash.data_access_runtime import is_reader
        """
        Retrieve query results.

        :param number query_id: The ID of the query whose results should be fetched
        :param number query_result_id: the ID of the query result to fetch
        :param string filetype: Format to return. One of 'json', 'xlsx', 'csv', 'tsv', or 'html'. Defaults to 'json'.

        :<json number id: Query result ID
        :<json string query: Query that produced this result
        :<json string query_hash: Hash code for query text
        :<json object data: Query output
        :<json number data_source_id: ID of data source that produced this result
        :<json number runtime: Length of execution time in seconds
        :<json string retrieved_at: Query retrieval date/time, in ISO format
        """
        # TODO:
        # This method handles two cases: retrieving result by id & retrieving result by query id.
        # They need to be split, as they have different logic (for example, retrieving by query id
        # should check for query parameters and shouldn't cache the result).
        if filetype != "json" and not self.current_user.has_permission("download_results"):
            abort(403, message="Downloads are disabled for your role.")
        should_cache = query_result_id is not None

        query_result = None
        query = None

        if query_result_id:
            query_result = get_object_or_404(models.QueryResult.get_by_id_and_org, query_result_id, self.current_org)

        if query_id is not None:
            query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
            require_access(query, self.current_user, view_only)

            from redash.data_access_runtime import can_read_all_scopes, DataAccessDenied, access_scope, is_protected, is_reader, resolve_source
            if query_result is None and query is not None and is_protected(query.data_source):
                require_access(query, self.current_user, view_only)
                try:
                    source = resolve_source(query.data_source, self.current_user)
                except DataAccessDenied as error:
                    abort(403, message=str(error))
                text = source.query_runner.apply_auto_limit(query.query_text, query.options.get("apply_auto_limit", False))
                query_result = models.QueryResult.get_latest(source, text, -1,
                    policy_scope=access_scope(source, self.current_user), any_policy_scope=can_read_all_scopes(self.current_user))
            elif query_result is None and query is not None and query.latest_query_data_id is not None:
                query_result = get_object_or_404(
                    models.QueryResult.get_by_id_and_org,
                    query.latest_query_data_id,
                    self.current_org,
                )

            if query is not None and query_result is not None and self.current_user.is_api_user():
                if query.query_hash != query_result.query_hash:
                    abort(404, message="No cached result found for this query.")

        if query_result:
            _require_result_access(query_result, self.current_user)
            require_access(query_result.data_source, self.current_user, view_only)

            # If the result was produced via the Query Results data source,
            # verify access to each underlying data source referenced in the query.
            if query_result.data_source and query_result.data_source.type == "results":
                query_ids = set(
                    extract_query_ids(query_result.query_text)
                    + extract_cached_query_ids(query_result.query_text)
                    + [int(p[0]) for p in extract_query_params(query_result.query_text)]
                )
                for qid in query_ids:
                    try:
                        source_query = models.Query.get_by_id(qid)
                        if source_query.data_source:
                            from redash.data_access_runtime import is_protected
                            if is_protected(source_query.data_source) and not is_reader(self.current_user):
                                abort(403, message="Protected Query Results dependencies are unavailable.")
                            require_access(source_query, self.current_user, view_only)
                    except models.NoResultFound:
                        abort(403, message="Access denied - referenced query no longer exists.")

            if isinstance(self.current_user, models.ApiUser):
                event = {
                    "user_id": None,
                    "org_id": self.current_org.id,
                    "action": "api_get",
                    "api_key": self.current_user.name,
                    "file_type": filetype,
                    "user_agent": request.user_agent.string,
                    "ip": request.remote_addr,
                }

                if query_id:
                    event["object_type"] = "query"
                    event["object_id"] = query_id
                else:
                    event["object_type"] = "query_result"
                    event["object_id"] = query_result_id

                self.record_event(event)

            response_builders = {
                "json": self.make_json_response,
                "xlsx": self.make_excel_response,
                "csv": self.make_csv_response,
                "tsv": self.make_tsv_response,
                "html": lambda result: self.make_html_response(result, query.name if query else "Bi-Dash report"),
            }
            response = response_builders[filetype](query_result)

            if len(settings.ACCESS_CONTROL_ALLOW_ORIGIN) > 0:
                self.add_cors_headers(response.headers)

            # Authorization and SQL visibility may change between requests or logins.
            response.headers["Cache-Control"] = "private, no-store"

            filename = get_download_filename(query_result, query, filetype)

            filenames = content_disposition_filenames(filename)
            response.headers.add("Content-Disposition", "attachment", **filenames)

            return response

        else:
            abort(404, message="No cached result found for this query.")

    @staticmethod
    def make_json_response(query_result):
        data = json_dumps({"query_result": serialize_query_result(query_result, current_user.is_api_user())})
        headers = {"Content-Type": "application/json"}
        return make_response(data, 200, headers)

    @staticmethod
    def make_csv_response(query_result):
        headers = {"Content-Type": "text/csv; charset=UTF-8"}
        return make_response(serialize_query_result_to_dsv(query_result, ","), 200, headers)

    @staticmethod
    def make_tsv_response(query_result):
        headers = {"Content-Type": "text/tab-separated-values; charset=UTF-8"}
        return make_response(serialize_query_result_to_dsv(query_result, "\t"), 200, headers)

    @staticmethod
    def make_html_response(query_result, title="Bi-Dash report"):
        headers = {"Content-Type": "text/html; charset=UTF-8", "X-Content-Type-Options": "nosniff"}
        return make_response(serialize_query_result_to_html(query_result, title), 200, headers)

    @staticmethod
    def make_excel_response(query_result):
        headers = {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        return make_response(serialize_query_result_to_xlsx(query_result), 200, headers)


class JobResource(BaseResource):
    def get(self, job_id, query_id=None):
        """
        Retrieve info about a running query job.
        """
        job = Job.fetch(job_id, connection=rq_redis_connection)
        if job.meta.get("org_id") != self.current_org.id:
            abort(404)
        if not self.current_user.has_permission("admin") and job.meta.get("user_id") != self.current_user.id:
            abort(403)
        _require_job_access(job, self.current_user)
        return serialize_job(job)

    def delete(self, job_id):
        """
        Cancel a query job in progress.
        """
        job = Job.fetch(job_id, connection=rq_redis_connection)
        if job.meta.get("org_id") != self.current_org.id:
            abort(404)
        if not self.current_user.has_permission("admin") and job.meta.get("user_id") != self.current_user.id:
            abort(403)
        _require_job_access(job, self.current_user)
        job.cancel()
