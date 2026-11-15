# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Administrator-managed shared-connection group access rules."""

from flask import request
from flask_restful import abort
from sqlalchemy.orm.exc import NoResultFound

from redash.data_access import (
    CONNECTORS, SETTING_KEY, GROUP_SETTING_KEY, connector_capabilities, default_configuration, validate_configuration, validate_group_configuration,
)
from redash.handlers.access_policy import access_body, lock_administrator
from redash.handlers.base import BaseResource
from redash.models import DataSource, Group, User, db
from redash.permissions import require_admin


def _stored(org):
    try:
        return validate_configuration(org.settings.get(SETTING_KEY, default_configuration()))
    except ValueError:
        abort(409, message="Stored draft configuration is invalid; administrative repair is required.")


def _response(config):
    return {**config, "status": "draft_only", "enforced": False,
            "capabilities": {name: connector_capabilities(name) for name in CONNECTORS}}


def _validate_references(config, org):
    # Resolve through organization-scoped lookups. Never persist arbitrary foreign IDs.
    checked = set()
    for policy in config["policies"]:
        references = [(DataSource, policy["data_source_id"])]
        references.extend((User, value) for value in policy["user_ids"])
        references.extend((Group, value) for value in policy["group_ids"])
        for model, object_id in references:
            key = (model, object_id)
            if key in checked:
                continue
            try:
                obj = model.get_by_id_and_org(object_id, org)
            except NoResultFound:
                obj = None
            if obj is None:
                abort(400, message="Draft references an unavailable source, user or group.")
            if model is DataSource and not connector_capabilities(obj.type)["draft_supported"]:
                abort(400, message="This connector does not support data access drafts.")
            if model is User and obj.is_disabled:
                abort(400, message="Disabled users cannot be assigned a draft policy.")
            checked.add(key)


class DataAccessResource(BaseResource):
    @require_admin
    def get(self):
        stored = self.current_org.settings.get(GROUP_SETTING_KEY)
        try:
            config = validate_group_configuration(stored) if stored is not None else _stored(self.current_org)
        except ValueError:
            abort(409, message="Stored group rules are invalid; administrative repair is required.")
        return {**config, "mode": "group_rules", "enforced": stored is not None,
                "protected_source_ids": sorted({p["data_source_id"] for p in config["policies"]})
                    if stored is not None else self.current_org.settings.get("data_access_protected_sources", []),
                "applies_to_roles": ["editor"], "select_only": True,
                "capabilities": {name: connector_capabilities(name) for name in CONNECTORS},
                "supported_connectors": ["mysql"]}

    @require_admin
    def post(self):
        from redash.data_access_metadata import fetch_metadata
        from redash.data_access_runtime import is_internal, DataAccessDenied
        from redash.data_access_shared import validate_shared_source
        try:
            config = validate_group_configuration(access_body())
        except ValueError as error:
            abort(400, message=str(error))
        lock_administrator(self.current_org, self.current_user)
        db.session.refresh(self.current_org)
        stored = self.current_org.settings.get(GROUP_SETTING_KEY)
        previous = stored if stored is not None else _stored(self.current_org)
        if config["revision"] != previous["revision"]:
            abort(409, message="Group rules changed. Reload before saving.")
        if config["revision"] >= 2147483646:
            abort(409, message="Rule revision limit reached.")
        _validate_references(config, self.current_org)
        checked = {}
        audited = set()
        for policy in config["policies"]:
            source = DataSource.get_by_id_and_org(policy["data_source_id"], self.current_org)
            if source.type != "mysql" or is_internal(source):
                abort(400, message="Group rules currently support ordinary MySQL/MariaDB sources.")
            if source.id not in audited:
                try:
                    validate_shared_source(source)
                except DataAccessDenied as error:
                    abort(400, message=str(error))
                audited.add(source.id)
            for rule in policy["rules"]:
                if rule["catalog"] is not None or not rule["schema"]:
                    abort(400, message="Choose Schema/database and leave Catalog empty.")
                key = (source.id, rule["schema"], rule["table"])
                if key not in checked:
                    try:
                        checked[key] = fetch_metadata(source, rule["schema"], rule["table"])
                    except ValueError:
                        abort(400, message="Could not validate selected table metadata. Check the connection and supported base-table requirements.")
                if not set(rule["columns"]).issubset(checked[key]["columns"]):
                    abort(400, message="A selected table or column is unavailable or unsupported. Refresh metadata.")
        config["revision"] += 1
        protected = {policy["data_source_id"] for policy in config["policies"]}
        self.current_org.settings = {**self.current_org.settings, GROUP_SETTING_KEY: config,
                                    "data_access_protected_sources": sorted(protected)}
        db.session.commit()
        self.record_event({"action": "edit", "object_type": "data_access_group_rules",
                           "object_id": self.current_org.id, "revision": config["revision"],
                           "policy_count": len(config["policies"]), "enforced": True})
        return {**config, "mode": "group_rules", "enforced": True}


class DataAccessEffectiveResource(BaseResource):
    @require_admin
    def get(self):
        from redash.data_access_runtime import DataAccessDenied, is_protected, resolved_policy
        from redash.handlers.base import get_object_or_404
        source_id = request.args.get("data_source_id", type=int)
        user_id = request.args.get("user_id", type=int)
        if source_id is None or user_id is None:
            abort(400, message="Select a user and data source.")
        source = get_object_or_404(DataSource.get_by_id_and_org, source_id, self.current_org)
        user = get_object_or_404(User.get_by_id_and_org, user_id, self.current_org)
        if user.role == "reader":
            return {"protected": is_protected(source), "assigned": False,
                    "message": "Editor data rules do not apply to Readers. Existing dashboard and result permissions apply; Reader dashboard rules will be configured separately."}
        if not is_protected(source):
            return {"protected": False, "message": "Existing role and source-group permissions apply."}
        try:
            policy = resolved_policy(source, user)
            return {"protected": True, "assigned": bool(policy), "policy": policy}
        except DataAccessDenied:
            return {"protected": True, "assigned": False,
                    "message": "No group rules grant access. Check group membership, source access and user status."}


class DataAccessMetadataResource(BaseResource):
    @require_admin
    def get(self):
        from redash.data_access_metadata import MetadataUnavailable, fetch_metadata
        from redash.data_access_runtime import is_internal
        from redash.handlers.base import get_object_or_404
        allowed = {"data_source_id", "schema", "table"}
        if set(request.args) - allowed or any(len(request.args.getlist(key)) != 1 for key in request.args):
            abort(400, message="Provide one data_source_id and optional schema/table names.")
        source_id = request.args.get("data_source_id", type=int)
        if source_id is None or source_id < 1:
            abort(400, message="Choose a data source.")
        source = get_object_or_404(DataSource.get_by_id_and_org, source_id, self.current_org)
        if is_internal(source):
            abort(403, message="Private execution profiles are unavailable for metadata selection.")
        try:
            # This is deliberate admin-management access to names only. Do not
            # resolve a user's restricted profile or execute caller-supplied SQL.
            return fetch_metadata(source, request.args.get("schema"), request.args.get("table"))
        except MetadataUnavailable:
            abort(400, message="Could not load metadata names. Check the connection and supported adapter settings.")
        except ValueError as error:
            abort(400, message=str(error))
