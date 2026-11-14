# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Compile SELECTs against a closed table/column policy, never execute raw SQL.

SQLGlot is a transpiler, not a security validator. This compiler additionally
limits the AST grammar, resolves every column against a closed schema, isolates
each real table behind an explicit projection, and regenerates canonical SQL.
"""
from redash.data_access_runtime import DataAccessDenied


MAX_SQL_LENGTH = 16000
MAX_AST_NODES = 5000
NORMALIZED_NATIVE_CLASSES = {"TimeToStr", "TsOrDsToDate", "UnixToTime", "TsOrDsToTimestamp"}

# Only these node arguments may be nonempty. Parser extensions such as hints,
# locks, file output, table functions and dialect modifiers are never ignored.
NODE_ARGS = {
    "Select": {"expressions", "from_", "joins", "where", "group", "having", "order", "limit", "offset", "distinct", "with_"},
    "From": {"this"}, "Table": {"this", "db", "alias"},
    "Identifier": {"this", "quoted"}, "Column": {"this", "table", "db"},
    "Alias": {"this", "alias"}, "TableAlias": {"this"}, "Star": set(),
    "Literal": {"this", "is_string"}, "Null": set(), "Boolean": {"this"},
    "Where": {"this"}, "Having": {"this"}, "Group": {"expressions"},
    "Order": {"expressions"}, "Ordered": {"this", "desc", "nulls_first"},
    "Limit": {"expression"}, "Offset": {"expression"},
    "Join": {"this", "on", "side", "kind", "using"},
    "Subquery": {"this", "alias"}, "CTE": {"this", "alias"},
    "With": {"expressions"}, "Union": {"this", "expression", "distinct", "order", "limit", "offset", "with_"},
    "Distinct": {"expressions"}, "Paren": {"this"},
    "And": {"this", "expression"}, "Or": {"this", "expression"}, "Not": {"this"},
    "EQ": {"this", "expression"}, "NEQ": {"this", "expression"},
    "GT": {"this", "expression"}, "GTE": {"this", "expression"},
    "LT": {"this", "expression"}, "LTE": {"this", "expression"},
    "Is": {"this", "expression"}, "In": {"this", "expressions", "query"},
    "Between": {"this", "low", "high"}, "Like": {"this", "expression"},
    "Add": {"this", "expression"}, "Sub": {"this", "expression"},
    "Mul": {"this", "expression"}, "Div": {"this", "expression", "typed", "safe"},
    "Mod": {"this", "expression"}, "Neg": {"this"},
    "Case": {"this", "ifs", "default"}, "If": {"this", "true", "false"},
    "Exists": {"this"}, "Cast": {"this", "to"},
    "DataType": {"this", "expressions", "nested"}, "DataTypeParam": {"this", "expression"},
    "Interval": {"this", "unit"}, "Var": {"this"},
    "Window": {"this", "partition_by", "order", "over"},
}


def _policy_schema(policy, connector):
    schema = {}
    rules = policy.get("rules") if isinstance(policy, dict) else None
    if not isinstance(rules, list) or not rules:
        raise DataAccessDenied("No tables are permitted by this data access policy.")
    for rule in rules:
        if not isinstance(rule, dict):
            raise DataAccessDenied("Invalid table access policy.")
        database, table, columns = rule.get("schema"), rule.get("table"), rule.get("columns")
        if (not isinstance(database, str) or not database or not isinstance(table, str) or not table
                or not isinstance(columns, list) or not columns
                or any(not isinstance(value, str) or not value for value in columns)):
            raise DataAccessDenied("Policies require exact schemas, tables and explicit column names.")
        if (database.lower() in {"mysql", "sys", "information_schema", "performance_schema", "pg_catalog"}
                or connector == "pg" and database.startswith("pg_")):
            raise DataAccessDenied("System schemas cannot be queried through data access policies.")
        if connector == "mysql" and rule.get("catalog") not in (None, "def"):
            raise DataAccessDenied("MySQL catalog must be null or def.")
        target = schema.setdefault(database, {}).setdefault(table, {})
        for column in columns:
            target[column] = "UNKNOWN"
    return schema


def _check_ast(tree, exp, native_functions):
    nodes = list(tree.walk())
    if len(nodes) > MAX_AST_NODES:
        raise DataAccessDenied("This SQL query is too complex for protected execution.")
    for node in nodes:
        if node.comments:
            raise DataAccessDenied("SQL comments are unsupported in protected queries.")
        name = type(node).__name__
        allowed = NODE_ARGS.get(name)
        if allowed is None:
            if isinstance(node, exp.Func):
                function = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
                if function.lower() not in native_functions and name not in NORMALIZED_NATIVE_CLASSES:
                    raise DataAccessDenied("This SQL function is unsupported in protected queries.")
                # Function children remain subject to the same closed AST grammar.
                allowed = set(node.arg_types)
            else:
                raise DataAccessDenied("Unsupported SQL construct in protected queries: {}.".format(name))
        for key, value in node.args.items():
            if value is not None and value is not False and value != [] and key not in allowed:
                raise DataAccessDenied("Unsupported SQL modifier in protected queries.")
        if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier):
            raise DataAccessDenied("Only ordinary table references are supported.")
        if isinstance(node, exp.Var):
            parent = node.parent
            unit_context = (isinstance(parent, exp.Extract) and parent.this is node
                            or type(parent).__name__ in {"Interval", "TimestampDiff", "TimestampAdd", "DateAdd", "DateSub", "DateDiff"}
                            and parent.args.get("unit") is node)
            if (not unit_context
                    or node.name.upper() not in {"YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND", "WEEK", "QUARTER"}):
                raise DataAccessDenied("Variables and opaque SQL fragments are unsupported.")
        if isinstance(node, exp.DataType):
            if str(node.this).split(".")[-1] not in {
                "BOOLEAN", "TINYINT", "SMALLINT", "INT", "BIGINT", "UTINYINT", "USMALLINT", "UINT", "UBIGINT",
                "FLOAT", "DOUBLE", "DECIMAL", "CHAR", "VARCHAR", "TEXT", "DATE", "DATETIME", "TIMESTAMP", "TIME",
            }:
                raise DataAccessDenied("Only ordinary built-in cast types are supported.")


def _check_columns(tree, exp, traverse_scope, allowed_schema, default_schema):
    # SQLGlot deliberately permits some unresolved HAVING/output-alias names.
    # Walk ALL Column nodes, not scope.columns (which omits those references).
    scopes = {id(scope.expression): scope for scope in traverse_scope(tree)}

    def fields(source):
        if isinstance(source, exp.Table):
            return set(allowed_schema.get(source.db or default_schema, {}).get(source.name, {}))
        return set(source.expression.named_selects)

    for column in tree.find_all(exp.Column):
        owner = column.find_ancestor(exp.Select, exp.Union)
        scope = scopes.get(id(owner))
        if scope is None or column.db or column.catalog or column.is_star:
            raise DataAccessDenied("A SQL column could not be resolved safely.")
        if column.table:
            current = scope
            while current is not None:
                source = current.selected_sources.get(column.table)
                if source is not None:
                    if column.name not in fields(source[1]):
                        raise DataAccessDenied("A SQL column is outside the permitted policy.")
                    break
                current = current.parent
            if current is None:
                raise DataAccessDenied("A SQL column references an unknown table alias.")
            continue
        parent, alias_context = column.parent, False
        while parent is not None and parent is not owner:
            if isinstance(parent, (exp.Having, exp.Order, exp.Group)):
                alias_context = True
                break
            parent = parent.parent
        if alias_context and column.name in owner.named_selects:
            continue
        current = scope
        while current is not None:
            matches = [alias for alias, (_, source) in current.selected_sources.items() if column.name in fields(source)]
            if matches:
                if len(matches) != 1:
                    raise DataAccessDenied("An unqualified SQL column is ambiguous.")
                column.set("table", exp.to_identifier(matches[0], quoted=True))
                break
            current = current.parent
        if current is None:
            raise DataAccessDenied("A SQL column is outside the permitted policy.")


def compile_policy_sql(sql, policy, connector="mysql", default_schema=None):
    """Return canonical SELECT SQL whose real sources expose allowed columns only.

    `policy.rules` is a union of exact table/column snapshots. The caller must
    validate that these names refer to supported base tables on the data source.
    PostgreSQL callers must additionally fix search_path to pg_catalog.
    """
    if connector not in ("mysql", "pg"):
        raise DataAccessDenied("This connector does not support application SQL policies.")
    if not isinstance(sql, str) or not 1 <= len(sql) <= MAX_SQL_LENGTH:
        raise DataAccessDenied("Protected SQL must contain at most 16000 characters.")
    from redash.data_access_mysql import NATIVE_FUNCTIONS, validate_mysql_sql
    # Keep the independently tested lexer defenses, including native function
    # names, comments, variables, quoted/qualified calls and dialect ambiguities.
    validate_mysql_sql(sql)
    allowed_schema = _policy_schema(policy, connector)
    dialect = "mysql" if connector == "mysql" else "postgres"
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.qualify import qualify
        from sqlglot.optimizer.scope import traverse_scope
        from sqlglot.schema import MappingSchema
        from sqlglot.errors import ErrorLevel
        expressions = sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.RAISE)
        if len(expressions) != 1 or not isinstance(expressions[0], (exp.Select, exp.Union)):
            raise DataAccessDenied("Protected execution requires a single SELECT query.")
        tree = expressions[0]
        _check_ast(tree, exp, NATIVE_FUNCTIONS)
        for scope in traverse_scope(tree):
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    database = source.db or default_schema
                    if database not in allowed_schema or source.name not in allowed_schema[database]:
                        raise DataAccessDenied("This query references a table outside the permitted policy.")
        # MySQL allows output aliases in GROUP BY, but never in WHERE.
        # Resolve only GROUP BY aliases; enabling SQLGlot's general alias
        # expansion would also turn forbidden WHERE columns into output aliases.
        for scope in traverse_scope(tree):
            select = scope.expression
            group = select.args.get("group")
            if not isinstance(select, exp.Select) or group is None:
                continue
            aliases = {item.alias: item.this for item in select.expressions if isinstance(item, exp.Alias)}
            source_columns = set()
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    source_columns.update(allowed_schema.get(source.db or default_schema, {}).get(source.name, {}))
                else:
                    source_columns.update(source.expression.named_selects)
            def group_alias(node):
                if (isinstance(node, exp.Column) and not node.table and not node.db
                        and node.name not in source_columns and node.name in aliases):
                    return aliases[node.name].copy()
                return node
            group.transform(group_alias, copy=False)
        tree = qualify(tree, dialect=dialect, db=default_schema,
                       schema=MappingSchema(allowed_schema, dialect=dialect, normalize=False),
                       expand_alias_refs=False, expand_stars=True,
                       allow_partial_qualification=False, validate_qualify_columns=True,
                       quote_identifiers=True, identify=True)
        _check_ast(tree, exp, NATIVE_FUNCTIONS)
        _check_columns(tree, exp, traverse_scope, allowed_schema, default_schema)
        tables = []
        for scope in traverse_scope(tree):
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    tables.append(source)
        for table in tables:
            database = table.db or default_schema
            columns = allowed_schema.get(database, {}).get(table.name)
            if not columns:
                raise DataAccessDenied("This query references a table outside the permitted policy.")
            physical = exp.Table(this=exp.to_identifier(table.name, quoted=True),
                                 db=exp.to_identifier(database, quoted=True))
            projection = exp.Select(expressions=[exp.column(column, quoted=True) for column in columns]).from_(physical)
            table.replace(exp.Subquery(this=projection,
                alias=exp.TableAlias(this=exp.to_identifier(table.alias_or_name, quoted=True))))
        _check_ast(tree, exp, NATIVE_FUNCTIONS)
        result = tree.sql(dialect=dialect, identify=True, unsupported_level=ErrorLevel.RAISE, comments=False)
        if len(result) > 100000:
            raise DataAccessDenied("The expanded protected query is too large.")
        validate_mysql_sql(result)
        return result
    except DataAccessDenied:
        raise
    except Exception as error:
        raise DataAccessDenied("SQL could not be safely resolved against the permitted tables and columns.") from error


validate_scoped_sql = compile_policy_sql
rewrite_query = compile_policy_sql


def compile_select_sql(sql, cursor, default_schema=None):
    """Compile unrestricted SELECT access using names-only metadata, never raw SQL.

    Administrator/unprotected means all ordinary user base tables are eligible;
    it does not permit arbitrary statements or bypass the closed SQL grammar.
    """
    from redash.data_access_mysql import NATIVE_FUNCTIONS, validate_mysql_sql
    validate_mysql_sql(sql)
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.scope import traverse_scope
        from sqlglot.errors import ErrorLevel
        statements = sqlglot.parse(sql, read="mysql", error_level=ErrorLevel.RAISE)
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
            raise DataAccessDenied("Only a single SELECT query is supported.")
        tree = statements[0]
        _check_ast(tree, exp, NATIVE_FUNCTIONS)
        objects = set()
        for scope in traverse_scope(tree):
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    schema = source.db or default_schema
                    if not schema or schema.lower() in {"mysql", "sys", "information_schema", "performance_schema"}:
                        raise DataAccessDenied("SELECT access requires an ordinary user schema.")
                    objects.add((schema, source.name))
        if len(objects) > 100:
            raise DataAccessDenied("A SELECT query may reference at most 100 base tables.")
        rules = []
        for schema, table in sorted(objects):
            cursor.execute("SELECT TABLE_TYPE,ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                           (schema, table))
            rows = cursor.fetchall()
            if len(rows) != 1 or tuple(rows[0]) != ("BASE TABLE", "InnoDB"):
                raise DataAccessDenied("SELECT access supports existing InnoDB base tables only.")
            cursor.execute("SELECT COLUMN_NAME,EXTRA,GENERATION_EXPRESSION FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
                           (schema, table))
            metadata = cursor.fetchall()
            if any(row[2] or any(flag in str(row[1]).upper() for flag in ("VIRTUAL", "STORED", "PERSISTENT")) for row in metadata):
                raise DataAccessDenied("Tables with generated columns are unsupported for SELECT-only execution.")
            columns = [row[0] for row in metadata]
            if not columns or len(columns) > 2000:
                raise DataAccessDenied("Table column metadata is unavailable or too large.")
            rules.append({"catalog": None, "schema": schema, "table": table, "columns": columns})
        # Constants-only SELECTs have no real source and still pass the identical
        # closed compiler. The synthetic schema is never referenced or emitted.
        if not rules:
            rules = [{"catalog": None, "schema": "__bidash_constants__", "table": "__none__", "columns": ["__none__"]}]
        return compile_policy_sql(sql, {"rules": rules}, connector="mysql", default_schema=default_schema)
    except DataAccessDenied:
        raise
    except Exception as error:
        raise DataAccessDenied("SELECT SQL could not be safely compiled.") from error
