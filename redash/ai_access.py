# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Names-only Editor AI context and authorization snapshots; never execute model SQL."""
import hashlib
import hmac
import json
import re

from redash.data_access_runtime import DataAccessDenied


def require_ai_user(org, user):
    if (org is None or user is None or user.is_api_user() or user.is_disabled or user.org_id != org.id
            or user.role not in ("admin", "editor") or not user.has_permission("use_ai")):
        raise DataAccessDenied("AI requires an active authorized organization user.")


def is_editor(user):
    return user.role == "editor"


def source_allowed(source, user):
    from redash.data_access_runtime import is_internal, resolved_execution
    from redash.permissions import has_access, not_view_only
    try:
        if (source.org_id != user.org_id or source.type != "mysql" or source.paused
                or is_internal(source) or not user.has_permissions(("use_ai", "view_source", "execute_query"))
                or not has_access(source, user, not_view_only)):
            return False
        resolved_execution(source, user)
        return True
    except (DataAccessDenied, ValueError):
        return False


def available_sources(org, user):
    from redash import models
    return [source for source in models.DataSource.query.filter_by(org_id=org.id).all()
            if source_allowed(source, user)]


def _unprotected_policy(source):
    # Cached source metadata can outlive a credential change and contain comments.
    # Read only names for the selected database and supported ordinary base tables.
    from redash.data_access_mysql import _connect, detect_server
    database = source.options.get("db")
    if not isinstance(database, str) or not database or database.lower() in {"mysql", "sys", "information_schema", "performance_schema"}:
        raise DataAccessDenied("Choose an ordinary MySQL database for Editor AI.")
    connection = None
    try:
        connection = _connect(source, 10)
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION(), @@version_comment")
            detect_server(*cursor.fetchone())
            cursor.execute("SELECT c.TABLE_NAME,c.COLUMN_NAME FROM information_schema.COLUMNS c "
                           "JOIN information_schema.TABLES t ON t.TABLE_SCHEMA=c.TABLE_SCHEMA AND t.TABLE_NAME=c.TABLE_NAME "
                           "WHERE c.TABLE_SCHEMA=%s AND t.TABLE_TYPE='BASE TABLE' AND t.ENGINE='InnoDB' "
                           "AND NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS g "
                           "WHERE g.TABLE_SCHEMA=c.TABLE_SCHEMA AND g.TABLE_NAME=c.TABLE_NAME "
                           "AND (COALESCE(g.GENERATION_EXPRESSION,'')<>'' OR LOCATE('VIRTUAL',UPPER(g.EXTRA))>0 "
                           "OR LOCATE('STORED',UPPER(g.EXTRA))>0 OR LOCATE('PERSISTENT',UPPER(g.EXTRA))>0)) "
                           "ORDER BY c.TABLE_NAME,c.ORDINAL_POSITION LIMIT 10001", (database,))
            rows = cursor.fetchall()
        if len(rows) > 10000:
            raise DataAccessDenied("Editor AI metadata is too large; configure a smaller group allowlist.")
        tables = {}
        for table, column in rows:
            tables.setdefault(table, []).append(column)
        return {"rules": [{"catalog": None, "schema": database, "table": name, "columns": columns}
                          for name, columns in tables.items()]}
    except DataAccessDenied:
        raise
    except Exception as error:
        raise DataAccessDenied("Editor AI metadata could not be loaded safely.") from error
    finally:
        if connection is not None:
            connection.close()


def source_snapshot(source, user):
    from redash import settings
    from redash.data_access_runtime import resolved_execution
    if not source_allowed(source, user):
        raise DataAccessDenied("Choose an accessible active MySQL source with assigned data access.")
    binding = resolved_execution(source, user)
    policy = binding if binding and not binding["unrestricted"] else _unprotected_policy(source)
    if not policy.get("rules"):
        raise DataAccessDenied("No supported tables are available for Editor AI.")
    schema = [{"name": rule["schema"] + "." + rule["table"], "columns": list(rule["columns"])}
              for rule in policy["rules"]]
    options = source.options.to_dict() if hasattr(source.options, "to_dict") else dict(source.options)
    value = {"source": source.id, "org": source.org_id, "connection": options,
             "type": source.type, "paused": source.paused, "user": user.id, "role": user.role,
             "groups": sorted(user.group_ids), "source_groups": sorted(source.groups.items()),
             "permissions": sorted(user.permissions), "schema": schema,
             "scope": binding["fingerprint"] if binding else None}
    key = settings.DATASOURCE_SECRET_KEY
    key = key.encode() if isinstance(key, str) else key
    fingerprint = hmac.new(key, json.dumps(value, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()
    return {"fingerprint": fingerprint, "policy": policy, "schema": schema,
            "default_schema": source.options.get("db")}


def validate_editor_sql(sql, snapshot, parameters=None):
    from redash.ai_dashboard import reporting_parameters
    from redash.data_access_sql import compile_policy_sql
    # Plans contain only validated typed date-range placeholders. Render defaults
    # solely for dependency validation; persisted SQL retains the typed template.
    sql, definitions = reporting_parameters(sql, parameters or [])
    rendered = sql
    for definition in definitions:
        for field in ("start", "end"):
            pattern = r"'{{\s*" + re.escape(definition["name"]) + r"\." + field + r"\s*}}'"
            rendered = re.sub(pattern, "'" + definition["value"][field] + "'", rendered)
    compile_policy_sql(rendered, snapshot["policy"], connector="mysql", default_schema=snapshot["default_schema"])
    return sql


def selected_tables(selected, snapshot):
    if selected is None:
        return None
    if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
        raise DataAccessDenied("Selected tables must be a list of table names.")
    allowed = {table["name"] for table in snapshot["schema"]}
    result = []
    for name in selected:
        if name in allowed:
            result.append(name)
            continue
        default = snapshot.get("default_schema")
        qualified = default + "." + name if isinstance(default, str) else None
        matches = [rule["schema"] + "." + rule["table"] for rule in snapshot["policy"]["rules"]
                   if rule["table"] == name]
        if qualified in allowed:
            result.append(qualified)
        elif len(matches) == 1:
            result.append(matches[0])
        else:
            raise DataAccessDenied("A selected table is outside your permitted data access.")
    return result


def editor_chat_answer(answer, snapshot):
    if not isinstance(answer, str) or len(answer) > 24000:
        raise DataAccessDenied("The AI response could not be safely validated.")
    raw = answer.strip()
    fence = re.fullmatch(r"```(?:sql|mysql)?\s*\n(.*?)\n```", raw, re.IGNORECASE | re.DOTALL)
    if fence:
        sql = fence.group(1)
    else:
        json_fence = re.fullmatch(r"```json[ \t]*\r?\n(.*?)\r?\n```", raw, re.IGNORECASE | re.DOTALL)
        if json_fence:
            raw = json_fence.group(1)
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            raise DataAccessDenied("Editor AI requires a single SQL block or structured SQL response.")
        if isinstance(parsed, dict) and set(parsed) == {"clarification"} and isinstance(parsed["clarification"], str):
            return "Please clarify the report using the tables and columns assigned to this data source."
        if not isinstance(parsed, dict) or set(parsed) != {"sql"}:
            raise DataAccessDenied("Editor AI requires a single SQL block or structured SQL response.")
        sql = parsed["sql"]
    return "```sql\n" + validate_editor_sql(sql, snapshot) + "\n```"
