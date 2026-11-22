"""Whitelisted query definitions and transaction-local immutable revision capture."""
from copy import deepcopy

from sqlalchemy import event, select
from sqlalchemy.orm import Session

PARAMETER_FIELDS = frozenset(("name", "title", "type", "value", "enumOptions", "queryId", "regex", "global", "multiValuesOptions"))
MULTI_VALUE_FIELDS = frozenset(("separator", "prefix", "suffix"))
SNAPSHOT_FIELDS = ("name", "description", "query", "data_source_id", "options")
_PENDING = "bidash_query_revision_candidates"


def _value(value):
    """Parameter defaults can contain values/ranges, not arbitrary option dictionaries."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return deepcopy(value)
    if isinstance(value, list):
        return [_value(v) for v in value if v is None or isinstance(v, (str, bool, int, float))]
    if isinstance(value, dict):
        return {key: _value(value[key]) for key in ("start", "end") if key in value}
    return None


def allowed_snapshot(definition):
    """Never copy raw Query.options, Change.change, API keys or scope fingerprints."""
    options = definition.get("options") or {}
    if not isinstance(options, dict):
        options = {}
    parameters = []
    for parameter in options.get("parameters", []) if isinstance(options.get("parameters", []), list) else []:
        if not isinstance(parameter, dict):
            continue
        safe = {}
        for key in PARAMETER_FIELDS:
            if key not in parameter:
                continue
            value = parameter[key]
            if key == "multiValuesOptions":
                safe[key] = {k: v for k, v in value.items() if k in MULTI_VALUE_FIELDS and isinstance(v, str)} if isinstance(value, dict) else None
            elif key in ("value", "enumOptions"):
                safe[key] = _value(value)
            elif value is None or isinstance(value, (str, bool, int, float)):
                safe[key] = deepcopy(value)
        parameters.append(safe)
    return {
        "name": definition.get("name") or "",
        "description": definition.get("description"),
        "query": definition.get("query") or "",
        "data_source_id": definition.get("data_source_id"),
        "options": {"parameters": parameters, "apply_auto_limit": bool(options.get("apply_auto_limit", False))},
    }


def snapshot_query(query):
    return allowed_snapshot({"name": query.name, "description": query.description,
        "query": query.query_text, "data_source_id": query.data_source_id, "options": query.options})


def install_revision_tracking(Query, QueryRevision):
    """Capture all ORM save paths, including constructor flushes and AI/fork creation."""
    if getattr(Query, "_bidash_revision_tracking_installed", False):
        return
    Query._bidash_revision_tracking_installed = True

    @event.listens_for(Session, "before_flush")
    def collect(session, flush_context, instances):
        if session.info.get("query_revision_capture_disabled"):
            session.info.pop(_PENDING, None)
            return
        candidates = [(obj, obj in session.new) for obj in list(session.new) + list(session.dirty)
                      if isinstance(obj, Query) and obj not in session.deleted]
        session.info[_PENDING] = candidates

    @event.listens_for(Session, "after_flush_postexec")
    def capture(session, flush_context):
        candidates = session.info.pop(_PENDING, [])
        seen = set()
        for query, created in sorted(candidates, key=lambda item: str(item[0].id)):
            if query.id in seen or query.id is None:
                continue
            seen.add(query.id)
            # The Query insert/update already holds its row lock. Also lock explicitly
            # for dirty relationship-only/no-op flushes before assigning a sequence.
            table = Query.__table__
            persisted = session.execute(select(table.c.name, table.c.description, table.c.query,
                table.c.data_source_id, table.c.options).where(table.c.id == query.id).with_for_update()).mappings().one()
            latest = session.query(QueryRevision).filter_by(query_id=query.id).order_by(QueryRevision.revision.desc()).first()
            snapshot = allowed_snapshot(persisted)
            if latest is not None and latest.snapshot == snapshot:
                continue
            actor = getattr(query, "changed_by", None)
            actor_id = getattr(actor, "id", None) if actor is not None and not actor.is_api_user() else None
            if actor_id is None:
                actor_id = query.last_modified_by_id or query.user_id
            previous = latest.snapshot if latest is not None else {}
            session.add(QueryRevision(query_id=query.id, org_id=query.org_id,
                revision=latest.revision + 1 if latest is not None else 1,
                actor_user_id=actor_id, kind="created" if latest is None and created else "baseline" if latest is None else "update",
                snapshot=snapshot, changed_fields=[field for field in SNAPSHOT_FIELDS if previous.get(field) != snapshot.get(field)]))

    @event.listens_for(Session, "after_rollback")
    def discard(session):
        session.info.pop(_PENDING, None)
