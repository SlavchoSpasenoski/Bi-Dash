"""Organization-scoped operational telemetry. Never serialize raw RQ metadata/args."""
import hashlib
import itertools
import json
import logging
import time
from datetime import datetime, timezone

from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.job import Job
from rq.registry import StartedJobRegistry

from redash import models, redis_connection, rq_redis_connection
from redash.models.users import LAST_ACTIVE_KEY

logger = logging.getLogger(__name__)
PRESENCE_TTL = 90
ACTIVITY_WINDOW = 300
INDEX_WINDOW = 3600
JOB_LIMIT = 1000
TAB_LIMIT = 50
QUERY_FUNCTION = 'redash.tasks.queries.execution.execute_query'


def presence_key(org_id, user_id):
    return f'monitor:presence:{org_id}:{user_id}'


def tab_key(org_id, user_id, digest):
    return f'{presence_key(org_id, user_id)}:{digest}'


def record_presence(org, user, session_id, tab_id, active):
    now = time.time()
    digest = hashlib.sha256(f'{session_id}:{tab_id}'.encode()).hexdigest()
    key = presence_key(org.id, user.id)
    pipe = redis_connection.pipeline()
    pipe.set(tab_key(org.id, user.id, digest), json.dumps({'seen': now, 'active': active}), ex=PRESENCE_TTL)
    pipe.zadd(key, {digest: now})
    pipe.zremrangebyscore(key, '-inf', now - 600)
    pipe.zremrangebyrank(key, 0, -TAB_LIMIT-1)
    pipe.expire(key, 600)
    pipe.execute()


def register_job(org_id, job_id):
    """Best effort; monitoring must not prevent an otherwise authorized query run."""
    try:
        now = time.time()
        key = f'monitor:jobs:{org_id}'
        pipe = rq_redis_connection.pipeline()
        pipe.zadd(key, {job_id: now})
        pipe.zremrangebyscore(key, '-inf', now - INDEX_WINDOW)
        pipe.zremrangebyrank(key, 0, -JOB_LIMIT-1)
        pipe.expire(key, INDEX_WINDOW + 60)
        pipe.execute()
    except RedisError:
        logger.warning('Query monitoring index unavailable')


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()
    return _number(value)


def _iso(value):
    value = _timestamp(value)
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _text(value):
    return value.decode() if isinstance(value, bytes) else str(value)


def _id(value):
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
        return n if n > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _users(org, page, page_size, now):
    query = models.User.query.filter_by(org_id=org.id, disabled_at=None).order_by(models.User.id)
    total = query.count()
    users = query.offset((page-1)*page_size).limit(page_size).all()
    health = 'ok'
    try:
        activity = redis_connection.hmget(LAST_ACTIVE_KEY, [str(u.id) for u in users]) if users else []
        presence = {}
        for user in users:
            entries = redis_connection.zrevrange(presence_key(org.id, user.id), 0, TAB_LIMIT-1, withscores=True)
            keys = [tab_key(org.id, user.id, _text(member)) for member, score in entries if now-PRESENCE_TTL <= score <= now+5]
            values = redis_connection.mget(keys) if keys else []
            fresh = []
            for value in values:
                try:
                    item = json.loads(value)
                    seen = _number(item.get('seen'))
                    if seen is not None and now-PRESENCE_TTL <= seen <= now+5 and isinstance(item.get('active'), bool):
                        fresh.append(item)
                except (ValueError, TypeError, AttributeError):
                    continue
            presence[user.id] = (entries, fresh)
    except RedisError:
        activity = [None]*len(users)
        presence = {}
        health = 'unavailable'
    result = []
    for user, recent in zip(users, activity):
        persisted = _timestamp(user.active_at)
        recent = _number(recent)
        times = [t for t in (persisted, recent) if t is not None and 0 <= t <= now+5]
        last = max(times) if times else None
        entries, fresh = presence.get(user.id, ([], []))
        state = 'active' if any(item['active'] for item in fresh) else 'idle' if fresh else 'stale' if entries else 'unknown'
        seen = max((item['seen'] for item in fresh), default=entries[0][1] if entries else None)
        result.append({'id': user.id, 'name': user.name, 'role': user.role, 'last_activity_at': _iso(last),
                       'presence': state, 'last_seen_at': _iso(seen)})
    return {'results': result, 'page': page, 'page_size': page_size, 'total': total}, health


def _worker_data(now):
    keys = list(itertools.islice(rq_redis_connection.sscan_iter('rq:workers', count=100), 101))
    workers = {}
    for key in keys[:100]:
        worker = Worker.find_by_key(_text(key), connection=rq_redis_connection)
        if worker is None:
            continue
        heartbeat = _timestamp(worker.last_heartbeat)
        state = worker.get_state()
        workers[worker.name] = {'name': worker.name, 'state': getattr(state, 'value', state),
            'last_heartbeat': _iso(heartbeat), 'stale': heartbeat is None or now-heartbeat > 90,
            'current_job_id': worker.get_current_job_id()}
    return workers, len(keys) > 100


def _jobs(org, user, page, page_size, now):
    workers, truncated = _worker_data(now)
    indexed = rq_redis_connection.zrevrangebyscore(f'monitor:jobs:{org.id}', now+5, now-INDEX_WINDOW,
                                                 start=0, num=JOB_LIMIT+1)
    truncated |= len(indexed) >= JOB_LIMIT
    # Transitional fallback for active jobs enqueued before telemetry was installed.
    queue_keys = list(itertools.islice(rq_redis_connection.sscan_iter('rq:queues', count=20), 21))
    truncated |= len(queue_keys) > 20
    ids = []
    for key in queue_keys[:20]:
        queue = Queue.from_queue_key(_text(key), connection=rq_redis_connection)
        queued = queue.get_job_ids(offset=0, length=101)
        started = StartedJobRegistry(queue=queue).get_job_ids(start=0, end=100, cleanup=False)
        truncated |= len(queued) > 100 or len(started) > 100
        ids.extend(started[:100] + queued[:100])
    ids.extend(indexed[:JOB_LIMIT])
    ids = list(dict.fromkeys(_text(i) for i in ids))
    truncated |= len(ids) > JOB_LIMIT
    jobs = Job.fetch_many(ids[:JOB_LIMIT], connection=rq_redis_connection) if ids else []
    missing = any(j is None for j in jobs)
    safe_jobs = []
    for job in jobs:
        if job is None or job.func_name != QUERY_FUNCTION or _id(job.meta.get('org_id')) != org.id:
            continue
        safe_jobs.append(job)
    source_ids = {_id(j.meta.get('data_source_id')) for j in safe_jobs}
    query_ids = {_id(j.meta.get('query_id')) for j in safe_jobs}
    user_ids = {_id(j.meta.get('user_id')) for j in safe_jobs if j.meta.get('is_api_key') is False}
    sources = {s.id: s.name for s in models.DataSource.query.filter(models.DataSource.org_id==org.id,
                                                                 models.DataSource.id.in_(source_ids)).all()}
    queries = {q.id: q.name for q in models.Query.query.filter(models.Query.org_id==org.id,
                                                             models.Query.id.in_(query_ids)).all()}
    actors = {u.id: u.name for u in models.User.query.filter(models.User.org_id==org.id,
                                                           models.User.id.in_(user_ids)).all()}
    result = []
    for job in safe_jobs:
        meta = job.meta
        source_id, query_id = _id(meta.get('data_source_id')), _id(meta.get('query_id'))
        if source_id not in sources:
            continue
        actor_id = _id(meta.get('user_id'))
        trigger = ('scheduled' if meta.get('scheduled') is True else 'api' if meta.get('is_api_key') is True or meta.get('monitoring_api') is True
                   else 'reader_report' if meta.get('reader_report') else 'dashboard' if meta.get('dashboard_report')
                   else 'manual' if meta.get('monitoring_api') is False and meta.get('is_api_key') is False else 'unknown')
        actor = actors.get(actor_id, 'Unknown') if meta.get('is_api_key') is False else 'API' if trigger=='api' else 'System' if trigger=='scheduled' else 'Unknown'
        status = job.get_status(refresh=False)
        status = getattr(status, 'value', status) or 'unknown'
        enqueued, started, ended = _timestamp(job.enqueued_at), _timestamp(job.started_at), _timestamp(job.ended_at)
        runtime = max(0, (ended or now)-started) if started else None
        worker = workers.get(job.worker_name)
        result.append({'id': job.id, 'query_id': query_id if query_id in queries else None,
            'query_name': queries.get(query_id, 'Ad hoc' if query_id is None else 'Unavailable query'),
            'data_source_name': sources[source_id], 'user_name': actor, 'trigger': trigger, 'state': status,
            'queue': job.origin, 'enqueued_at': _iso(enqueued), 'started_at': _iso(started),
            'wait_seconds': max(0, (started or now)-enqueued) if enqueued else None,
            'runtime_seconds': runtime, 'long_running': status=='started' and runtime is not None and runtime>=60,
            'worker_stale': (worker is None or worker['stale']) if status=='started' else False})
    priority = {'started': 0, 'queued': 1, 'deferred': 2, 'scheduled': 2}
    result.sort(key=lambda j: (priority.get(j['state'], 3), -(datetime.fromisoformat(j['enqueued_at']).timestamp() if j['enqueued_at'] else 0)))
    total = len(result)
    return {'results': result[(page-1)*page_size:page*page_size], 'page': page, 'page_size': page_size,
            'total': total, 'truncated': truncated}, list(workers.values()) if instance_admin(org, user) else [], 'partial' if missing or truncated else 'ok'


def instance_admin(org, user):
    from redash.permissions import is_instance_admin
    return user.org_id == org.id and is_instance_admin(user)


def get_snapshot(org, user, users_page=1, jobs_page=1, page_size=25):
    now = time.time()
    users, activity = _users(org, users_page, page_size, now)
    warnings = []
    try:
        jobs, workers, health = _jobs(org, user, jobs_page, page_size, now)
    except RedisError:
        jobs = {'results': [], 'page': jobs_page, 'page_size': page_size, 'total': None, 'truncated': False}
        workers, health = [], 'unavailable'
    if health != 'ok':
        warnings.append('Job status is incomplete or unavailable; counts are not a complete system total.')
    if activity != 'ok':
        warnings.append('Live activity is unavailable; persisted last activity may be stale.')
    return {'instance_admin': instance_admin(org, user), 'generated_at': _iso(now), 'refresh_seconds': 10, 'activity_window_seconds': ACTIVITY_WINDOW,
            'presence_ttl_seconds': PRESENCE_TTL, 'users': users, 'jobs': jobs, 'workers': workers,
            'health': {'activity': activity, 'jobs': health}, 'warnings': warnings}
