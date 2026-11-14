# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""MySQL 8.0/8.4 and MariaDB 10.6 explicit-column profiles. No roles allowed."""
import re

from redash.data_access_runtime import DataAccessDenied

SYSTEM_SCHEMAS = {"mysql", "information_schema", "performance_schema", "sys"}
IDENTIFIER = r"`(?:``|[^`])+`"
ACCOUNT = r"(?P<user>`(?:``|[^`])+`|'(?:''|[^'])+')@(?P<host>`(?:``|[^`])+`|'(?:''|[^'])+')"
NATIVE_FUNCTIONS = set("""count sum avg min max coalesce nullif round floor ceil ceiling abs concat concat_ws
lower upper trim ltrim rtrim substring substr replace length char_length ifnull if date date_format
year month day dayofmonth dayofweek week quarter from_unixtime unix_timestamp datediff timestampdiff
now current_date current_timestamp extract cast convert row_number rank dense_rank lag lead first_value last_value
greatest least stddev stddev_pop variance power sqrt mod str_to_date timestampadd date_add date_sub adddate subdate""".split())






def detect_server(version, comment):
    version, comment = str(version), str(comment)
    combined = (version + " " + comment).lower()
    if ("mariadb" in combined and re.match(r"^(?:5\.5\.5-)?10\.6\.\d+(?:[-+].*)?$", version)):
        return "mariadb"
    # Ubuntu's upstream MySQL packages report "(Ubuntu)" rather than
    # "MySQL" in version_comment. Require the matching package version too;
    # an arbitrary compatible server must not pass on its numeric version alone.
    ubuntu_package = (re.fullmatch(r"8\.(?:0|4)\.\d+-0ubuntu\d+(?:\.\d+)+", version) is not None
                      and comment.strip().lower() in ("ubuntu", "(ubuntu)"))
    if (re.fullmatch(r"8\.(?:0|4)\.\d+(?:[-+].*)?", version)
            and ("mysql" in comment.lower() or ubuntu_package)
            and not any(value in combined for value in ("mariadb", "percona", "aurora"))):
        return "mysql"
    raise DataAccessDenied("SELECT-only connections support MySQL 8.0/8.4 (Oracle or Ubuntu packages) and MariaDB 10.6 only.")






def validate_mysql_sql(query):
    if isinstance(query, str) and "\\" in query:
        raise DataAccessDenied("Backslash escapes are unsupported in protected MySQL queries; use ordinary SQL literals.")
    if isinstance(query, str) and any(char.isspace() and char not in " \t\r\n\v\f" for char in query):
        raise DataAccessDenied("Non-ASCII whitespace is unsupported in protected MySQL queries.")
    from redash.ai_dashboard import validate_readonly_sql
    import sqlparse
    from sqlparse import tokens as T
    from sqlparse.sql import Function, Identifier
    query = validate_readonly_sql(query)

    def visit(group):
        for token in group.tokens:
            if isinstance(token, Function):
                if (not re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(", token.value)
                        or (token.get_name() or "").lower() not in NATIVE_FUNCTIONS
                        or isinstance(group, Identifier) and group.get_parent_name()):
                    raise DataAccessDenied("Only unqualified native MySQL reporting functions without whitespace before '(' are supported.")
            if token.is_group:
                visit(token)
    statement = sqlparse.parse(query)[0]
    visit(statement)
    # sqlparse does not always build Function nodes for `name (...)`. Inspect
    # every lexical opening parenthesis too; string contents remain single
    # tokens, so text resembling calls inside literals never enters this gate.
    syntax_parentheses = {
        "SELECT", "FROM", "JOIN", "USING", "WHERE", "HAVING", "ON", "WHEN", "THEN", "ELSE",
        "AND", "OR", "XOR", "NOT", "IN", "EXISTS", "AS", "OVER", "BY",
        "DISTINCT", "ALL", "CASE",
    }
    flattened = list(statement.flatten())
    significant = []
    for index, token in enumerate(flattened):
        if token.ttype in T.Error:
            raise DataAccessDenied("Unrecognized SQL tokens are unsupported in protected MySQL queries.")
        if token.is_whitespace:
            continue
        if token.value == "(" and significant:
            previous_index, previous = significant[-1]
            dotted = len(significant) > 1 and significant[-2][1].value == "."
            if previous.ttype in T.Keyword and previous.normalized in syntax_parentheses and not dotted:
                significant.append((index, token))
                continue
            value = previous.value
            candidate = (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is not None
                         or previous.ttype in T.Name or value.startswith(('`', '"', "'")))
            if candidate and (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)
                              or value.lower() not in NATIVE_FUNCTIONS or dotted
                              or previous_index != index - 1):
                raise DataAccessDenied("Only adjacent, unquoted and unqualified native MySQL reporting calls are supported.")
        significant.append((index, token))
    return query


def _connect(source, timeout):
    if source.type != "mysql" or source.uses_ssh_tunnel:
        raise DataAccessDenied("Restricted MySQL profiles require a direct mysql connection.")
    import MySQLdb
    from redash.query_runner import get_query_runner
    options = source.options
    params = {"host": options.get("host", "127.0.0.1"), "port": int(options.get("port", 3306)),
              "user": options.get("user", ""), "passwd": options.get("passwd", ""),
              "charset": "utf8mb4", "use_unicode": True, "autocommit": False,
              "connect_timeout": min(10, timeout), "read_timeout": timeout, "write_timeout": timeout,
              "local_infile": 0, "multi_statements": False}
    ssl = get_query_runner("mysql", options)._get_ssl_parameters()
    if ssl:
        params["ssl"] = ssl
    return MySQLdb.connect(**params)
