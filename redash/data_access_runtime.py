# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Group allowlists and immutable result scopes on shared connections."""
import hashlib
import hmac
import json

class DataAccessDenied(ValueError):
    pass

def is_reader(user):
    # Flask's current_user can be a proxy resolving to None outside a request.
    return getattr(user, "role", None) == "reader" and not user.is_api_user()


def can_read_all_scopes(user):
    """Report-read privilege only; execution and Editor caches remain scoped."""
    return (getattr(user, "is_api_user", None) is not None and not user.is_api_user()
            and (is_reader(user) or user.has_permission("admin")))

def _settings(source):
    if source.org_id is None:
        return {}
    from redash import models
    return models.Organization.query.filter_by(id=source.org_id).populate_existing().one().settings

def _state(source):
    from redash.data_access import validate_group_configuration
    settings = _settings(source)
    internal = settings.get("data_access_internal_sources", [])
    protected = settings.get("data_access_protected_sources", [])
    legacy = settings.get("active_data_access", [])
    if (not isinstance(internal, list) or not isinstance(protected, list) or not isinstance(legacy, list)
            or any(type(value) is not int or value < 1 for value in internal + protected)):
        raise DataAccessDenied("Data access configuration is invalid.")
    for entry in legacy:
        if (not isinstance(entry, dict) or type(entry.get("profile_source_id")) is not int
                or not isinstance(entry.get("policy"), dict) or type(entry["policy"].get("data_source_id")) is not int):
            raise DataAccessDenied("Legacy data access configuration is invalid.")
        internal = internal + [entry["profile_source_id"]]
        protected = protected + [entry["policy"]["data_source_id"]]
    try:
        config = validate_group_configuration(settings.get("data_access_group_rules", {"revision": 0, "policies": []}))
    except ValueError as error:
        raise DataAccessDenied("Group data access configuration is invalid.") from error
    # Modern group rules are the source of truth. Historical protection markers
    # must not keep an ordinary source restricted after its last rule is removed.
    active = {p["data_source_id"] for p in config["policies"]}
    return config, set(internal), active if "data_access_group_rules" in settings else set(protected) | active

def is_internal(source):
    try:
        return source.id in _state(source)[1]
    except DataAccessDenied:
        return True

def is_protected(source):
    try:
        _, internal, protected = _state(source)
        return (source.id in internal | protected or bool(internal | protected)
                and getattr(source, "type", None) in ("results", "python"))
    except DataAccessDenied:
        return True

def _binding(source, user, execute=False):
    if execute and is_reader(user):
        raise DataAccessDenied("Readers cannot execute queries.")
    config, internal, protected = _state(source)
    if source.id in internal:
        raise DataAccessDenied("This private execution profile is retired.")
    # Reader report authorization remains the pre-existing source-group model.
    # Group column rules are an Editor policy, never implicit dashboard grants.
    if is_reader(user):
        if user.is_disabled or user.org_id != source.org_id:
            raise DataAccessDenied("An active organization user is required.")
        return None
    if internal | protected and getattr(source, "type", None) in ("results", "python"):
        raise DataAccessDenied("Query Results and Python sources are unavailable while protection is configured.")
    if source.id not in protected:
        return None
    if user is None or user.is_api_user() or user.org_id != source.org_id or user.is_disabled:
        raise DataAccessDenied("An active organization user is required.")
    from redash.permissions import has_access_to_groups
    if not has_access_to_groups(source, user, not execute):
        raise DataAccessDenied("Access to this data source is unavailable.")
    if execute and (getattr(source, "paused", False) or not user.has_permission("execute_query")):
        raise DataAccessDenied("Execution is unavailable for this user or paused source.")
    unrestricted = user.has_permission("admin")
    tables = {}
    if not unrestricted:
        policies = [p for p in config["policies"] if p["data_source_id"] == source.id
                    and set(p["group_ids"]).intersection(user.group_ids)]
        if not policies:
            raise DataAccessDenied("No group data access rules are assigned.")
        for policy in policies:
            for rule in policy["rules"]:
                key = (rule["catalog"] or "", rule["schema"], rule["table"])
                tables.setdefault(key, set()).update(rule["columns"])
    rules = [{"catalog": catalog or None, "schema": schema, "table": table,
              "column_mode": "explicit", "columns": sorted(columns)}
             for (catalog, schema, table), columns in sorted(tables.items())]
    from redash import settings
    options = source.options.to_dict() if hasattr(source.options, "to_dict") else dict(source.options)
    # Scope tokens are visible to clients; a keyed digest avoids making a
    # password-derived public fingerprint an offline password-guessing oracle.
    key = settings.DATASOURCE_SECRET_KEY
    key = key.encode("utf-8") if isinstance(key, str) else key
    connection_scope = hmac.new(key, json.dumps(options, sort_keys=True, default=str,
                                separators=(",", ":")).encode("utf-8"), hashlib.sha256).hexdigest()
    value = {"org": source.org_id, "source": source.id, "connection": connection_scope,
             "unrestricted": unrestricted, "rules": rules}
    scope = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"id": "effective", "name": "Administrator access" if unrestricted else "Effective group access",
            "data_source_id": source.id, "rules": rules, "unrestricted": unrestricted, "fingerprint": scope, "policy_scope": scope}

def can_access_source(source, user):
    try:
        return True if _binding(source, user) else None
    except DataAccessDenied:
        return False

def access_scope(source, user):
    binding = _binding(source, user)
    return binding["fingerprint"] if binding else None

def resolve_source(source, user):
    _binding(source, user)
    return source

def resolved_policy(source, user):
    return _binding(source, user)

def result_access_allowed(result, user):
    if result.data_source is None:
        return False
    try:
        if can_read_all_scopes(user):
            from redash.permissions import has_access_to_groups
            return (not user.is_disabled and user.org_id == result.data_source.org_id
                    and not is_internal(result.data_source)
                    and has_access_to_groups(result.data_source, user, True))
        return getattr(result, "policy_scope", None) == access_scope(result.data_source, user)
    except DataAccessDenied:
        return False

def ensure_execution(source, user):
    binding = _binding(source, user, execute=True)
    if binding is None:
        from redash.query_runner import get_query_runner
        return get_query_runner(source.type, source.options)
    return ProtectedRunner(source, binding)

def resolved_execution(source, user):
    return _binding(source, user, execute=True)

def schema_for(source, user):
    policy = _binding(source, user)
    if policy is None:
        raise DataAccessDenied("Protected metadata requires assigned group rules.")
    if policy["unrestricted"]:
        from redash.query_runner import get_query_runner
        return get_query_runner(source.type, source.options).get_schema()
    return [{"name": "{}.{}".format(rule["schema"], rule["table"]), "columns": list(rule["columns"])} for rule in policy["rules"]]

class ProtectedRunner:
    def __init__(self, source, binding=None):
        self.source = source
        self.binding = binding
    def __getattr__(self, name):
        if name in ("syntax", "supports_auto_limit", "apply_auto_limit", "annotate_query", "fetch_columns", "gen_query_hash"):
            from redash.query_runner import get_query_runner
            return getattr(get_query_runner(self.source.type, self.source.options), name)
        raise DataAccessDenied("This operation is unavailable on a protected source.")
    def run_query(self, query, user):
        binding = _binding(self.source, user, execute=True)
        if binding is None or self.binding and self.binding["fingerprint"] != binding["fingerprint"]:
            raise DataAccessDenied("Group access changed before execution.")
        from rq import get_current_job
        job = get_current_job()
        if job is not None and getattr(job, "meta", {}).get("policy_scope") != binding["fingerprint"]:
            raise DataAccessDenied("Group access changed after this job was authorized.")
        from redash.data_access_shared import run_shared_query
        return run_shared_query(self.source, binding, query)
