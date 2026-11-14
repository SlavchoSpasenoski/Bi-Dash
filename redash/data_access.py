# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Draft-only data access configuration. Nothing here authorizes query access."""
import re

SETTING_KEY = "data_access_drafts"
GROUP_SETTING_KEY = "data_access_group_rules"
CONNECTORS = ("mysql", "rds_mysql", "pg", "mssql", "oracle")


def connector_capabilities(type_name):
    return {"draft_supported": type_name in CONNECTORS, "enforcement_available": type_name == "mysql",
            "object_levels": ["catalog", "schema", "table", "column"],
            "column_modes": ["explicit", "all_current"]}


def default_configuration():
    return {"revision": 0, "policies": []}


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("Invalid {} fields.".format(label))


def _identifier(value, label, nullable=False):
    if nullable and value is None:
        return None
    if (not isinstance(value, str) or not value or not value.strip() or len(value) > 256
            or any(ord(char) < 32 for char in value) or "*" in value):
        raise ValueError("Invalid {} identifier; use exact names, not wildcards.".format(label))
    return value


def _ids(values, label):
    if (not isinstance(values, list) or len(values) > 500
            or any(type(value) is not int or value < 1 or value > 2147483647 for value in values)):
        raise ValueError("{} must be a bounded list of positive integer IDs.".format(label))
    if len(set(values)) != len(values):
        raise ValueError("Duplicate {}.".format(label))
    return list(values)


def validate_configuration(body):
    """Return a detached validated configuration, never an effective access policy.

    Both column modes require a concrete snapshot. `all_current` records how the
    columns were selected, and never grants subsequently added columns.
    Database ownership/existence checks are the administrator API's responsibility.
    """
    _fields(body, ("revision", "policies"), "configuration")
    revision = body["revision"]
    if type(revision) is not int or not 0 <= revision <= 2147483646:
        raise ValueError("Invalid revision.")
    policies = body["policies"]
    if not isinstance(policies, list) or len(policies) > 50:
        raise ValueError("Provide at most 50 draft policies.")
    result, seen_ids, assignments = [], set(), set()
    for policy in policies:
        _fields(policy, ("id", "name", "data_source_id", "status", "default_effect", "rules",
                         "user_ids", "group_ids"), "policy")
        policy_id = policy["id"]
        if not isinstance(policy_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", policy_id):
            raise ValueError("Invalid policy ID.")
        if policy_id in seen_ids:
            raise ValueError("Duplicate policy ID.")
        seen_ids.add(policy_id)
        name = _identifier(policy["name"], "policy name")
        source_id = _ids([policy["data_source_id"]], "data source")[0]
        if policy["status"] != "draft":
            raise ValueError("Activation is unavailable: only draft policies can be saved.")
        if policy["default_effect"] != "deny":
            raise ValueError("Draft policies must use default_effect=deny.")
        rules = policy["rules"]
        if not isinstance(rules, list) or len(rules) > 100:
            raise ValueError("Provide at most 100 table rules per policy.")
        normalized_rules, paths = [], set()
        for rule in rules:
            _fields(rule, ("catalog", "schema", "table", "column_mode", "columns"), "table rule")
            catalog = _identifier(rule["catalog"], "catalog", nullable=True)
            schema = _identifier(rule["schema"], "schema", nullable=True)
            table = _identifier(rule["table"], "table")
            if catalog is None and schema is None:
                raise ValueError("A table rule needs an exact catalog or schema.")
            path = (catalog, schema, table)
            if path in paths:
                raise ValueError("Duplicate table rule.")
            paths.add(path)
            mode = rule["column_mode"]
            if mode not in ("explicit", "all_current"):
                raise ValueError("Invalid column mode.")
            columns = rule["columns"]
            if not isinstance(columns, list) or not 1 <= len(columns) <= 1000:
                raise ValueError("Every table needs 1 to 1000 explicit column names, including all_current snapshots.")
            columns = [_identifier(column, "column") for column in columns]
            if len(set(columns)) != len(columns):
                raise ValueError("Duplicate column identifier.")
            normalized_rules.append({"catalog": catalog, "schema": schema, "table": table,
                                     "column_mode": mode, "columns": columns})
        users = _ids(policy["user_ids"], "user IDs")
        groups = _ids(policy["group_ids"], "group IDs")
        for kind, ids in (("user", users), ("group", groups)):
            for principal_id in ids:
                key = (source_id, kind, principal_id)
                if key in assignments:
                    raise ValueError("A principal can have only one draft policy per data source.")
                assignments.add(key)
        result.append({"id": policy_id, "name": name, "data_source_id": source_id,
                       "status": "draft", "default_effect": "deny", "rules": normalized_rules,
                       "user_ids": users, "group_ids": groups})
    return {"revision": revision, "policies": result}


def validate_group_configuration(body):
    """Validate immediately enforced group allowlists on a shared connection."""
    import copy
    candidate = copy.deepcopy(body)
    if not isinstance(candidate, dict) or not isinstance(candidate.get("policies"), list):
        raise ValueError("Provide a rules configuration.")
    for policy in candidate["policies"]:
        if not isinstance(policy, dict) or policy.get("status") != "active":
            raise ValueError("Group rules must have status=active.")
        policy["status"] = "draft"
    config = validate_configuration(candidate)
    for policy in config["policies"]:
        if policy["user_ids"] or not policy["group_ids"] or not policy["rules"]:
            raise ValueError("Each rule set needs groups and table rules. Manage users through group membership.")
        policy["status"] = "active"
    return config
