import re
from functools import partial
from numbers import Number

import pystache
from dateutil.parser import parse
from funcy import distinct

from redash.utils import mustache_render


def _pluck_name_and_value(default_column, row):
    row = {k.lower(): v for k, v in row.items()}
    name_column = "name" if "name" in row.keys() else default_column.lower()
    value_column = "value" if "value" in row.keys() else default_column.lower()

    return {"name": row[name_column], "value": str(row[value_column])}


def _load_result(query_id, org, actor=None):
    from redash import models

    query = models.Query.get_by_id_and_org(query_id, org)

    from flask import has_request_context
    from flask_login import current_user
    from redash.permissions import has_access, view_only
    from redash.data_access_runtime import result_access_allowed
    from redash.reader_reports import result_dashboard_allowed
    actor = actor if actor is not None else (current_user if has_request_context() else None)
    if (actor is None or not actor.is_authenticated or actor.is_disabled or actor.is_api_user()
            or actor.org_id != org.id or not has_access(query, actor, view_only)):
        raise InvalidParameterError("This dropdown query is outside your assigned report access.")

    from redash.data_access_runtime import is_protected, can_read_all_scopes
    if query.data_source is not None and is_protected(query.data_source):
        if can_read_all_scopes(actor):
            from redash.permissions import require_access, view_only
            require_access(query.data_source, actor, view_only)
            text = query.data_source.query_runner.apply_auto_limit(query.query_text, query.options.get("apply_auto_limit", False))
            result = models.QueryResult.get_latest(query.data_source, text, -1, any_policy_scope=True)
            if result is None:
                raise InvalidParameterError("No saved dropdown result is available.")
            if (not result_access_allowed(result, actor)
                    or not result_dashboard_allowed(result, actor)):
                raise InvalidParameterError("This dropdown result is outside your assigned report access.")
            return result.data
        raise InvalidParameterError("Protected sources do not support query dropdown dependencies.")

    if query.data_source:
        query_result = models.QueryResult.get_by_id_and_org(query.latest_query_data_id, org)
        if (not result_access_allowed(query_result, actor)
                or not result_dashboard_allowed(query_result, actor)):
            raise InvalidParameterError("This dropdown result is outside your assigned report access.")
        return query_result.data
    else:
        raise QueryDetachedFromDataSourceError(query_id)


def dropdown_values(query_id, org, actor=None):
    data = _load_result(query_id, org, actor=actor)
    first_column = data["columns"][0]["name"]
    pluck = partial(_pluck_name_and_value, first_column)
    return list(map(pluck, data["rows"]))


def join_parameter_list_values(parameters, schema):
    updated_parameters = {}
    for key, value in parameters.items():
        if isinstance(value, list):
            definition = next((definition for definition in schema if definition["name"] == key), {})
            multi_values_options = definition.get("multiValuesOptions", {})
            separator = str(multi_values_options.get("separator", ","))
            prefix = str(multi_values_options.get("prefix", ""))
            suffix = str(multi_values_options.get("suffix", ""))
            updated_parameters[key] = separator.join([prefix + v + suffix for v in value])
        else:
            updated_parameters[key] = value
    return updated_parameters


def _collect_key_names(nodes):
    keys = []
    for node in nodes._parse_tree:
        if isinstance(node, (pystache.parser._EscapeNode, pystache.parser._LiteralNode)):
            keys.append(node.key)
        elif isinstance(node, pystache.parser._SectionNode):
            keys.append(node.key)
            keys.extend(_collect_key_names(node.parsed))
        elif isinstance(node, pystache.parser._InvertedNode):
            keys.append(node.key)
            keys.extend(_collect_key_names(node.parsed_section))

    return distinct(keys)


def _collect_query_parameters(query):
    nodes = pystache.parse(query)
    keys = _collect_key_names(nodes)
    return keys


def _parameter_names(parameter_values):
    names = []
    for key, value in parameter_values.items():
        if isinstance(value, dict):
            for inner_key in value.keys():
                names.append("{}.{}".format(key, inner_key))
        else:
            names.append(key)

    return names


def _is_number(string):
    if isinstance(string, Number):
        return True
    else:
        float(string)
        return True


def _is_regex_pattern(value, regex):
    try:
        if re.compile(regex).fullmatch(value):
            return True
        else:
            return False
    except re.error:
        return False


def _is_date(string):
    parse(string)
    return True


def _is_date_range(obj):
    return _is_date(obj["start"]) and _is_date(obj["end"])


def _is_value_within_options(value, dropdown_options, allow_list=False):
    if isinstance(value, list):
        return allow_list and set(map(str, value)).issubset(set(dropdown_options))
    return str(value) in dropdown_options


class ParameterizedQuery:
    def __init__(self, template, schema=None, org=None, actor=None):
        self.schema = schema or []
        self.org = org
        self.actor = actor
        self.template = template
        self.query = template
        self.parameters = {}

    def apply(self, parameters):
        invalid_parameter_names = [key for (key, value) in parameters.items() if not self._valid(key, value)]
        if invalid_parameter_names:
            raise InvalidParameterError(invalid_parameter_names)
        else:
            self.parameters.update(parameters)
            self.query = mustache_render(self.template, join_parameter_list_values(parameters, self.schema))

        return self

    def _valid(self, name, value):
        if not self.schema:
            return True

        definition = next(
            (definition for definition in self.schema if definition["name"] == name),
            None,
        )

        if not definition:
            return False

        enum_options = definition.get("enumOptions")
        query_id = definition.get("queryId")
        regex = definition.get("regex")
        allow_multiple_values = isinstance(definition.get("multiValuesOptions"), dict)

        if isinstance(enum_options, str):
            enum_options = enum_options.split("\n")

        validators = {
            "text": lambda value: isinstance(value, str),
            "text-pattern": lambda value: _is_regex_pattern(value, regex),
            "number": _is_number,
            "enum": lambda value: _is_value_within_options(value, enum_options, allow_multiple_values),
            "query": lambda value: _is_value_within_options(
                value,
                [v["value"] for v in dropdown_values(query_id, self.org, actor=self.actor)],
                allow_multiple_values,
            ),
            "date": _is_date,
            "datetime-local": _is_date,
            "datetime-with-seconds": _is_date,
            "date-range": _is_date_range,
            "datetime-range": _is_date_range,
            "datetime-range-with-seconds": _is_date_range,
        }

        validate = validators.get(definition["type"], lambda x: False)

        try:
            # multiple error types can be raised here; but we want to convert
            # all except QueryDetached to InvalidParameterError in `apply`
            return validate(value)
        except QueryDetachedFromDataSourceError:
            raise
        except Exception:
            return False

    @property
    def is_safe(self):
        # Free-text parameter types are substituted into the query verbatim.
        if any(param["type"] in ("text", "text-pattern") for param in self.schema):
            return False

        # Placeholders that are not declared in the schema cannot be validated, so
        # any value would be substituted verbatim as well.
        declared = {param["name"] for param in self.schema}
        for param in self.schema:
            if param["type"] in ("date-range", "datetime-range", "datetime-range-with-seconds"):
                # Range values are validated as a single object but rendered via
                # these two fields. Other nested fields are not validated.
                declared.update("{}.{}".format(param["name"], field) for field in ("start", "end"))
        undeclared = set(_collect_query_parameters(self.template)) - declared
        return not undeclared

    @property
    def missing_params(self):
        query_parameters = set(_collect_query_parameters(self.template))
        return set(query_parameters) - set(_parameter_names(self.parameters))

    @property
    def text(self):
        return self.query


class InvalidParameterError(Exception):
    def __init__(self, parameters):
        parameter_names = ", ".join(parameters)
        message = "The following parameter values are incompatible with their definitions: {}".format(parameter_names)
        super(InvalidParameterError, self).__init__(message)


class QueryDetachedFromDataSourceError(Exception):
    def __init__(self, query_id):
        self.query_id = query_id
        super(QueryDetachedFromDataSourceError, self).__init__(
            "This query is detached from any data source. Please select a different query."
        )
