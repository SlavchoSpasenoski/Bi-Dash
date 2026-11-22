"""Read-only historical query definitions under current organization/source rules."""
from types import SimpleNamespace

from flask import request, session
from flask_restful import abort

from redash import limiter, models
from redash.access_policy import can_view_sql
from redash.data_access_reports import can_read_saved_query
from redash.handlers.base import BaseResource
from redash.models.parameterized_query import ParameterizedQuery
from redash.permissions import has_access, require_access, view_only
from redash.query_revisions import allowed_snapshot, snapshot_query


def _positive(value):
    try:
        number = int(value)
        if isinstance(value, bool) or number < 1:
            raise ValueError()
        return number
    except (ValueError, TypeError, OverflowError):
        abort(400, message='Invalid history request.')


def snapshot_readable(snapshot, user, org):
    """No old policy fingerprint shortcuts; historical dependencies are rechecked."""
    safe = allowed_snapshot(snapshot)
    source_id = safe['data_source_id']
    if source_id is None:
        return user.role == 'admin'
    source = models.DataSource.query.filter_by(id=source_id, org_id=org.id).first()
    if source is None or not has_access(source, user, view_only):
        return False
    if user.role == 'admin':
        return True
    for parameter in safe['options']['parameters']:
        if parameter.get('type') == 'query':
            try:
                dependency = models.Query.query.filter_by(id=int(parameter.get('queryId')), org_id=org.id).first()
                if dependency is None or not has_access(dependency, user, view_only):
                    return False
            except (ValueError, TypeError):
                return False
    proxy = SimpleNamespace(data_source=source, query_text=safe['query'], options=safe['options'],
        parameterized=ParameterizedQuery(safe['query'], schema=safe['options']['parameters'], org=org))
    return can_read_saved_query(proxy, user)


def revision_metadata(revision, org):
    actor = revision.actor_user
    return {'id': revision.id, 'version': revision.revision, 'kind': revision.kind,
            'created_at': revision.created_at.isoformat(),
            'author': {'id': actor.id, 'name': actor.name} if actor is not None and actor.org_id == org.id else None,
            'changed_fields': [k for k in revision.changed_fields if k in ('query','name','description','data_source_id','options')]}


class HistoryBase(BaseResource):
    def readable_query(self, query_id):
        user, org = self.current_user, self.current_org
        if (user.is_api_user() or user.is_disabled or user.org_id != org.id or user.role not in ('admin','editor')
                or not can_view_sql(user) or session.get('_user_id') != user.get_id()
                or 'api_key' in request.args or request.headers.get('Authorization')):
            abort(403)
        query = models.Query.query.filter_by(id=_positive(query_id), org_id=org.id).first()
        if query is None:
            abort(404)
        require_access(query, user, view_only)
        return query

    def response(self, value):
        from redash.handlers.base import json_response
        response = json_response(value)
        response.headers['Cache-Control'] = 'private, no-store'
        return response


class QueryVersionsResource(HistoryBase):
    @limiter.limit('30/minute')
    def get(self, query_id):
        query = self.readable_query(query_id)
        page = _positive(request.args.get('page', 1))
        size = _positive(request.args.get('page_size', 20))
        if size > 100 or page > 100000:
            abort(400)
        revisions = models.QueryRevision.query.filter_by(query_id=query.id, org_id=self.current_org.id).order_by(models.QueryRevision.revision.desc())
        if self.current_user.role == 'admin':
            # Source changes/deletions can still make individual snapshots unavailable.
            candidates = revisions.yield_per(100)
        else:
            candidates = revisions.yield_per(100)
        count, results = 0, []
        start, end = (page-1)*size, page*size
        for revision in candidates:
            if not snapshot_readable(revision.snapshot, self.current_user, self.current_org):
                continue
            if start <= count < end:
                results.append(revision_metadata(revision, self.current_org))
            count += 1
        return self.response({'results': results, 'page': page, 'page_size': size, 'count': count})


class QueryVersionResource(HistoryBase):
    @limiter.limit('60/minute')
    def get(self, query_id, revision_id):
        query = self.readable_query(query_id)
        def revision(ident):
            result = models.QueryRevision.query.filter_by(id=_positive(ident), query_id=query.id, org_id=self.current_org.id).first()
            if result is None or not snapshot_readable(result.snapshot, self.current_user, self.current_org):
                abort(404)
            return result
        selected = revision(revision_id)
        current = snapshot_query(query)
        value = {**revision_metadata(selected, self.current_org), 'snapshot': allowed_snapshot(selected.snapshot),
                 'current': current if snapshot_readable(current, self.current_user, self.current_org) else None}
        if 'compare_to' in request.args:
            comparison = revision(request.args['compare_to'])
            value['comparison'] = {'id': comparison.id, 'version': comparison.revision,
                                   'snapshot': allowed_snapshot(comparison.snapshot)}
        return self.response(value)
