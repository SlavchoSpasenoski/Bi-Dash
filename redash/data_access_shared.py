# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Shared read-only MySQL execution after closed-subset SQL authorization."""
from redash.data_access_runtime import DataAccessDenied

def run_shared_query(source, policy, query):
    if source.type != "mysql":
        raise DataAccessDenied("Shared group SQL enforcement currently supports mysql connections only.")
    from redash.data_access_mysql import _connect, detect_server
    from redash.query_runner import get_query_runner
    from redash.query_runner.mysql import types_map
    from rq import get_current_job
    import MySQLdb
    job = get_current_job()
    timeout = max(1, int(min(60, job.timeout))) if job and job.timeout and job.timeout > 0 else 60
    connection, cursor = None, None
    try:
        connection = _connect(source, timeout)
        cursor = connection.cursor()
        cursor.execute("SELECT VERSION(), @@version_comment")
        dialect = detect_server(*cursor.fetchone())
        cursor.execute("SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION'")
        if dialect == "mysql":
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=%s", (timeout * 1000,))
        else:
            cursor.execute("SET SESSION max_statement_time=%s", (timeout,))
        cursor.execute("SET SESSION lock_wait_timeout=%s", (timeout,))
        cursor.execute("SET SESSION innodb_lock_wait_timeout=%s", (timeout,))
        cursor.execute("START TRANSACTION READ ONLY")
        if policy.get("unrestricted"):
            from redash.data_access_sql import compile_select_sql
            sql = compile_select_sql(query, cursor, default_schema=source.options.get("db"))
        else:
            from redash.data_access_sql import compile_policy_sql
            sql = compile_policy_sql(query, policy, connector="mysql", default_schema=source.options.get("db"))
            # Recheck eligible objects on every run: a saved table cannot silently
            # become a definer view or expose subsequently added columns.
            objects = sorted({(rule["schema"], rule["table"]) for rule in policy["rules"]})
            placeholders = ",".join(["(%s,%s)"] * len(objects))
            cursor.execute("SELECT TABLE_SCHEMA,TABLE_NAME,TABLE_TYPE,ENGINE FROM information_schema.TABLES "
                           "WHERE (TABLE_SCHEMA,TABLE_NAME) IN (" + placeholders + ")",
                           tuple(value for obj in objects for value in obj))
            actual = cursor.fetchall()
            if ({(row[0], row[1]) for row in actual} != set(objects)
                    or any(tuple(row[2:]) != ("BASE TABLE", "InnoDB") for row in actual)):
                raise DataAccessDenied("Group rules support existing InnoDB base tables only.")
            cursor.execute("SELECT TABLE_SCHEMA,TABLE_NAME FROM information_schema.COLUMNS "
                           "WHERE (TABLE_SCHEMA,TABLE_NAME) IN (" + placeholders + ") "
                           "AND (COALESCE(GENERATION_EXPRESSION,'')<>'' OR LOCATE('VIRTUAL',UPPER(EXTRA))>0 "
                           "OR LOCATE('STORED',UPPER(EXTRA))>0 OR LOCATE('PERSISTENT',UPPER(EXTRA))>0) LIMIT 1",
                           tuple(value for obj in objects for value in obj))
            if cursor.fetchall():
                raise DataAccessDenied("Tables with generated columns are unsupported for SELECT-only execution.")
        cursor.execute(sql)
        if cursor.description is None:
            raise DataAccessDenied("Protected queries must return rows.")
        runner = get_query_runner("mysql", source.options)
        columns = runner.fetch_columns([(item[0], types_map.get(item[1])) for item in cursor.description])
        names = [column["name"] for column in columns]
        return {"columns": columns, "rows": [dict(zip(names, row)) for row in cursor.fetchall()]}, None
    except MySQLdb.Error as error:
        code = error.args[0] if error.args and type(error.args[0]) is int else "unavailable"
        raise DataAccessDenied("Shared read-only query was rejected ({}).".format(code)) from error
    finally:
        if cursor is not None:
            try: cursor.close()
            except MySQLdb.Error: pass
        if connection is not None:
            try: connection.rollback()
            except MySQLdb.Error: pass
            finally:
                try: connection.close()
                except MySQLdb.Error: pass

def validate_shared_source(source):
    """Validate SELECT-only execution without inspecting or changing account grants."""
    run_shared_query(source, {"unrestricted": True}, "SELECT 1")
    return {"select_only": True}
