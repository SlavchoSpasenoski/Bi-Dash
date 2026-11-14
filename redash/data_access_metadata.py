# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Bounded, names-only metadata for the administrator policy editor."""
import select
import time

MAX_METADATA_ITEMS = 2000
METADATA_TIMEOUT = 15


class MetadataUnavailable(ValueError):
    pass


def _name(value, label):
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(ord(char) < 32 for char in value)):
        raise ValueError("Invalid {} name.".format(label))


def metadata_query(connector, schema=None, table=None):
    """Return one fixed parameterized names-only query and its response key."""
    if connector not in ("mysql", "pg"):
        raise ValueError("Metadata selection currently supports PostgreSQL and mysql connections.")
    if schema is not None:
        _name(schema, "schema")
        if (connector == "mysql" and schema.lower() in {"mysql", "information_schema", "performance_schema", "sys"}
                or connector == "pg" and (schema in {"pg_catalog", "information_schema"} or schema.startswith("pg_"))):
            raise ValueError("System schemas are unavailable for access policies.")
    if table is not None:
        _name(table, "table")
        if schema is None:
            raise ValueError("Choose a schema before a table.")
    limit = MAX_METADATA_ITEMS + 1
    if connector == "mysql":
        if schema is None:
            return "schemas", """SELECT SCHEMA_NAME FROM information_schema.SCHEMATA
                WHERE LOWER(SCHEMA_NAME) NOT IN ('information_schema','performance_schema','mysql','sys')
                ORDER BY SCHEMA_NAME LIMIT %s""", (limit,)
        if table is None:
            return "tables", """SELECT TABLE_NAME FROM information_schema.TABLES
                WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' AND ENGINE='InnoDB'
                ORDER BY TABLE_NAME LIMIT %s""", (schema, limit)
        return "columns", """SELECT c.COLUMN_NAME FROM information_schema.COLUMNS c
            JOIN information_schema.TABLES t ON t.TABLE_SCHEMA=c.TABLE_SCHEMA AND t.TABLE_NAME=c.TABLE_NAME
            WHERE c.TABLE_SCHEMA=%s AND c.TABLE_NAME=%s AND t.TABLE_TYPE='BASE TABLE' AND t.ENGINE='InnoDB'
            ORDER BY c.ORDINAL_POSITION LIMIT %s""", (schema, table, limit)
    # Only offer objects the first enforcement adapter can actually approve.
    eligible = """c.relkind='r' AND NOT c.relispartition AND NOT c.relhassubclass
        AND NOT c.relhasrules AND NOT c.relrowsecurity
        AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)
        AND has_schema_privilege(n.oid,'USAGE') AND has_any_column_privilege(c.oid,'SELECT')"""
    if schema is None:
        return "schemas", """SELECT n.nspname FROM pg_catalog.pg_namespace n
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND left(n.nspname,3)<>'pg_'
            ORDER BY n.nspname LIMIT %s""", (limit,)
    if table is None:
        return "tables", """SELECT c.relname FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND """ + eligible + " ORDER BY c.relname LIMIT %s", (schema, limit)
    return "columns", """SELECT a.attname FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
        JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
        WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped
        AND tn.nspname='pg_catalog' AND has_column_privilege(c.oid,a.attnum,'SELECT') AND """ + eligible + " ORDER BY a.attnum LIMIT %s", (schema, table, limit)


def _mysql_rows(source, sql, params):
    from redash.data_access_mysql import _connect, detect_server
    connection, cursor = None, None
    try:
        connection = _connect(source, METADATA_TIMEOUT)
        cursor = connection.cursor()
        cursor.execute("SELECT VERSION(), @@version_comment")
        dialect = detect_server(*cursor.fetchone())
        if dialect == "mysql":
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=%s", (METADATA_TIMEOUT * 1000,))
        else:
            cursor.execute("SET SESSION max_statement_time=%s", (METADATA_TIMEOUT,))
        cursor.execute("SET SESSION lock_wait_timeout=%s", (METADATA_TIMEOUT,))
        cursor.execute("SET SESSION innodb_lock_wait_timeout=%s", (METADATA_TIMEOUT,))
        cursor.execute("START TRANSACTION READ ONLY")
        cursor.execute(sql, params)
        return cursor.fetchmany(MAX_METADATA_ITEMS + 1)
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()


def _wait_pg(connection):
    from psycopg2.extensions import POLL_OK, POLL_READ, POLL_WRITE
    deadline = time.monotonic() + METADATA_TIMEOUT
    while True:
        state = connection.poll()
        if state == POLL_OK:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MetadataUnavailable("Metadata query timed out.")
        if state == POLL_READ:
            select.select([connection.fileno()], [], [], remaining)
        elif state == POLL_WRITE:
            select.select([], [connection.fileno()], [], remaining)
        else:
            raise MetadataUnavailable("Metadata connection is unavailable.")


def _pg_rows(source, sql, params):
    from redash.query_runner import get_query_runner
    from redash.query_runner.pg import _cleanup_ssl_certs
    runner = get_query_runner("pg", source.options)
    connection = None
    try:
        connection = runner._get_connection()
        _wait_pg(connection)
        cursor = connection.cursor()
        for setting in ("BEGIN READ ONLY", "SET LOCAL search_path=pg_catalog",
                        "SET LOCAL statement_timeout='15s'", "SET LOCAL lock_timeout='15s'"):
            cursor.execute(setting)
            _wait_pg(connection)
        cursor.execute(sql, params)
        _wait_pg(connection)
        return cursor.fetchmany(MAX_METADATA_ITEMS + 1)
    finally:
        try:
            if connection is not None:
                # Closing rolls back the open read-only transaction, including on timeout.
                connection.close()
        finally:
            _cleanup_ssl_certs(getattr(runner, "ssl_config", {}))


def fetch_metadata(source, schema=None, table=None):
    key, sql, params = metadata_query(source.type, schema, table)
    if source.uses_ssh_tunnel:
        raise ValueError("Policy metadata selection does not yet support SSH tunnels.")
    try:
        rows = _mysql_rows(source, sql, params) if source.type == "mysql" else _pg_rows(source, sql, params)
        names = [row[0] for row in rows]
        if any(not isinstance(name, str) for name in names):
            raise MetadataUnavailable("Metadata names are unavailable.")
        return {key: names[:MAX_METADATA_ITEMS], "truncated": len(names) > MAX_METADATA_ITEMS}
    except Exception as error:
        # Neither SQL driver errors nor connection options are safe API responses.
        raise MetadataUnavailable("Could not load metadata names from this connection.") from error
