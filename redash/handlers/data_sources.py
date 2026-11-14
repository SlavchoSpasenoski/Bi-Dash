import copy
import logging
import time
from types import SimpleNamespace

from flask import make_response, request
from flask_restful import abort
from funcy import project
from sqlalchemy.exc import IntegrityError

from redash import models
from redash.data_access_runtime import is_internal, is_protected, resolve_source
from redash.handlers.base import (
    BaseResource,
    get_object_or_404,
    require_fields,
)
from redash.handlers.databricks import clear_databricks_schema_cache
from redash.permissions import (
    require_access,
    require_admin,
    require_permission,
    view_only,
)
from redash.query_runner import (
    get_configuration_schema_for_query_runner_type,
    query_runners,
)
from redash.serializers import serialize_job
from redash.tasks.general import get_schema, test_connection
from redash.utils import filter_none
from redash.utils.configuration import ConfigurationContainer, ValidationError


class DataSourceTypeListResource(BaseResource):
    @require_admin
    def get(self):
        return [q.to_dict() for q in sorted(query_runners.values(), key=lambda q: q.name().lower())]


class DataSourceResource(BaseResource):
    def get(self, data_source_id):
        data_source = get_object_or_404(models.DataSource.get_by_id_and_org, data_source_id, self.current_org)
        if is_internal(data_source):
            abort(404)
        require_access(data_source, self.current_user, view_only)

        ds = {}
        if self.current_user.has_permission("list_data_sources"):
            # if it's a non-admin, limit the information
            ds = data_source.to_dict(all=self.current_user.has_permission("admin"))

        # add view_only info, required for frontend permissions
        ds["view_only"] = all(project(data_source.groups, self.current_user.group_ids).values())
        self.record_event({"action": "view", "object_id": data_source_id, "object_type": "datasource"})
        return ds

    @require_admin
    def post(self, data_source_id):
        data_source = get_object_or_404(models.DataSource.get_by_id_and_org, data_source_id, self.current_org)
        if is_internal(data_source):
            abort(400, message="Retired private connections cannot be updated.")
        protected = is_protected(data_source)
        req = request.get_json(True)
        if protected and (data_source.type != "mysql" or req.get("type") != "mysql"):
            abort(400, message="Protected data sources must keep the mysql connector.")

        schema = get_configuration_schema_for_query_runner_type(req["type"])
        if schema is None:
            abort(400)
        try:
            options = ConfigurationContainer(copy.deepcopy(data_source.options.to_dict()), schema)
            options.update(filter_none(req["options"]))
        except ValidationError:
            abort(400)

        if protected:
            from redash.data_access_shared import validate_shared_source
            candidate = SimpleNamespace(type="mysql", options=options,
                                        uses_ssh_tunnel="ssh_tunnel" in options)
            try:
                validate_shared_source(candidate)
            except ValueError:
                abort(400, message="The updated connection failed SELECT-only validation. Existing settings were kept.")

        data_source.options = options
        data_source.type = req["type"]
        data_source.name = req["name"]
        models.db.session.add(data_source)

        try:
            models.db.session.commit()
        except IntegrityError as e:
            if req["name"] in str(e):
                abort(
                    400,
                    message="Data source with the name {} already exists.".format(req["name"]),
                )

            abort(400)

        if data_source.type == "databricks":
            # The cached database and table lists belong to the previous configuration (e.g. catalog).
            clear_databricks_schema_cache(data_source.id)
        if protected:
            # Connection identity is part of execution/result scopes. Retain the
            # source and its rules, but discard metadata from the old connection.
            models.redis_connection.delete(data_source._schema_key)

        self.record_event({"action": "edit", "object_id": data_source.id, "object_type": "datasource"})

        return data_source.to_dict(all=True)

    @require_admin
    def delete(self, data_source_id):
        data_source = models.DataSource.get_by_id_and_org(data_source_id, self.current_org)
        if is_internal(data_source) or is_protected(data_source):
            abort(400, message="Protected connections cannot be deleted through this endpoint.")
        data_source.delete()

        self.record_event(
            {
                "action": "delete",
                "object_id": data_source_id,
                "object_type": "datasource",
            }
        )

        return make_response("", 204)


class DataSourceListResource(BaseResource):
    @require_permission("list_data_sources")
    def get(self):
        if self.current_user.has_permission("admin"):
            data_sources = models.DataSource.all(self.current_org)
        else:
            data_sources = models.DataSource.all(self.current_org, group_ids=self.current_user.group_ids)

        response = {}
        for ds in data_sources:
            if is_internal(ds):
                continue
            if is_protected(ds) and not self.current_user.has_permission("admin"):
                from redash.data_access_runtime import can_access_source
                if can_access_source(ds, self.current_user) is False:
                    continue
            if ds.id in response:
                continue

            try:
                d = ds.to_dict()
                d["view_only"] = all(project(ds.groups, self.current_user.group_ids).values())
                response[ds.id] = d
            except AttributeError:
                logging.exception("Error with DataSource#to_dict (data source id: %d)", ds.id)

        self.record_event(
            {
                "action": "list",
                "object_id": "admin/data_sources",
                "object_type": "datasource",
            }
        )

        return sorted(list(response.values()), key=lambda d: d["name"].lower())

    @require_admin
    def post(self):
        req = request.get_json(True)
        require_fields(req, ("options", "name", "type"))

        schema = get_configuration_schema_for_query_runner_type(req["type"])
        if schema is None:
            abort(400)

        config = ConfigurationContainer(filter_none(req["options"]), schema)
        if not config.is_valid():
            abort(400)

        try:
            datasource = models.DataSource.create_with_group(
                org=self.current_org, name=req["name"], type=req["type"], options=config
            )

            models.db.session.commit()
        except IntegrityError as e:
            if req["name"] in str(e):
                abort(
                    400,
                    message="Data source with the name {} already exists.".format(req["name"]),
                )

            abort(400)

        self.record_event(
            {
                "action": "create",
                "object_id": datasource.id,
                "object_type": "datasource",
            }
        )

        return datasource.to_dict(all=True)


class DataSourceSchemaResource(BaseResource):
    def get(self, data_source_id):
        data_source = get_object_or_404(models.DataSource.get_by_id_and_org, data_source_id, self.current_org)
        require_access(data_source, self.current_user, view_only)
        if is_internal(data_source):
            abort(404)
        if is_protected(data_source):
            from redash.data_access_runtime import schema_for
            try:
                return {"schema": schema_for(data_source, self.current_user)}
            except ValueError:
                abort(403, message="No unambiguous protected data profile is available.")
        refresh = request.args.get("refresh") is not None

        if not refresh:
            cached_schema = data_source.get_cached_schema()

            if cached_schema is not None:
                return {"schema": cached_schema}

        job = get_schema.delay(data_source.id, refresh)
        job.meta.update({"org_id": self.current_org.id, "user_id": self.current_user.id,
                         "data_source_id": data_source.id, "schema": True})
        job.save_meta()

        return serialize_job(job)


class DataSourcePauseResource(BaseResource):
    @require_admin
    def post(self, data_source_id):
        data_source = get_object_or_404(models.DataSource.get_by_id_and_org, data_source_id, self.current_org)
        if is_internal(data_source):
            abort(404)
        data = request.get_json(force=True, silent=True)
        if data:
            reason = data.get("reason")
        else:
            reason = request.args.get("reason")

        data_source.pause(reason)

        self.record_event(
            {
                "action": "pause",
                "object_id": data_source.id,
                "object_type": "datasource",
            }
        )
        return data_source.to_dict()

    @require_admin
    def delete(self, data_source_id):
        data_source = get_object_or_404(models.DataSource.get_by_id_and_org, data_source_id, self.current_org)
        if is_internal(data_source):
            abort(404)
        data_source.resume()

        self.record_event(
            {
                "action": "resume",
                "object_id": data_source.id,
                "object_type": "datasource",
            }
        )
        return data_source.to_dict()


class DataSourceTestResource(BaseResource):
    @require_admin
    def post(self, data_source_id):
        data_source = get_object_or_404(models.DataSource.get_by_id_and_org, data_source_id, self.current_org)
        if is_internal(data_source):
            abort(400, message="Retired private connections cannot be tested.")
        if is_protected(data_source) and data_source.type != "mysql":
            abort(400, message="Protected connection tests require the mysql connector.")

        response = {}

        job = test_connection.delay(data_source.id)
        while not (job.is_finished or job.is_failed):
            time.sleep(1)
            job.refresh()

        if isinstance(job.result, Exception):
            response = {"message": str(job.result), "ok": False}
        else:
            response = {"message": "success", "ok": True}

        self.record_event(
            {
                "action": "test",
                "object_id": data_source_id,
                "object_type": "datasource",
                "result": response,
            }
        )
        return response
