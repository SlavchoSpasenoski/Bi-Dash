# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Compile a bounded Dataset projection for offline correctness testing.

The caller supplies authoritative output types and must validate base-table and
column access with the existing execution policy before running the SQL.
"""

import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


class DatasetSqlError(ValueError):
    """A Dataset input is outside the prototype's supported grammar."""


class DatasetWildcard(DatasetSqlError):
    """The saved projection requires source catalog resolution."""


def preserve_dataset_numbers(data):
    """Keep exact SQL numbers through JSON, CSV and browser transport."""
    return {
        **data,
        "rows": [
            {name: str(value) if isinstance(value, Decimal) or type(value) is int and abs(value) >= 2**53
             else value for name, value in row.items()}
            for row in data["rows"]
        ],
    }


_DIALECTS = {"postgres": "postgres", "mysql": "mysql", "mariadb": "mysql"}
_NUMERIC_TYPES = {"integer", "decimal"}
_FIELD_TYPES = _NUMERIC_TYPES | {"text", "date", "datetime", "boolean"}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
_DECIMAL = re.compile(r"-?[0-9]{1,28}(?:\.[0-9]{1,10})?")
_OPERATORS = {"+": exp.Add, "-": exp.Sub, "*": exp.Mul, "/": exp.Div}
_BASE_ARGS = {
    exp.Select: {"expressions", "from_", "where", "distinct", "limit", "joins"},
    exp.Join: {"this", "on", "side", "kind"},
    exp.From: {"this"},
    exp.Table: {"this", "db", "alias"},
    exp.TableAlias: {"this"},
    exp.Column: {"this", "table"},
    exp.Alias: {"this", "alias"},
    exp.Identifier: {"this", "quoted"},
    exp.Where: {"this"},
    exp.Distinct: set(),
    exp.Limit: {"expression"},
    exp.Paren: {"this"},
    exp.Literal: {"this", "is_string"},
    exp.Boolean: {"this"},
    exp.Null: set(),
    exp.Neg: {"this"},
    exp.Not: {"this"},
    exp.And: {"this", "expression"},
    exp.Or: {"this", "expression"},
    exp.Is: {"this", "expression"},
    exp.EQ: {"this", "expression"},
    exp.NEQ: {"this", "expression"},
    exp.GT: {"this", "expression"},
    exp.GTE: {"this", "expression"},
    exp.LT: {"this", "expression"},
    exp.LTE: {"this", "expression"},
}
_DATASET_ALIAS = "_dataset"


def _check_field_names(fields):
    if not isinstance(fields, Mapping) or not 1 <= len(fields) <= 64:
        raise DatasetSqlError("Supply between 1 and 64 fields.")
    if any(not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) for name in fields):
        raise DatasetSqlError("Field names must be ASCII SQL identifiers of at most 63 characters.")
    if len({name.lower() for name in fields}) != len(fields):
        raise DatasetSqlError("Field names must be unique ignoring case.")


def _output_name(projection, dialect):
    column = projection.this if isinstance(projection, exp.Alias) else projection
    if not isinstance(column, exp.Column) or not isinstance(column.this, exp.Identifier):
        raise DatasetSqlError("The base SELECT must project explicit columns.")
    identifier = projection.args["alias"] if isinstance(projection, exp.Alias) else column.this
    name = identifier.name
    if dialect == "postgres" and not identifier.args.get("quoted"):
        return name.lower()
    return name


def _parse_base(base_sql, dialect, *, allow_star=False):
    if not isinstance(base_sql, str) or not 1 <= len(base_sql) <= 16000:
        raise DatasetSqlError("Supply a base SELECT of at most 16,000 characters.")
    try:
        statements = sqlglot.parse(base_sql, read=dialect)
    except (SqlglotError, RecursionError) as error:
        raise DatasetSqlError("The base SELECT could not be parsed.") from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise DatasetSqlError("Supply exactly one base SELECT.")
    base = statements[0]
    if not isinstance(base.args.get("from_"), exp.From):
        raise DatasetSqlError("The base SELECT must read from one table.")
    joins = base.args.get("joins") or []
    if joins:
        if dialect != "mysql" or len(joins) != 1:
            raise DatasetSqlError("Use one same-source join in a Dataset base Query.")
        _join_parts(base, joins[0])
    limit = base.args.get("limit")
    if limit is not None and (not isinstance(limit, exp.Limit)
                              or not isinstance(limit.expression, exp.Literal)
                              or limit.expression.is_string
                              or not re.fullmatch(r"[0-9]+", limit.expression.this)):
        raise DatasetSqlError("Dataset LIMIT must be a nonnegative integer literal.")
    if allow_star and len(base.expressions) == 1 and type(base.expressions[0]) is exp.Star:
        star = base.expressions[0]
        if joins or any(value not in (None, False, []) for value in star.args.values()) or star.comments or base.args.get("distinct"):
            raise DatasetSqlError("Only a plain SELECT * from one table is supported.")
        # Replace only the projection for validation, then restore it.
        base.set("expressions", [exp.column("_catalog_field")])
    else:
        star = None
    for count, node in enumerate(base.walk(), start=1):
        allowed_args = _BASE_ARGS.get(type(node))
        if count > 512 or allowed_args is None or node.comments:
            raise DatasetSqlError("The base SELECT contains an unsupported SQL construct.")
        if any(key not in allowed_args and value not in (None, False, []) for key, value in node.args.items()):
            raise DatasetSqlError("The base SELECT contains an unsupported SQL modifier.")
    if star is not None:
        base.set("expressions", [star])
    return base


def dataset_source_fields(base_sql):
    """Return supported MySQL Query output names without running SQL."""
    base = _parse_base(base_sql, "mysql", allow_star=True)
    if isinstance(base.expressions[0], exp.Star):
        raise DatasetWildcard("Source catalog resolution is required for SELECT *.")
    if base.args.get("joins"):
        raise DatasetWildcard("Source catalog validation is required for a Dataset join.")
    names = [_output_name(projection, "mysql") for projection in base.expressions]
    _check_field_names(dict.fromkeys(names))
    if len(names) != len(set(names)):
        raise DatasetSqlError("Base output aliases must be unique.")
    return names


def _cast_number(expression, precision=38, scale=10):
    return exp.Cast(this=expression, to=exp.DataType.build(f"DECIMAL({precision},{scale})"))


def _divide(left, right, *, numerator_precision=48):
    # Round the scaled quotient using its exact remainder before division.
    # The final division therefore has an integer result and cannot round
    # twice or depend on MySQL's div_precision_increment.
    if sum(1 for operand in (left, right) for _ in operand.walk()) > 512:
        raise DatasetSqlError("The division expression is too complex.")
    numerator = _cast_number(exp.Mul(this=left, expression=exp.Literal.number(10**10)), numerator_precision, 10)
    divisor = exp.Nullif(this=right, expression=exp.Literal.number(0))
    remainder = exp.Mod(this=numerator.copy(), expression=divisor.copy())
    midpoint = exp.GTE(
        this=exp.Mul(this=exp.Abs(this=remainder.copy()), expression=exp.Literal.number(2)),
        expression=exp.Abs(this=divisor.copy()),
    )
    correction = exp.If(
        this=midpoint,
        true=exp.If(
            this=exp.LT(this=numerator.copy(), expression=exp.Literal.number(0)),
            true=exp.Neg(this=exp.Abs(this=divisor.copy())),
            false=exp.Abs(this=divisor.copy()),
        ),
        false=exp.Literal.number(0),
    )
    rounded_numerator = exp.Add(
        this=exp.Sub(this=numerator, expression=remainder),
        expression=correction,
    )
    units = _cast_number(
        exp.Div(this=exp.Paren(this=rounded_numerator), expression=divisor, typed=True),
        39,
        0,
    )
    return exp.Mul(this=units, expression=exp.Literal.number("0.0000000001"))


def _compile_field(spec, columns, depth=0):
    if depth > 6 or not isinstance(spec, Mapping):
        raise DatasetSqlError("Invalid or excessively nested field expression.")
    keys = set(spec)
    if keys == {"field"}:
        field = spec["field"]
        if not isinstance(field, str) or not _IDENTIFIER.fullmatch(field) or (columns is not None and field not in columns):
            raise DatasetSqlError("Field expression refers to an unknown base column.")
        field_type = "decimal" if columns is None else columns[field]
        return exp.column(field, table=_DATASET_ALIAS, quoted=True), field_type
    if keys == {"decimal"}:
        value = spec["decimal"]
        if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
            raise DatasetSqlError("Supply decimal literals as strings with at most 28 integer and 10 fractional digits.")
        return _cast_number(exp.Literal.number(value)), "decimal"
    if keys != {"op", "left", "right"} or not isinstance(spec["op"], str) or spec["op"] not in _OPERATORS:
        raise DatasetSqlError("Unsupported field expression.")
    left, left_type = _compile_field(spec["left"], columns, depth + 1)
    right, right_type = _compile_field(spec["right"], columns, depth + 1)
    if left_type not in _NUMERIC_TYPES or right_type not in _NUMERIC_TYPES:
        raise DatasetSqlError("Arithmetic requires integer or decimal operands.")

    operation = spec["op"]
    left = _cast_number(left)
    right = _cast_number(right)
    if operation == "/":
        expression = _divide(left, right)
    else:
        expression = _OPERATORS[operation](this=left, expression=right)
    return _cast_number(expression), "decimal"


def validate_dataset_fields(fields):
    """Validate draft field names and formula structure before source binding."""
    _check_field_names(fields)
    for spec in fields.values():
        _compile_field(spec, None)


def compile_dataset_sql(base_sql: str, dialect: str, columns: Mapping, fields: Mapping) -> str:
    """Project fields and typed arithmetic from one saved SELECT.

    Formula nodes are `{"field": "name"}`, `{"decimal": "1.25"}`, or a binary
    `{"op": "+", "left": ..., "right": ...}` tree. References point only to
    base columns. Direct references retain their source types. Arithmetic
    casts operands and results to DECIMAL(38,10), propagates NULL and returns
    NULL for division by zero. Division rounds ties away from zero. The
    fixture domain must fit that precision at every formula step. Overflow
    parity is outside this prototype.
    """
    if not isinstance(dialect, str) or dialect not in _DIALECTS:
        raise DatasetSqlError("Unsupported Dataset SQL dialect.")
    sql_dialect = _DIALECTS[dialect]
    base = _parse_base(base_sql, sql_dialect)
    if base.args.get("joins"):
        raise DatasetSqlError("Dataset joins require catalog validation.")
    return _compile_projection(base, sql_dialect, columns, fields)


def _compile_projection(base, sql_dialect, columns, fields, report=None, legacy_avg=False):
    _check_field_names(columns)
    _check_field_names(fields)
    if any(not isinstance(kind, str) or kind not in _FIELD_TYPES for kind in columns.values()):
        raise DatasetSqlError("Unsupported base column type.")
    names = [_output_name(projection, sql_dialect) for projection in base.expressions]
    if len(names) != len(columns) or set(names) != set(columns):
        raise DatasetSqlError("The base SELECT field names must match the declared column metadata.")
    projections = []
    field_types = {}
    for name, spec in fields.items():
        expression, field_types[name] = _compile_field(spec, columns)
        projections.append(exp.alias_(expression, name, quoted=True))
    query = exp.select(*projections).from_(base.subquery(_DATASET_ALIAS))
    if report is not None:
        query = _report_query(query, field_types, report, legacy_avg=legacy_avg)
    try:
        sql = query.sql(dialect=sql_dialect, unsupported_level=sqlglot.ErrorLevel.RAISE)
    except (SqlglotError, RecursionError) as error:
        raise DatasetSqlError("The Dataset could not be compiled for the source dialect.") from error

    if len(sql) > 128_000:
        raise DatasetSqlError("The compiled Dataset SQL is too large.")
    return sql


def compile_dataset_from_catalog(cursor, base_sql, dialect, default_schema, fields, *, report=None, policy=None,
                                 legacy_avg=False):
    """Compile using source metadata after the caller authorizes the base query.

    Execute the compiled SQL through current policy checks on the same source.
    The cursor must use the source connection's native DB-API driver.
    """
    if not isinstance(dialect, str) or dialect not in _DIALECTS:
        raise DatasetSqlError("Unsupported Dataset SQL dialect.")
    sql_dialect = _DIALECTS[dialect]
    base = _parse_base(base_sql, sql_dialect, allow_star=policy is not None and sql_dialect == "mysql")
    if base.args.get("joins"):
        columns = _joined_catalog(cursor, base, default_schema)
        return list(columns) if fields is None else _compile_projection(
            base, sql_dialect, columns, fields, report=report, legacy_avg=legacy_avg,
        )
    table = base.args["from_"].this
    if not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier):
        raise DatasetSqlError("The base SELECT must read from one ordinary table.")

    def source_name(identifier):
        name = identifier.name
        if dialect == "postgres" and not identifier.args.get("quoted"):
            return name.lower()
        return name

    schema_identifier = table.args.get("db")
    schema = source_name(schema_identifier) if schema_identifier else default_schema
    if not isinstance(schema, str) or not _IDENTIFIER.fullmatch(schema):
        raise DatasetSqlError("The Dataset requires an explicit source schema.")
    table_name = source_name(table.this)
    if isinstance(base.expressions[0], exp.Star):
        columns = _wildcard_catalog(cursor, schema, table_name, policy)
        if fields is None:
            return list(columns)
        _check_field_names(fields)
        referenced = set()
        for spec in fields.values():
            expression, _ = _compile_field(spec, columns)
            referenced.update(column.name for column in expression.find_all(exp.Column))
        if referenced:
            columns = {name: columns[name] for name in sorted(referenced)}
        base.set("expressions", [exp.column(name, quoted=True) for name in columns])
        table.set("db", exp.to_identifier(schema, quoted=True))
        return _compile_projection(base, sql_dialect, columns, fields, report=report, legacy_avg=legacy_avg)
    aliases = {}
    projections = [_output_name(projection, sql_dialect) for projection in base.expressions]
    _check_field_names(dict.fromkeys(projections))
    if len(set(projections)) != len(projections):
        raise DatasetSqlError("Base output aliases must be unique.")
    for output, projection in zip(projections, base.expressions):
        column = projection.this if isinstance(projection, exp.Alias) else projection
        aliases[output] = source_name(column.this)

    def lookup_key(name):
        return name if dialect == "postgres" else name.lower()

    names = sorted({lookup_key(name) for name in aliases.values()})
    column_filter = "column_name" if dialect == "postgres" else "LOWER(column_name)"
    table_filter = ("table_schema = %s AND table_name = %s" if dialect == "postgres"
                    else "BINARY table_schema = BINARY %s AND BINARY table_name = BINARY %s")
    placeholders = ", ".join(["%s"] * len(names))
    cursor.execute(
        "SELECT column_name, data_type, numeric_precision, numeric_scale "
        f"FROM information_schema.columns WHERE {table_filter} "
        f"AND {column_filter} IN ({placeholders})",
        (schema, table_name, *names),
    )
    catalog = {lookup_key(name): (kind, precision, scale) for name, kind, precision, scale in cursor.fetchall()}
    if set(catalog) != set(names):
        raise DatasetSqlError("One or more Dataset columns are missing from the source catalog.")
    columns = {output: _catalog_type(*catalog[lookup_key(name)]) for output, name in aliases.items()}
    table.set("db", exp.to_identifier(schema, quoted=True))
    return _compile_projection(base, sql_dialect, columns, fields, report=report, legacy_avg=legacy_avg)


def _wildcard_catalog(cursor, schema, table, policy):
    allowed = None
    if not policy.get("unrestricted"):
        allowed = {column for rule in policy.get("rules", ())
                   if (rule["schema"], rule["table"]) == (schema, table) for column in rule["columns"]}
    cursor.execute(
        "SELECT column_name, data_type, numeric_precision, numeric_scale FROM information_schema.columns "
        "WHERE BINARY table_schema = BINARY %s AND BINARY table_name = BINARY %s "
        "AND LOCATE('INVISIBLE', UPPER(extra)) = 0 ORDER BY ordinal_position",
        (schema, table),
    )
    columns = {}
    for name, kind, precision, scale in cursor.fetchall():
        if (allowed is not None and name not in allowed) or not _IDENTIFIER.fullmatch(name):
            continue
        try:
            columns[name] = _catalog_type(kind, precision, scale)
        except DatasetSqlError:
            continue
    _check_field_names(columns)
    return columns


def _join_parts(base, join):
    left, right = base.args["from_"].this, join.this
    condition = join.args.get("on")
    if (join.side not in ("", "LEFT") or join.kind not in ("", "INNER", "OUTER")
            or join.kind == "OUTER" and join.side != "LEFT"
            or not isinstance(left, exp.Table) or not isinstance(right, exp.Table)
            or left.alias_or_name == right.alias_or_name or not isinstance(condition, exp.EQ)
            or not isinstance(condition.this, exp.Column) or not isinstance(condition.expression, exp.Column)):
        raise DatasetSqlError("Use INNER or LEFT JOIN with one qualified equality key.")
    keys = {column.table: column.name for column in (condition.this, condition.expression)}
    if set(keys) != {left.alias_or_name, right.alias_or_name}:
        raise DatasetSqlError("The join must compare one qualified key from each table.")
    return left, right, keys[left.alias_or_name], keys[right.alias_or_name]


def _joined_catalog(cursor, base, default_schema):
    left, right, left_key, right_key = _join_parts(base, base.args["joins"][0])
    tables = {table.alias_or_name: (table.db or default_schema, table.name) for table in (left, right)}
    for schema, table in tables.values():
        if not isinstance(schema, str) or not _IDENTIFIER.fullmatch(schema) or not table:
            raise DatasetSqlError("Dataset joins need explicit source table identities.")
    key_types = []
    for node, key in ((left, left_key), (right, right_key)):
        cursor.execute(
            "SELECT column_type, character_set_name, collation_name FROM information_schema.columns "
            "WHERE BINARY table_schema = BINARY %s AND BINARY table_name = BINARY %s AND LOWER(column_name) = LOWER(%s)",
            (*tables[node.alias_or_name], key),
        )
        key_types.append(cursor.fetchall())
    if len(key_types[0]) != 1 or key_types[0] != key_types[1]:
        raise DatasetSqlError("Dataset join keys must have matching types and collations.")
    schema, table = tables[right.alias_or_name]
    cursor.execute(
        "SELECT index_name FROM information_schema.statistics WHERE BINARY table_schema = BINARY %s "
        "AND BINARY table_name = BINARY %s AND non_unique = 0 GROUP BY index_name "
        "HAVING COUNT(*) = 1 AND MAX(LOWER(column_name)) = LOWER(%s) AND MAX(sub_part) IS NULL",
        (schema, table, right_key),
    )
    if not cursor.fetchall():
        raise DatasetSqlError("The right-hand join key must have a single-column unique index to prevent duplicated rows.")
    columns = {}
    for projection in base.expressions:
        output = _output_name(projection, "mysql")
        column = projection.this if isinstance(projection, exp.Alias) else projection
        if column.table not in tables or output in columns:
            raise DatasetSqlError("Qualify every join output column and give it a unique name.")
        schema, table = tables[column.table]
        cursor.execute(
            "SELECT data_type, numeric_precision, numeric_scale FROM information_schema.columns "
            "WHERE BINARY table_schema = BINARY %s AND BINARY table_name = BINARY %s AND LOWER(column_name) = LOWER(%s)",
            (schema, table, column.name),
        )
        metadata = cursor.fetchall()
        if len(metadata) != 1:
            raise DatasetSqlError("A Dataset join column is missing from the source catalog.")
        columns[output] = _catalog_type(*metadata[0])
    _check_field_names(columns)
    for node in (left, right):
        node.set("db", exp.to_identifier(tables[node.alias_or_name][0], quoted=True))
    return columns


def _report_column(field, field_types):
    if not isinstance(field, str) or field not in field_types:
        raise DatasetSqlError("The report refers to an unknown Dataset field.")
    return exp.column(field, table="_report", quoted=True), field_types[field]


def _filter_condition(item, field_types):
    if not isinstance(item, dict) or set(item) != {"field", "value"}:
        raise DatasetSqlError("Provide a field and value for each report filter.")
    column, kind = _report_column(item["field"], field_types)
    value = item["value"]
    if value is None:
        return exp.Is(this=column, expression=exp.Null())
    if kind == "boolean" and type(value) is bool:
        literal = exp.Boolean(this=value)
    elif kind in _NUMERIC_TYPES and isinstance(value, str) and _DECIMAL.fullmatch(value):
        if kind == "integer" and "." in value:
            raise DatasetSqlError("The selected report field requires an integer filter.")
        literal = _cast_number(exp.Literal.number(value))
    elif (kind in {"date", "datetime", "text"} and isinstance(value, str) and len(value) <= 256
          and "{{" not in value and "}}" not in value and "\x00" not in value):
        if kind != "text":
            try:
                if kind == "date":
                    value = date.fromisoformat(value).isoformat()
                else:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo is not None:
                        raise ValueError("Time zone is unsupported.")
                    value = parsed.isoformat(sep=" ")
            except ValueError as error:
                raise DatasetSqlError("The selected report field requires an ISO date or time.") from error
        literal = exp.Literal.string(value)
    else:
        raise DatasetSqlError("The filter value does not match its Dataset field type.")
    return exp.EQ(this=column, expression=literal)


def _report_query(dataset, field_types, report, *, legacy_avg=False):
    if not isinstance(report, dict) or set(report) != {"group_by", "metrics", "filters"}:
        raise DatasetSqlError("Provide grouping, metrics and filters for the report.")
    groups, metrics, filters = report["group_by"], report["metrics"], report["filters"]
    if (not isinstance(groups, list) or len(groups) > 4 or not isinstance(metrics, dict)
            or len(metrics) > 8 or not isinstance(filters, list) or len(filters) > 8):
        raise DatasetSqlError("Use up to four fields, eight metrics and eight filters.")
    group_columns = [_report_column(field, field_types)[0] for field in groups]
    names = [*groups, *metrics]
    _check_field_names(dict.fromkeys(names))
    if len(set(names)) != len(names):
        raise DatasetSqlError("Report output names must be unique.")
    projections = [column.copy() for column in group_columns]
    for name, metric in sorted(metrics.items()):
        if not isinstance(metric, dict) or set(metric) != {"field", "aggregation"}:
            raise DatasetSqlError("Provide a field and aggregation for each metric.")
        column, kind = _report_column(metric["field"], field_types)
        aggregation = metric["aggregation"]
        if aggregation == "count":
            expression = exp.Count(this=column)
        elif aggregation in ("sum", "avg") and kind in _NUMERIC_TYPES:
            widened = aggregation == "avg" and not legacy_avg
            total = _cast_number(exp.Sum(this=_cast_number(column.copy())), 55 if widened else 38)
            expression = total if aggregation == "sum" else _cast_number(
                _divide(total, _cast_number(exp.Count(this=column)), numerator_precision=65 if widened else 48)
            )
        else:
            raise DatasetSqlError("Use COUNT, or use SUM or AVG with a numeric field.")
        projections.append(exp.alias_(expression, name, quoted=True))
    query = exp.select(*projections).from_(dataset.subquery("_report"))
    if group_columns and metrics:
        query = query.group_by(*group_columns)
    if filters:
        query = query.where(*[_filter_condition(item, field_types) for item in filters])
    return query


def _catalog_type(kind, precision, scale):
    if kind in {"tinyint", "smallint", "mediumint", "int", "integer", "bigint"}:
        return "integer"
    if kind in {"decimal", "numeric"}:
        if (type(precision) is not int or type(scale) is not int
                or not 0 <= scale <= min(10, precision) or not 1 <= precision <= scale + 28):
            raise DatasetSqlError("Source decimals must fit within DECIMAL(38,10) without rounding.")
        return "decimal"
    if kind in {"char", "varchar", "character", "character varying", "text", "tinytext", "mediumtext", "longtext"}:
        return "text"
    if kind == "date":
        return "date"
    if kind in {"datetime", "timestamp", "timestamp without time zone"}:
        return "datetime"
    if kind == "boolean":
        return "boolean"
    raise DatasetSqlError("The source column type is unsupported for Datasets.")
