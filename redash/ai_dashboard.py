# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Validated reporting plans. Model output never supplies application code or options."""

import json
import re

import sqlparse
from sqlparse import tokens as T
from sqlparse.sql import Function

SUPPORTED_SOURCES = {"pg", "mysql"}
MAX_WIDGETS = 6
SAFE_FUNCTIONS = set("""count sum avg min max coalesce nullif round floor ceil ceiling abs concat concat_ws
lower upper trim ltrim rtrim substring substr replace length char_length ifnull if date date_trunc date_format
year month day dayofmonth dayofweek week quarter from_unixtime unix_timestamp datediff timestampdiff
now current_date current_timestamp extract cast convert to_char to_date date_part row_number rank dense_rank
lag lead first_value last_value over filter greatest least stddev stddev_pop variance power sqrt mod
strftime julianday iif str_to_date timestampadd date_add date_sub adddate subdate""".split())
FORBIDDEN = set("""insert update delete merge replace into outfile dumpfile copy create alter drop truncate grant
revoke call execute exec do set reset begin start commit rollback savepoint release prepare deallocate
vacuum analyze explain lock unlock attach detach pragma load handler for share recursive""".split())


def validate_readonly_sql(sql):
    if not isinstance(sql, str) or not 1 <= len(sql.strip()) <= 16000 or "\x00" in sql:
        raise ValueError("Each visualization needs a SELECT query of at most 16000 characters.")
    statements = sqlparse.parse(sql)
    if len(statements) != 1 or statements[0].get_type() != "SELECT":
        raise ValueError("AI reports require one SELECT or WITH ... SELECT statement.")
    for token in statements[0].flatten():
        if token.ttype in T.Comment:
            raise ValueError("AI report queries must not contain SQL comments.")
        if token.ttype in T.Keyword.DDL or (token.ttype in T.Keyword.DML and token.normalized != "SELECT"):
            raise ValueError("AI report queries cannot modify data.")
        if token.ttype not in T.Literal.String and set(token.value.lower().split()) & FORBIDDEN:
            raise ValueError("AI report queries cannot contain commands or locking clauses.")
        if token.value == ":=" or token.value.startswith("@"):
            raise ValueError("Session variables are not supported in AI report queries.")

    def check_functions(group):
        for token in group.tokens:
            if isinstance(token, Function):
                name = (token.get_name() or "").strip('`"').lower()
                if name not in SAFE_FUNCTIONS:
                    raise ValueError(f"Function {name[:60]} is not supported for automatic AI reporting.")
            if token.is_group:
                check_functions(token)
    check_functions(statements[0])
    return sql.strip().rstrip(";").strip()


def text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Invalid {label} in AI plan.")
    return value.strip()


def parse_plan(answer):
    if not isinstance(answer, str) or len(answer) > 100000:
        raise ValueError("The AI dashboard plan is too large.")
    raw = answer.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    try:
        plan = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise ValueError("AI did not return a valid dashboard plan. Refine the prompt and try again.") from error
    if not isinstance(plan, dict):
        raise ValueError("AI must return a dashboard plan object.")
    if "clarification" in plan:
        return {"clarification": text(plan["clarification"], "clarification", 2000)}
    widgets = plan.get("widgets")
    if not isinstance(widgets, list) or not 1 <= len(widgets) <= MAX_WIDGETS:
        raise ValueError("A dashboard must contain between 1 and 6 visualizations.")
    result = {"title": text(plan.get("title"), "dashboard title", 200),
              "description": str(plan.get("description", ""))[:4000], "widgets": []}
    for widget in widgets:
        if not isinstance(widget, dict) or widget.get("visualization") not in ("table", "bar", "line", "pie", "counter"):
            raise ValueError("Unsupported visualization in AI dashboard plan.")
        kind = widget["visualization"]
        x, y = widget.get("x", ""), widget.get("y", [])
        if not isinstance(x, str) or not isinstance(y, list) or len(y) > 8:
            raise ValueError("Invalid chart column mapping.")
        if any(not isinstance(col, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", col) for col in y):
            raise ValueError("Chart values must use simple SQL output aliases.")
        if kind in ("bar", "line", "pie") and (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", x) or not y or x in y):
            raise ValueError("Charts require a category/time column and numeric value aliases.")
        if kind == "counter" and len(y) != 1:
            raise ValueError("Counters require one numeric result alias.")
        sql = validate_readonly_sql(widget.get("sql"))
        result["widgets"].append({"title": text(widget.get("title"), "visualization title", 200),
                                  "sql": sql, "visualization": kind, "x": x, "y": y})
    return result


def visualization_options(widget):
    kind = widget["visualization"]
    if kind == "table":
        return "TABLE", {"itemsPerPage": 20}
    if kind == "counter":
        return "COUNTER", {"counterColName": widget["y"][0], "counterLabel": widget["title"], "rowNumber": 1}
    return "CHART", {"globalSeriesType": {"bar": "column", "line": "line", "pie": "pie"}[kind],
                     "columnMapping": {widget["x"]: "x", **{col: "y" for col in widget["y"]}},
                     "legend": {"enabled": True}, "sortX": kind == "line"}


def reference_schema(schema, selected, dialect):
    if not isinstance(selected, list) or not 1 <= len(selected) <= 12 or any(not isinstance(t, str) for t in selected):
        raise ValueError("Choose between 1 and 12 relevant tables.")
    tables = {table["name"]: table for table in schema}
    if any(name not in tables for name in selected):
        raise ValueError("A selected table is not present in this data source's cached schema. Refresh the schema.")
    lines = ["SQL dialect: " + ("MySQL / MariaDB" if dialect == "mysql" else "PostgreSQL")]
    for name in dict.fromkeys(selected):
        columns = tables[name].get("columns", [])
        names = [c.get("name", "") if isinstance(c, dict) else c for c in columns]
        lines.append(name + "(" + ", ".join(names) + ")")
    reference = "\n".join(lines)
    if len(reference) > 16000:
        raise ValueError("The selected schema is too large. Choose fewer tables.")
    return reference
