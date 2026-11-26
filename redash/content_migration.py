"""Portable organization content. Caller authenticates, encrypts and commits.

Inspection never mutates metadata. Imports use a savepoint; the caller includes
its replay receipt in the outer transaction. No SQL/provider/mail execution.
"""
import copy
import uuid
from datetime import datetime, timezone

from sqlalchemy import insert, update
from redash import models, settings
from redash.access_policy import EDITOR_PERMISSIONS, policy_for, validate_policy, validate_limits
from redash.query_revisions import allowed_snapshot, SNAPSHOT_FIELDS
from redash.query_runner import BaseQueryRunner, get_configuration_schema_for_query_runner_type
from redash.models.parameterized_query import join_parameter_list_values
from redash.utils import mustache_render
from redash.utils.configuration import ConfigurationContainer

COMPONENTS = frozenset(('queries', 'dashboards', 'sources', 'users', 'groups', 'rules', 'policies', 'history', 'ai'))
TABLES = ('sources', 'source_refs', 'groups', 'users', 'queries', 'visualizations', 'dashboards', 'widgets', 'history', 'source_groups', 'grants')
MAX_OBJECTS = 20000
UNSUPPORTED_SOURCES = frozenset(('results', 'query_results', 'python', 'python3', 'json', 'csv', 'excel', 'url'))
_SECRET_WORDS = ('password', 'passwd', 'secret', 'token', 'privatekey', 'apikey', 'credential', 'certificate', 'authorization', 'accesskey')


def _fail(message='Invalid migration package.'):
    raise ValueError(message)


def _dict(value):
    if not isinstance(value, dict): _fail()
    return value


def _list(value, maximum=MAX_OBJECTS):
    if not isinstance(value, list) or len(value) > maximum: _fail('Migration list is invalid or too large.')
    return value


def _text(value, maximum=1000000, empty=True):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()) or '\x00' in value:
        _fail('Invalid migration text field.')
    try:
        value.encode('utf-8')
    except UnicodeError:
        _fail('Invalid migration text encoding.')
    return value


def _id(value):
    if type(value) is not int or not 1 <= value <= 2147483647: _fail('Invalid migration reference.')
    return value


def _nullable_text(value, maximum=1000000):
    if value is not None:
        _text(value, maximum)


def _definition_hash(definition, source):
    """Match execution hashing without loading dropdown results or executing SQL.

    Default values are only rendered here; runtime authorization and parameter
    validation still run normally before a query can execute.
    """
    options = definition['options']
    parameters = options.get('parameters', [])
    values = {parameter['name']: parameter.get('value') for parameter in parameters}
    query = definition['query']
    if values:
        try:
            query = mustache_render(query, join_parameter_list_values(values, parameters))
        except (TypeError, ValueError):
            _fail('Invalid parameter defaults in migration query.')
    runner = source.query_runner if source is not None else BaseQueryRunner({})
    return runner.gen_query_hash(query, options.get('apply_auto_limit', False))


def _embedded_id(value):
    if type(value) is int:
        return _id(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return _id(int(value))
    _fail('Invalid embedded migration reference.')


def _json(value, depth=0):
    if depth > 25: _fail('Migration data is nested too deeply.')
    if isinstance(value, dict):
        if len(value) > 5000: _fail()
        for key, item in value.items():
            _text(key, 1000); _json(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_OBJECTS: _fail()
        for item in value: _json(item, depth + 1)
    elif isinstance(value, str): _text(value)
    elif value is not None and not isinstance(value, (bool, int, float)): _fail()
    elif isinstance(value, float) and (value != value or abs(value) == float('inf')): _fail()


def normalize_components(components):
    if not isinstance(components, list) or not components or any(not isinstance(c, str) or c not in COMPONENTS for c in components):
        _fail('Select at least one supported migration component.')
    selected = set(components)
    if 'dashboards' in selected or 'history' in selected: selected.add('queries')
    if 'users' in selected or 'rules' in selected: selected.add('groups')
    if 'rules' in selected: selected.add('sources')
    return sorted(selected)


def _legacy_rules(org):
    return ('data_access_group_rules' not in org.settings and any(org.settings.get(key)
        for key in ('data_access_protected_sources', 'data_access_internal_sources', 'active_data_access')))


def _safe_source_options(source, credentials):
    schema = get_configuration_schema_for_query_runner_type(source.type)
    if not isinstance(schema, dict) or source.type in UNSUPPORTED_SOURCES:
        _fail('A source uses an unsupported or derived connector. Resolve its dependencies before migration.')
    raw = copy.deepcopy(source.options.to_dict())
    if credentials: return raw
    secret_keys = {k.lower() for k in schema.get('secret', [])}
    omitted = object()
    def clean(value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                compact = ''.join(c for c in key.lower() if c.isalnum())
                if key.lower() in secret_keys or compact in ('connectionstring', 'connection', 'uri', 'dsn') or any(word in compact for word in _SECRET_WORDS):
                    continue
                item = clean(item)
                if item is not omitted: result[key] = item
            return result
        if isinstance(value, list): return [item for item in (clean(v) for v in value) if item is not omitted]
        if isinstance(value, str) and (('://' in value and ('@' in value or '?' in value))
                or value.strip().lower().startswith(('bearer ', 'basic ', 'authorization:', 'x-api-key:', 'api-key:'))):
            return omitted
        return value
    return clean(raw)


def _safe_query(query):
    definition = allowed_snapshot({'name': query.name, 'description': query.description, 'query': query.query_text,
        'data_source_id': query.data_source_id, 'options': query.options})
    return {'id': query.id, **definition, 'tags': list(query.tags or []), 'is_draft': bool(query.is_draft)}


def export_content(org, user, components, include_credentials=False):
    selected = normalize_components(components)
    if type(include_credentials) is not bool: _fail('Credential selection must be boolean.')
    package = {'format': 'bi-dash-content', 'version': 1, 'package_id': str(uuid.uuid4()),
        'created_at': datetime.now(timezone.utc).isoformat(), 'components': selected, 'credential_material': include_credentials}
    package.update({name: [] for name in TABLES}); package.update({'rules': None, 'policies': None, 'ai': None})
    queries = list(models.Query.query.filter_by(org_id=org.id, is_archived=False).order_by(models.Query.id)) if 'queries' in selected else []
    query_map = {q.id: q for q in queries}; frontier = list(queries)
    while frontier:
        query = frontier.pop()
        for parameter in (query.options or {}).get('parameters', []):
            if isinstance(parameter, dict) and parameter.get('type') == 'query':
                dependency = _embedded_id(parameter.get('queryId'))
                if dependency not in query_map:
                    target = models.Query.query.filter_by(id=dependency, org_id=org.id).first()
                    if target is None: _fail('A query dropdown dependency is unavailable in this organization.')
                    query_map[target.id] = target; frontier.append(target)
        if len(query_map) > MAX_OBJECTS: _fail('Too many query dependencies.')
    queries = sorted(query_map.values(), key=lambda q: q.id)
    for query in queries:
        if (query.options or {}).get("dataset_report") is True:
            _fail('Dataset-backed Queries cannot be exported until their Dataset dependencies can be remapped.')
        row = _safe_query(query); row['owner_id'] = query.user_id if 'users' in selected else None
        package['queries'].append(row)
        for vis in query.visualizations:
            package['visualizations'].append({'id': vis.id, 'query_id': query.id, 'type': vis.type, 'name': vis.name,
                'description': vis.description, 'options': copy.deepcopy(vis.options or {})})
    if 'dashboards' in selected:
        for board in models.Dashboard.query.filter_by(org_id=org.id, is_archived=False).order_by(models.Dashboard.id):
            package['dashboards'].append({'id': board.id, 'name': board.name, 'owner_id': board.user_id if 'users' in selected else None,
                'layout': copy.deepcopy(board.layout or []), 'options': copy.deepcopy(board.options or {}), 'tags': list(board.tags or []),
                'is_draft': bool(board.is_draft), 'dashboard_filters_enabled': bool(board.dashboard_filters_enabled)})
            for widget in board.widgets:
                if widget.visualization is not None and widget.visualization.query_id not in query_map:
                    _fail('A dashboard contains an archived query dependency. Unarchive it before exporting the dashboard.')
                package['widgets'].append({'id': widget.id, 'dashboard_id': board.id, 'visualization_id': widget.visualization_id,
                    'text': widget.text, 'width': widget.width, 'options': copy.deepcopy(widget.options or {})})
            if 'users' in selected:
                for grant in models.DashboardReadGrant.query.filter_by(dashboard_id=board.id):
                    package['grants'].append({'dashboard_id': board.id, 'user_id': grant.user_id, 'group_id': None})
            if 'groups' in selected:
                for grant in models.DashboardGroupReadGrant.query.filter_by(dashboard_id=board.id):
                    package['grants'].append({'dashboard_id': board.id, 'user_id': None, 'group_id': grant.group_id})
    if 'groups' in selected:
        for group in models.Group.query.filter_by(org_id=org.id).order_by(models.Group.id):
            package['groups'].append({'id': group.id, 'name': group.name, 'permissions': sorted(set(group.permissions or []) & EDITOR_PERMISSIONS)})
    if 'users' in selected:
        for account in models.User.query.filter_by(org_id=org.id).order_by(models.User.id):
            package['users'].append({'id': account.id, 'name': account.name, 'email': account.email, 'role': account.role,
                'groups': list(account.group_ids or []), 'limits': copy.deepcopy((account.details or {}).get('access_limits', {}))})
    if 'history' in selected:
        for revision in models.QueryRevision.query.filter(models.QueryRevision.org_id == org.id,
                models.QueryRevision.query_id.in_(list(query_map))).order_by(models.QueryRevision.query_id, models.QueryRevision.revision):
            package['history'].append({'query_id': revision.query_id, 'revision': revision.revision,
                'actor_id': revision.actor_user_id if 'users' in selected else None, 'created_at': revision.created_at.isoformat(),
                'kind': revision.kind, 'snapshot': allowed_snapshot(revision.snapshot), 'changed_fields': [k for k in revision.changed_fields if k in SNAPSHOT_FIELDS]})
    if 'rules' in selected:
        from redash.data_access import validate_group_configuration
        if _legacy_rules(org): _fail('The origin uses legacy restrictions. Migrate its rules configuration before exporting Rules.')
        package['rules'] = validate_group_configuration(org.settings.get('data_access_group_rules', {'revision': 0, 'policies': []}))
    needed_sources = {q.data_source_id for q in queries if q.data_source_id is not None}
    needed_sources.update(r['snapshot']['data_source_id'] for r in package['history'] if r['snapshot']['data_source_id'] is not None)
    sources = list(models.DataSource.query.filter_by(org_id=org.id).order_by(models.DataSource.id)) if 'sources' in selected else list(
        models.DataSource.query.filter(models.DataSource.org_id == org.id, models.DataSource.id.in_(needed_sources)))
    if not needed_sources.issubset({source.id for source in sources}):
        _fail('Some query history sources no longer exist. Export without history or resolve those sources.')
    from redash.data_access_runtime import is_internal
    for source in sources:
        if is_internal(source): _fail('Retired private data sources cannot be migrated.')
        options = _safe_source_options(source, include_credentials)
        package['source_refs'].append({'id': source.id, 'name': source.name, 'type': source.type})
        if 'sources' in selected: package['sources'].append({'id': source.id, 'name': source.name, 'type': source.type, 'options': options})
    if 'rules' in selected:
        # Rules is the explicit permission-transfer component. Restore ordinary
        # source grants as well as column-restricted grants, but only between
        # newly imported sources and newly cloned groups (never target builtins).
        source_set = {source['id'] for source in package['sources']}
        group_set = {group['id'] for group in package['groups']}
        for binding in models.DataSourceGroup.query.join(models.DataSource).filter(models.DataSource.org_id == org.id):
            if binding.data_source_id in source_set and binding.group_id in group_set:
                package['source_groups'].append({'source_id': binding.data_source_id, 'group_id': binding.group_id, 'view_only': bool(binding.view_only)})
    if 'policies' in selected: package['policies'] = validate_policy(policy_for(org))
    if 'ai' in selected:
        from redash.ai_settings import secret_cipher
        stored = org.settings.get('ai_configuration', {}); profiles = {}
        for provider, profile in stored.get('profiles', {}).items():
            profiles[provider] = {'model': profile.get('model', ''), 'base_url': profile.get('base_url', ''), 'api_key': ''}
            if include_credentials and profile.get('encrypted_api_key'):
                try: profiles[provider]['api_key'] = secret_cipher(settings.DATASOURCE_SECRET_KEY).decrypt(profile['encrypted_api_key'].encode()).decode()
                except Exception: _fail('A saved AI key cannot be decrypted. Save it again before exporting credentials.')
        package['ai'] = {'provider': stored.get('provider', 'ollama'), 'profiles': profiles, 'context': org.settings.get('ai_context', '')}
    _validate(package)
    return package


def _records(package, key):
    result = {}
    for row in _list(package.get(key, [])):
        _dict(row); ident = _id(row.get('id'))
        if ident in result: _fail('Duplicate migration references.')
        result[ident] = row
    return result


def _ref(value, ids, nullable=False):
    if value is None and nullable: return
    if _id(value) not in ids: _fail('A migration dependency is missing.')


def _query_options(options, queries):
    _dict(options)
    if set(options) - {'parameters', 'apply_auto_limit'}: _fail('Unsupported query options in migration package.')
    if type(options.get('apply_auto_limit', False)) is not bool: _fail()
    for param in _list(options.get('parameters', []), 1000):
        _dict(param); _text(param.get('name'), 255, False); _text(param.get('type'), 60, False)
        if param.get('type') == 'query':
            target = _embedded_id(param.get('queryId'))
            _ref(target, queries)


def _refs_in_options(value, maps):
    if isinstance(value, list): return [_refs_in_options(v, maps) for v in value]
    if not isinstance(value, dict): return copy.deepcopy(value)
    result = {}
    keys = {'queryId': 'queries', 'query_id': 'queries', 'visualizationId': 'visualizations', 'visualization_id': 'visualizations',
            'widgetId': 'widgets', 'widget_id': 'widgets'}
    for key, item in value.items():
        if key in keys and item is not None:
            old = _embedded_id(item)
            if old not in maps[keys[key]]: _fail('An embedded migration dependency is missing.')
            result[key] = maps[keys[key]][old]
        else: result[key] = _refs_in_options(item, maps)
    return result


def _validate(package):
    _dict(package); _json(package)
    allowed = set(TABLES) | {'format', 'version', 'package_id', 'created_at', 'components', 'credential_material', 'rules', 'policies', 'ai'}
    if set(package) - allowed or package.get('format') != 'bi-dash-content' or type(package.get('version')) is not int or package.get('version') != 1:
        _fail('Unsupported migration package format or version.')
    try:
        if str(uuid.UUID(package.get('package_id'))) != package.get('package_id'): _fail()
    except (ValueError, TypeError, AttributeError): _fail('Invalid migration package identifier.')
    selected = normalize_components(package.get('components'))
    if selected != sorted(set(package['components'])) or type(package.get('credential_material')) is not bool:
        _fail('Migration component dependencies are inconsistent.')
    for key in TABLES: _list(package.get(key))
    if sum(len(package[key]) for key in TABLES) > MAX_OBJECTS: _fail('Too many migration objects.')
    indexes = {key: _records(package, key) for key in ('sources', 'source_refs', 'groups', 'users', 'queries', 'visualizations', 'dashboards', 'widgets')}
    for key, component in (('sources','sources'),('groups','groups'),('users','users'),('queries','queries'),('visualizations','queries'),('dashboards','dashboards'),('widgets','dashboards'),('history','history')):
        if component not in selected and package[key]: _fail('Unselected migration component contains records.')
    if 'sources' in selected and set(indexes['sources']) != set(indexes['source_refs']): _fail('Source definitions and references differ.')
    for source in indexes['source_refs'].values():
        _text(source.get('name'),255,False); _text(source.get('type'),255,False)
        if source['type'] in UNSUPPORTED_SOURCES or not isinstance(get_configuration_schema_for_query_runner_type(source['type']),dict):
            _fail('A package source requires an unavailable or unsupported connector.')
    for source in indexes['sources'].values():
        descriptor = indexes['source_refs'][source['id']]
        if source.get('type') != descriptor['type'] or source.get('name') != descriptor['name']: _fail('Source descriptor mismatch.')
        _dict(source.get('options'))
    for group in indexes['groups'].values():
        _text(group.get('name'),100,False)
        if any(not isinstance(p,str) or p not in EDITOR_PERMISSIONS for p in _list(group.get('permissions'),100)):
            _fail('Unsupported or elevated group permissions in package.')
    seen_emails=set()
    for account in indexes['users'].values():
        _text(account.get('name'),320,False); email=_text(account.get('email'),320,False).lower()
        if '@' not in email or email in seen_emails or account.get('role') not in ('admin','editor','reader'): _fail('Invalid or duplicate imported account.')
        seen_emails.add(email)
        for gid in _list(account.get('groups'),1000): _ref(gid,indexes['groups'])
        validate_limits(account.get('limits',{}))
    for query in indexes['queries'].values():
        _text(query.get('name'),255,False); _text(query.get('query')); _nullable_text(query.get('description'),4096)
        _ref(query.get('data_source_id'),indexes['source_refs'],True)
        if 'users' in selected: _ref(query.get('owner_id'),indexes['users'])
        if type(query.get('is_draft')) is not bool: _fail()
        for tag in _list(query.get('tags'),1000): _text(tag,255)
        _query_options(query.get('options'),indexes['queries'])
    dummy={k:{old:old for old in indexes[k]} for k in ('queries','visualizations','widgets')}
    for vis in indexes['visualizations'].values():
        _ref(vis.get('query_id'),indexes['queries']); _text(vis.get('name'),255); _text(vis.get('type'),100,False)
        _nullable_text(vis.get('description'),4096); _dict(vis.get('options')); _refs_in_options(vis['options'],dummy)
    for board in indexes['dashboards'].values():
        _text(board.get('name'),100,False)
        if 'users' in selected: _ref(board.get('owner_id'),indexes['users'])
        if type(board.get('is_draft')) is not bool or type(board.get('dashboard_filters_enabled')) is not bool: _fail()
        _dict(board.get('options')); _refs_in_options(board['options'],dummy)
        for tag in _list(board.get('tags'),1000): _text(tag,255)
        for line in _list(board.get('layout')):
            for wid in _list(line):
                _ref(wid,indexes['widgets'])
                if indexes['widgets'][wid].get('dashboard_id')!=board['id']: _fail('Dashboard layout references another dashboard widget.')
    for widget in indexes['widgets'].values():
        _ref(widget.get('dashboard_id'),indexes['dashboards']); _ref(widget.get('visualization_id'),indexes['visualizations'],True)
        _nullable_text(widget.get('text'))
        if type(widget.get('width')) is not int or not 1<=widget['width']<=100: _fail('Invalid widget width.')
        _dict(widget.get('options')); _refs_in_options(widget['options'],dummy)
    previous={}
    for row in package['history']:
        _dict(row); _ref(row.get('query_id'),indexes['queries']); seq=_id(row.get('revision'))
        if seq == 2147483647: _fail('History revision leaves no room for an imported baseline.')
        if seq <= previous.get(row['query_id'],0): _fail('History revisions must be ordered and unique.')
        previous[row['query_id']]=seq
        if row.get('kind') not in ('created','baseline','update'): _fail()
        try:
            date=datetime.fromisoformat(row.get('created_at'))
            if date.tzinfo is None: _fail()
        except (ValueError,TypeError): _fail('Invalid history timestamp.')
        snapshot=_dict(row.get('snapshot'))
        if snapshot!=allowed_snapshot(snapshot): _fail('Unsupported history snapshot fields.')
        _text(snapshot.get('name'),255,False); _text(snapshot.get('query')); _nullable_text(snapshot.get('description'),4096)
        _ref(snapshot.get('data_source_id'),indexes['source_refs'],True); _query_options(snapshot['options'],indexes['queries'])
        if row.get('actor_id') is not None: _ref(row['actor_id'],indexes['users'])
        if any(field not in SNAPSHOT_FIELDS for field in _list(row.get('changed_fields'),10)): _fail()
    if 'rules' in selected:
        from redash.data_access import validate_group_configuration
        config=validate_group_configuration(package.get('rules'))
        for policy in config['policies']:
            _ref(policy['data_source_id'],indexes['sources'])
            for gid in policy['group_ids']: _ref(gid,indexes['groups'])
    elif package.get('rules') is not None or package['source_groups']: _fail('Rules were not selected.')
    binding_keys=set()
    for binding in package['source_groups']:
        _dict(binding); pair=(binding.get('source_id'),binding.get('group_id'))
        _ref(binding.get('source_id'), indexes['sources']); _ref(binding.get('group_id'), indexes['groups'])
        if 'rules' not in selected or pair in binding_keys or type(binding.get('view_only')) is not bool: _fail('Invalid source permission binding.')
        binding_keys.add(pair)
    grant_keys=set()
    for grant in package['grants']:
        _dict(grant); _ref(grant.get('dashboard_id'),indexes['dashboards'])
        uid,gid=grant.get('user_id'),grant.get('group_id')
        if (uid is None)==(gid is None): _fail()
        if uid is not None: _ref(uid,indexes['users'])
        if gid is not None: _ref(gid,indexes['groups'])
        key=(grant['dashboard_id'],uid,gid)
        if key in grant_keys: _fail('Duplicate dashboard grant.')
        grant_keys.add(key)
    if 'policies' in selected: validate_policy(package.get('policies'))
    elif package.get('policies') is not None: _fail()
    if 'ai' in selected:
        from redash.ai_settings import PROVIDERS,CONTEXT_LIMIT,validate_url
        ai=_dict(package.get('ai')); _text(ai.get('context'),CONTEXT_LIMIT)
        if ai.get('provider') not in PROVIDERS: _fail('Unsupported AI provider.')
        for provider,profile in _dict(ai.get('profiles')).items():
            if provider not in PROVIDERS: _fail('Unsupported AI provider.')
            _dict(profile); _text(profile.get('model'),200); url=_text(profile.get('base_url'),2048)
            if url: validate_url(url)
            if provider in ('openai','anthropic') and url and url!=PROVIDERS[provider]['base_url']: _fail('Invalid fixed provider URL.')
            key=_text(profile.get('api_key'),4096)
            if key and not package['credential_material']: _fail('Unexpected credential material.')
    elif package.get('ai') is not None: _fail()
    return indexes


def _mapping(package, indexes, org, source_mapping, errors):
    _dict(source_mapping)
    if 'sources' in package['components']:
        if source_mapping: errors.append('Source mapping cannot override included source definitions.')
        return {}
    wanted = {str(key) for key in indexes['source_refs']}
    if set(source_mapping) - wanted: errors.append('Unknown source mapping reference.')
    result = {}
    from redash.data_access_runtime import is_internal
    for sid, source in indexes['source_refs'].items():
        target = source_mapping.get(str(sid))
        if type(target) is not int or target < 1:
            errors.append('Every omitted source requires an explicit target mapping.'); continue
        target = models.DataSource.query.filter_by(id=target, org_id=org.id).first()
        if target is None or target.type != source['type'] or is_internal(target):
            errors.append('Mapped source is unavailable, incompatible or outside this organization.'); continue
        result[sid] = target.id
    return result


def inspect_content(package, org, user, source_mapping=None):
    errors = []; warnings = []
    try:
        indexes = _validate(package)
        _mapping(package, indexes, org, source_mapping or {}, errors)
        if 'rules' in package['components'] and _legacy_rules(org):
            errors.append('The destination uses legacy source restrictions. Migrate its rules configuration before importing new rules.')
        elif 'rules' in package['components']:
            from redash.data_access import validate_group_configuration
            try: validate_group_configuration(org.settings.get('data_access_group_rules', {'revision': 0, 'policies': []}))
            except ValueError: errors.append('The destination rules configuration is invalid. Repair it before importing rules.')
        for account in indexes['users'].values():
            if models.User.query.filter(models.User.org_id == org.id, models.User.email == account['email'].lower()).first():
                errors.append('An imported account email already exists. Export without Users or use a clean target with a different bootstrap Admin email.')
                break
        requirements = [dict(source) for source in indexes['source_refs'].values()] if 'sources' not in package['components'] else []
        if requirements:
            warnings.append('Existing source permissions apply to mapped sources and can make imported standalone queries readable by existing Editors. Mapping does not modify those permissions.')
        if 'users' not in package['components']:
            warnings.append('All imported queries and dashboards will belong to the importing Admin. No user accounts or direct user grants will be created.')
        elif package['users']:
            warnings.append('Imported accounts are disabled, receive new API keys and have no imported password or session. Review roles and configure authentication before enabling them.')
        if package['sources']:
            warnings.append('New sources have no broad default-group access. Configure and explicitly enable them before use.')
        if 'groups' in package['components'] and 'rules' not in package['components']:
            warnings.append('Groups are copied without source access bindings; configure source permissions explicitly.')
        if 'rules' in package['components']:
            warnings.append('Restrictions and source-group permissions apply only to new sources and new cloned groups. Existing target permissions are preserved.')
        if 'policies' in package['components']:
            warnings.append('The selected Editor/Reader policy replaces the target role policy; unrelated organization settings remain unchanged.')
        if 'ai' in package['components']:
            warnings.append('AI settings/context replace those selected target settings. No provider request is made.')
        return {'valid': not errors, 'errors': errors, 'warnings': warnings, 'package_id': package['package_id'],
            'components': package['components'], 'counts': {key: len(package[key]) for key in TABLES if key != 'source_refs'},
            'source_requirements': requirements, 'users': [{'name': u['name'], 'email': u['email'], 'role': u['role']} for u in indexes['users'].values()]}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        return {'valid': False, 'errors': [str(error) if isinstance(error, ValueError) else 'Invalid migration package.'],
            'warnings': [], 'counts': {}, 'source_requirements': [], 'users': []}


def _remap_query_options(options, query_ids):
    result = copy.deepcopy(options)
    for parameter in result.get('parameters', []):
        if parameter.get('type') == 'query': parameter['queryId'] = query_ids[int(parameter['queryId'])]
    return result


def import_content(package, org, user, source_mapping=None):
    report = inspect_content(package, org, user, source_mapping)
    if not report['valid']: _fail(report['errors'][0])
    indexes = _validate(package); selected = set(package['components']); session = models.db.session
    errors = []; source_ids = _mapping(package, indexes, org, source_mapping or {}, errors)
    if errors: _fail(errors[0])
    group_ids = {}; user_ids = {}; query_ids = {}; vis_ids = {}; board_ids = {}; widget_ids = {}; created_sources = []
    with session.begin_nested():
        for row in package['groups']:
            name = ('Imported ' + row['name'])[:100] if row['name'] in ('default', 'admin') else row['name']
            group = models.Group(org_id=org.id, name=name, type=models.Group.REGULAR_GROUP, permissions=row['permissions'])
            session.add(group); session.flush(); group_ids[row['id']] = group.id
        for row in package['users']:
            account = models.User(org_id=org.id, name=row['name'], email=row['email'].lower(), password_hash=None,
                group_ids=[group_ids[g] for g in row['groups']], disabled_at=datetime.now(timezone.utc),
                details={'role': row['role'], 'access_limits': validate_limits(row.get('limits', {})), 'migration_package_id': package['package_id']})
            session.add(account); session.flush(); user_ids[row['id']] = account.id
        for row in package['sources']:
            source = models.DataSource(org_id=org.id, name=row['name'], type=row['type'], options=ConfigurationContainer(copy.deepcopy(row['options'])))
            session.add(source); session.flush(); source_ids[row['id']] = source.id; created_sources.append(source.id)
        # Core writes avoid ORM hash hooks that load dropdown results. Pure
        # default rendering still supplies the same hash as a subsequent run.
        for row in package['queries']:
            owner = user_ids[row['owner_id']] if 'users' in selected else user.id
            qid = session.execute(insert(models.Query.__table__).values(org_id=org.id, user_id=owner, last_modified_by_id=user.id,
                data_source_id=source_ids.get(row['data_source_id']), name=row['name'], description=row.get('description'),
                query=row['query'], query_hash=_definition_hash(row, session.get(models.DataSource, source_ids.get(row['data_source_id']))
                    if source_ids.get(row['data_source_id']) is not None else None), version=1,
                is_archived=False, is_draft=row['is_draft'], schedule=None, options={'parameters': [], 'apply_auto_limit': False},
                tags=row['tags'], latest_query_data_id=None).returning(models.Query.id)).scalar_one()
            query_ids[row['id']] = qid
        for row in package['queries']:
            session.execute(update(models.Query.__table__).where(models.Query.id == query_ids[row['id']]).values(
                options=_remap_query_options(row['options'], query_ids)))
        for row in package['visualizations']:
            vis = models.Visualization(query_id=query_ids[row['query_id']], type=row['type'], name=row['name'], description=row.get('description'), options={})
            session.add(vis); session.flush(); vis_ids[row['id']] = vis.id
        for row in package['dashboards']:
            board = models.Dashboard(org_id=org.id, user=session.get(models.User, user_ids[row['owner_id']]) if 'users' in selected else user,
                name=row['name'], layout=[], options={}, tags=row['tags'], is_archived=False, is_draft=row['is_draft'],
                dashboard_filters_enabled=row['dashboard_filters_enabled'])
            session.add(board); session.flush(); board_ids[row['id']] = board.id
        for row in package['widgets']:
            widget = models.Widget(dashboard_id=board_ids[row['dashboard_id']], visualization_id=vis_ids.get(row['visualization_id']),
                text=row.get('text'), width=row['width'], options={})
            session.add(widget); session.flush(); widget_ids[row['id']] = widget.id
        maps = {'queries': query_ids, 'visualizations': vis_ids, 'widgets': widget_ids}
        for row in package['visualizations']:
            session.get(models.Visualization, vis_ids[row['id']]).options = _refs_in_options(row['options'], maps)
        for row in package['widgets']:
            session.get(models.Widget, widget_ids[row['id']]).options = _refs_in_options(row['options'], maps)
        for row in package['dashboards']:
            board = session.get(models.Dashboard, board_ids[row['id']])
            board.options = _refs_in_options(row['options'], maps)
            board.layout = [[widget_ids[wid] for wid in line] for line in row['layout']]
        for row in package['grants']:
            if row['user_id'] is not None:
                session.add(models.DashboardReadGrant(dashboard_id=board_ids[row['dashboard_id']], user_id=user_ids[row['user_id']], grantor_id=user.id))
            else:
                session.add(models.DashboardGroupReadGrant(dashboard_id=board_ids[row['dashboard_id']], group_id=group_ids[row['group_id']]))
        if 'rules' in selected:
            from redash.data_access import validate_group_configuration
            current = copy.deepcopy(org.settings.get('data_access_group_rules', {'revision': 0, 'policies': []}))
            for policy in package['rules']['policies']:
                policy = copy.deepcopy(policy); policy['id'] = str(uuid.uuid4()); policy['data_source_id'] = source_ids[policy['data_source_id']]
                policy['group_ids'] = [group_ids[g] for g in policy['group_ids']]; policy['user_ids'] = []
                current['policies'].append(policy)
            current['revision'] = int(current.get('revision', 0)) + 1
            org.settings['data_access_group_rules'] = validate_group_configuration(current)
            for row in package['source_groups']:
                session.add(models.DataSourceGroup(data_source_id=source_ids[row['source_id']], group_id=group_ids[row['group_id']], view_only=row['view_only']))
        if 'policies' in selected: org.settings['access_policy'] = validate_policy(package['policies'])
        if 'ai' in selected:
            from redash.ai_settings import secret_cipher
            ai = package['ai']; profiles = {}
            for provider, profile in ai['profiles'].items():
                key = profile['api_key']
                profiles[provider] = {'model': profile['model'], 'base_url': profile['base_url'],
                    'encrypted_api_key': secret_cipher(settings.DATASOURCE_SECRET_KEY).encrypt(key.encode()).decode() if key else ''}
            org.settings['ai_configuration'] = {'provider': ai['provider'], 'profiles': profiles}
            org.settings['ai_context'] = ai['context']
        latest = {}
        for row in package['history']:
            snapshot = copy.deepcopy(row['snapshot']); snapshot['data_source_id'] = source_ids.get(snapshot['data_source_id'])
            snapshot['options'] = _remap_query_options(snapshot['options'], query_ids)
            # Omitted historical identities are unknown, not the importing actor.
            actor = user_ids.get(row.get('actor_id'))
            session.add(models.QueryRevision(query_id=query_ids[row['query_id']], org_id=org.id, revision=row['revision'], actor_user_id=actor,
                created_at=datetime.fromisoformat(row['created_at']), kind=row['kind'], snapshot=snapshot, changed_fields=row['changed_fields']))
            latest[row['query_id']] = (row['revision'], snapshot)
        for row in package['queries']:
            snapshot = allowed_snapshot(row); snapshot['data_source_id'] = source_ids.get(row['data_source_id'])
            snapshot['options'] = _remap_query_options(snapshot['options'], query_ids)
            last, saved = latest.get(row['id'], (0, None))
            if saved != snapshot:
                session.add(models.QueryRevision(query_id=query_ids[row['id']], org_id=org.id, revision=last + 1, actor_user_id=user.id,
                    kind='baseline', snapshot=snapshot, changed_fields=list(SNAPSHOT_FIELDS)))
        session.flush()
    return {'package_id': package['package_id'], 'counts': report['counts'], 'created_source_ids': created_sources,
        'ownership': 'imported_users' if 'users' in selected else 'importing_admin',
        'source_map': {str(k): v for k, v in source_ids.items()}, 'query_map': {str(k): v for k, v in query_ids.items()},
        'dashboard_map': {str(k): v for k, v in board_ids.items()}, 'warnings': report['warnings']}
