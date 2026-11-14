# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Authorize a saved report definition independently from its author's cache scope."""
import copy
import re


def can_read_saved_query(query, user):
    from redash.data_access_runtime import DataAccessDenied, resolved_policy
    from redash.data_access_sql import compile_policy_sql
    from redash.models.parameterized_query import InvalidParameterError
    from sqlglot.errors import SqlglotError
    try:
        source = query.data_source
        policy = resolved_policy(source, user)
        if policy is None or policy["unrestricted"]:
            return True
        if source.type != "mysql":
            return False
        same_scope = (query.options or {}).get("data_access_scope") == policy["fingerprint"]
        # Preserve legacy same-scope definitions, including unfinished queries
        # and text parameters. The runner still checks the actual rendered SQL.
        if same_scope:
            return True
        # Only ordinary declared substitutions can be analyzed across scopes;
        # conditional/unbalanced Mustache syntax can hide SQL dependencies.
        template = re.sub(r"{{\s*[A-Za-z_][A-Za-z0-9_]*(?:\.(?:start|end))?\s*}}", "", query.query_text)
        if "{{" in template or "}}" in template:
            return False
        parameterized = query.parameterized
        if not parameterized.is_safe:
            return False
        # Discovery validates value placeholders, never dynamic identifiers.
        # A nonnumeric enum uses a sentinel so an unquoted column/table selector
        # cannot pass authorization using only its first (permitted) choice.
        parameterized.schema = copy.deepcopy(parameterized.schema)
        values = {}
        for definition in parameterized.schema:
            name, kind = definition["name"], definition["type"]
            if kind == "number":
                value = 0
            elif kind in ("date", "datetime-local", "datetime-with-seconds"):
                value = "2000-01-01" if kind == "date" else "2000-01-01 00:00:00"
            elif kind in ("date-range", "datetime-range", "datetime-range-with-seconds"):
                value = {"start": "2000-01-01", "end": "2000-12-31"}
            elif kind == "enum":
                options = definition.get("enumOptions", [])
                options = options.splitlines() if isinstance(options, str) else options
                if not options:
                    return False
                value = "0" if all(re.fullmatch(r"-?\d+(?:\.\d+)?", str(option)) for option in options) else "__bidash_report_parameter__"
                definition["enumOptions"] = [value]
                if isinstance(definition.get("multiValuesOptions"), dict):
                    value = [value]
            else:
                return False
            values[name] = value
        parameterized.apply(values)
        if parameterized.missing_params:
            return False
        sql = parameterized.text
        if not same_scope:
            import sqlglot
            from sqlglot import exp
            tree = sqlglot.parse_one(sql, read="mysql")
            # A shared saved report must have stable output columns. Existing
            # SELECT * reports need an explicit projection before cross-scope
            # sharing; COUNT(*) does not select forbidden column values.
            if any(not isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star)):
                return False
        compile_policy_sql(sql, policy, connector="mysql", default_schema=source.options.get("db"))
        return True
    except (DataAccessDenied, InvalidParameterError, SqlglotError, ValueError, TypeError, KeyError, AttributeError):
        return False
