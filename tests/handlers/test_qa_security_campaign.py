"""Independent adversarial security campaign. Disposable metadata only."""
import json
import os
import time
from unittest.mock import patch

from flask import g
from rq.job import Job
from rq.registry import StartedJobRegistry
from sqlalchemy.engine import make_url

from redash import models, redis_connection, rq_redis_connection
from redash.tasks.worker import Queue
from tests import BaseTestCase


class TestQASecurityCampaign(BaseTestCase):
    def setUp(self):
        url = make_url(os.environ.get('REDASH_DATABASE_URL', 'postgresql:///unset'))
        if url.host != 'bidash-rigorous-pg' or url.database != 'bidash_access_app_test_security_campaign':
            self.skipTest('Dedicated disposable security campaign database required')
        super().setUp()
        self.admin = self.factory.create_user(details={'role': 'admin'}, name='Organization Admin')
        self.instance_admin = self.factory.create_admin()
        self.editor = self.factory.create_user(details={'role': 'editor'})
        self.reader = self.factory.create_user(details={'role': 'reader'})
        self.foreign_org = self.factory.create_org()
        self.foreign_user = self.factory.create_user(org=self.foreign_org, details={'role': 'editor'},
            email='foreign-private@example.test', name='FOREIGN_PRIVATE_USER')
        self.source = self.factory.data_source
        self.query = self.factory.create_query(user=self.editor, data_source=self.source, is_draft=False,
            query_text='SELECT 42 AS amount')
        self.board = self.factory.create_dashboard(user=self.editor, is_draft=False)
        self.vis = models.Visualization(query_rel=self.query, type='TABLE', name='Synthetic table', options={})
        self.widget = models.Widget(dashboard=self.board, visualization=self.vis, width=1, options={})
        models.db.session.add_all([self.vis, self.widget]); models.db.session.flush()
        self.board.layout = [[self.widget.id]]
        foreign_source = self.factory.create_data_source(org=self.foreign_org)
        foreign_query = self.factory.create_query(org=self.foreign_org, user=self.foreign_user,
            data_source=foreign_source, is_draft=False)
        self.foreign_alert = models.Alert(user=self.foreign_user, query_rel=foreign_query,
            name='Foreign alert', options={})
        self.local_alert = models.Alert(user=self.editor, query_rel=self.query, name='Local alert', options={})
        models.db.session.add_all([self.foreign_alert, self.local_alert]); models.db.session.flush()
        self.foreign_subscription = models.AlertSubscription(user=self.foreign_user, alert=self.foreign_alert)
        self.local_subscription = models.AlertSubscription(user=self.editor, alert=self.local_alert)
        models.db.session.add_all([self.foreign_subscription, self.local_subscription]); models.db.session.commit()

    def make_request(self, *args, **kwargs):
        g.pop('org', None)
        return super().make_request(*args, **kwargs)

    def explicit_super_admin(self, user):
        return any('super_admin' in group.permissions for group in models.Group.query.filter(
            models.Group.org_id == user.org_id, models.Group.id.in_(user.group_ids)))

    def test_org_admin_cannot_read_instance_admin_api_key(self):
        response = self.make_request('get', '/api/users/{}'.format(self.instance_admin.id), user=self.admin)
        self.assertNotIn('api_key', response.json)

    def test_org_admin_cannot_mutate_instance_admin_email(self):
        original = self.instance_admin.email
        response = self.make_request('post', '/api/users/{}'.format(self.instance_admin.id),
            user=self.admin, data={'email': 'attacker-controlled@example.test'})
        self.assertEqual(response.status_code, 403, response.json)
        models.db.session.refresh(self.instance_admin)
        self.assertEqual(self.instance_admin.email, original)

    def test_org_admin_cannot_disable_instance_admin(self):
        response = self.make_request('post', '/api/users/{}/disable'.format(self.instance_admin.id),
            user=self.admin)
        self.assertEqual(response.status_code, 403, response.json)
        models.db.session.refresh(self.instance_admin)
        self.assertFalse(self.instance_admin.is_disabled)

    def test_instance_account_management_boundaries(self):
        for suffix in ('invite', 'reset_password', 'regenerate_api_key'):
            with self.subTest(action=suffix), patch('redash.handlers.users.send_password_reset_email') as reset:
                response = self.make_request('post', f'/api/users/{self.instance_admin.id}/{suffix}', user=self.admin)
                self.assertEqual(response.status_code, 403, response.json)
                reset.assert_not_called()
        response = self.make_request('delete', f'/api/users/{self.instance_admin.id}', user=self.admin)
        self.assertEqual(response.status_code, 403, response.json)
        with patch('redash.security.csrf.protect'):
            response = self.make_request('post', f'/api/users/{self.instance_admin.id}/access',
                user=self.admin, data={'role': 'editor', 'limits': {}})
        self.assertEqual(response.status_code, 403, response.json)

    def test_explicit_instance_admin_can_manage_accounts_and_grants(self):
        with patch('redash.handlers.get_status', return_value={'ok': True}):
            response = self.make_request('get', '/status.json', org=False, user=self.instance_admin)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        response = self.make_request('post', f'/api/groups/{self.factory.admin_group.id}/members',
            user=self.instance_admin, data={'user_id': self.admin.id})
        self.assertEqual(response.status_code, 200, response.json)
        models.db.session.refresh(self.admin)
        self.assertTrue(self.explicit_super_admin(self.admin))

    def test_view_only_execution_requires_saved_safe_definition(self):
        from redash.tasks.queries.execution import _can_execute_saved_view_only, enqueue_query, QueryExecutionError
        link = models.DataSourceGroup.query.filter_by(data_source_id=self.source.id,
            group_id=self.factory.default_group.id).one()
        link.view_only = True
        models.db.session.commit()
        metadata = {'query_id': self.query.id, 'saved_parameters': {}}
        self.assertTrue(_can_execute_saved_view_only(self.source, self.editor, self.query.query_text, metadata))
        self.assertFalse(_can_execute_saved_view_only(self.source, self.editor, 'SELECT 99', metadata))
        response = self.make_request('post', f'/api/queries/{self.query.id}', user=self.editor,
            data={'query': 'SELECT 99'})
        self.assertEqual(response.status_code, 403, response.json)
        with self.assertRaises(QueryExecutionError):
            enqueue_query('SELECT 99', self.source, self.editor.id, metadata=metadata)
        self.query.query_text = 'SELECT {{amount}}'
        self.query.options = {'parameters': [{'name': 'amount', 'type': 'number'}]}
        models.db.session.commit()
        metadata['saved_parameters'] = {'amount': 42}
        self.assertTrue(_can_execute_saved_view_only(self.source, self.editor, 'SELECT 42', metadata))
        metadata['saved_parameters'] = {'amount': 'invalid'}
        self.assertFalse(_can_execute_saved_view_only(self.source, self.editor, 'SELECT 42', metadata))
        self.query.query_text = 'SELECT 99'
        models.db.session.commit()
        self.assertFalse(_can_execute_saved_view_only(self.source, self.editor, 'SELECT 42', metadata))

    def test_view_only_saved_query_runs_in_actual_worker(self):
        from rq import SimpleWorker
        from redash.tasks.queries.execution import execute_query
        from redash.utils.configuration import ConfigurationContainer
        url = make_url(os.environ['REDASH_DATABASE_URL'])
        self.source.type = 'pg'
        self.source.options = ConfigurationContainer(dict(host=url.host, port=url.port or 5432,
            dbname=url.database, user=url.username, password=url.password))
        models.DataSourceGroup.query.filter_by(data_source_id=self.source.id,
            group_id=self.factory.default_group.id).one().view_only = True
        self.query.query_text = 'SELECT {{amount}} AS amount'
        self.query.options = {'parameters': [{'name': 'amount', 'type': 'number'}]}
        models.db.session.commit()
        queue = Queue('qa_view_only_worker', connection=rq_redis_connection)
        queue.empty()
        job = queue.enqueue(execute_query, 'SELECT 42 AS amount', self.source.id,
            {'query_id': self.query.id, 'saved_parameters': {'amount': 42}}, user_id=self.editor.id)
        SimpleWorker([queue], connection=rq_redis_connection, job_class=queue.job_class).work(
            burst=True, with_scheduler=False, logging_level='ERROR')
        job.refresh()
        self.assertEqual(job.get_status().value, 'finished', job.exc_info)
        models.db.session.expire_all()
        result = models.QueryResult.query.filter_by(data_source_id=self.source.id).one()
        self.assertEqual(result.data['rows'], [{'amount': 42}])

    def test_bad_email_shapes_are_client_errors(self):
        for email in (None, 42, 'missing-at', 'a@@example.test', '@example.test'):
            with self.subTest(email=email):
                response = self.make_request('post', f'/api/users/{self.editor.id}',
                    user=self.admin, data={'email': email})
                self.assertEqual(response.status_code, 400, response.json)

    def test_protected_view_only_saved_query_keeps_column_rules_in_worker(self):
        if os.environ.get('QA_SAVED_MYSQL_HOST') != 'access-test-mysql':
            self.skipTest('Explicit disposable saved-query MySQL required')
        import MySQLdb
        from rq import SimpleWorker
        from redash.utils.configuration import ConfigurationContainer
        connection = MySQLdb.connect(host='access-test-mysql', user='root', passwd='synthetic-connector-qa')
        connection.autocommit(True)
        try:
            with connection.cursor() as cursor:
                cursor.execute('CREATE DATABASE IF NOT EXISTS bidash_qa_saved_only')
                cursor.execute('DROP TABLE IF EXISTS bidash_qa_saved_only.orders')
                cursor.execute('CREATE TABLE bidash_qa_saved_only.orders(id INT, amount INT, secret TEXT)')
                cursor.execute("INSERT INTO bidash_qa_saved_only.orders VALUES(1,42,'hidden')")
            self.source.type = 'mysql'
            self.source.options = ConfigurationContainer({'host': 'access-test-mysql', 'port': 3306,
                'db': 'bidash_qa_saved_only', 'user': 'root', 'passwd': 'synthetic-connector-qa'})
            models.DataSourceGroup.query.filter_by(data_source_id=self.source.id,
                group_id=self.factory.default_group.id).one().view_only = True
            # Protected sources intentionally do not support alerts.
            models.db.session.delete(self.local_subscription)
            models.db.session.delete(self.local_alert)
            self.query.query_text = 'SELECT id,amount FROM orders WHERE id={{id}}'
            self.query.options = {'parameters': [{'name': 'id', 'type': 'number'}]}
            models.db.session.commit()
            policy = {'id': 'qa-saved', 'name': 'QA saved', 'data_source_id': self.source.id,
                'status': 'active', 'default_effect': 'deny', 'user_ids': [],
                'group_ids': [self.factory.default_group.id], 'rules': [{'catalog': None,
                    'schema': 'bidash_qa_saved_only', 'table': 'orders',
                    'column_mode': 'explicit', 'columns': ['id', 'amount']}]}
            with patch('redash.security.csrf.protect'):
                response = self.make_request('post', '/api/settings/data-access', user=self.admin,
                    data={'revision': 0, 'policies': [policy]})
            self.assertEqual(response.status_code, 200, response.json)
            response = self.make_request('post', f'/api/queries/{self.query.id}/results', user=self.editor,
                data={'parameters': {'id': 1}, 'max_age': 0})
            self.assertEqual(response.status_code, 200, response.json)
            job = Job.fetch(response.json['job']['id'], connection=rq_redis_connection)
            queue = Queue(self.source.queue_name, connection=rq_redis_connection)
            SimpleWorker([queue], connection=rq_redis_connection, job_class=queue.job_class).work(
                burst=True, with_scheduler=False, logging_level='ERROR')
            job.refresh()
            self.assertEqual(job.get_status().value, 'finished', job.exc_info)
            models.db.session.expire_all()
            result = models.QueryResult.query.filter_by(data_source_id=self.source.id).one()
            self.assertEqual(result.data['rows'], [{'id': 1, 'amount': 42}])
            self.assertNotIn('secret', [column['name'] for column in result.data['columns']])
        finally:
            with connection.cursor() as cursor:
                cursor.execute('DROP DATABASE IF EXISTS bidash_qa_saved_only')
            connection.close()
    def test_org_admin_cannot_read_global_instance_status(self):
        self.assertFalse(self.explicit_super_admin(self.admin))
        with patch('redash.handlers.get_status', return_value={'private_instance_metric': 'PRIVATE_GLOBAL_MARKER'}):
            response = self.make_request('get', '/status.json', org=False, user=self.admin)
        self.assertIn(response.status_code, (403, 404), response.get_data(as_text=True))

    def test_org_admin_cannot_read_foreign_started_job_metadata(self):
        queue = Queue('qa_foreign_started', connection=rq_redis_connection)
        job = queue.enqueue('builtins.len', [1], meta={'org_id': self.foreign_org.id,
            'user_id': self.foreign_user.id, 'reader_report': {'parameters': {'private': 'FOREIGN_JOB_PARAMETER_SECRET'}}})
        queue.remove(job.id)
        registry = StartedJobRegistry(queue=queue)
        rq_redis_connection.zadd(registry.key, {job.id: time.time() + 300})
        response = self.make_request('get', '/api/admin/queries/rq_status', org=False, user=self.admin)
        self.assertIn(response.status_code, (403, 404), response.get_data(as_text=True))
        self.assertNotIn('FOREIGN_JOB_PARAMETER_SECRET', response.get_data(as_text=True))

    def test_org_admin_cannot_promote_self_to_explicit_instance_group(self):
        self.assertFalse(self.explicit_super_admin(self.admin))
        response = self.make_request('post', f'/api/groups/{self.factory.admin_group.id}/members',
            user=self.admin, data={'user_id': self.admin.id})
        self.assertIn(response.status_code, (400, 403), response.json)
        models.db.session.refresh(self.admin)
        self.assertFalse(self.explicit_super_admin(self.admin))

    def test_org_admin_cannot_grant_instance_group_to_another_org_admin(self):
        other = self.factory.create_user(details={'role': 'admin'}); models.db.session.commit()
        response = self.make_request('post', f'/api/users/{other.id}', user=self.admin,
            data={'group_ids': [self.factory.default_group.id, self.factory.admin_group.id]})
        self.assertEqual(response.status_code, 403, response.json)
        models.db.session.refresh(other)
        self.assertFalse(self.explicit_super_admin(other))

    def test_admin_cannot_list_other_organization_group_members(self):
        response = self.make_request('get', f'/api/groups/{self.foreign_org.default_group.id}/members', user=self.admin)
        self.assertIn(response.status_code, (403, 404), response.json)
        self.assertNotIn('FOREIGN_PRIVATE_USER', response.get_data(as_text=True))
        self.assertNotIn('foreign-private@example.test', response.get_data(as_text=True))

    def test_admin_cannot_delete_other_organization_subscription(self):
        ident = self.foreign_subscription.id
        response = self.make_request('delete', f'/api/alerts/{self.local_alert.id}/subscriptions/{ident}', user=self.admin)
        self.assertIn(response.status_code, (403, 404), response.json)
        self.assertIsNotNone(models.db.session.get(models.AlertSubscription, ident))

    def test_subscription_route_alert_must_match_subscription(self):
        other_alert = models.Alert(user=self.editor, query_rel=self.query, name='Other local alert', options={})
        models.db.session.add(other_alert); models.db.session.commit()
        ident = self.local_subscription.id
        response = self.make_request('delete', f'/api/alerts/{other_alert.id}/subscriptions/{ident}', user=self.admin)
        self.assertEqual(response.status_code, 404, response.json)
        self.assertIsNotNone(models.db.session.get(models.AlertSubscription, ident))

    def test_editor_cannot_modify_other_editor_reports_even_with_generic_modify_grant(self):
        other = self.factory.create_user(details={'role': 'editor'})
        models.AccessPermission.grant(self.query, 'modify', other, self.instance_admin)
        models.db.session.add(models.DashboardReadGrant(dashboard_id=self.board.id, user_id=other.id,
            grantor_id=self.editor.id)); models.db.session.commit()
        for path, data in ((f'/api/queries/{self.query.id}', {'name':'Must not overwrite'}),
                           (f'/api/dashboards/{self.board.id}', {'name':'Must not overwrite'}),
                           (f'/api/visualizations/{self.vis.id}', {'name':'Must not overwrite'})):
            response = self.make_request('post', path, user=other, data=data)
            self.assertEqual(response.status_code, 403, response.json)

    def test_admin_query_listing_has_no_foreign_rows(self):
        response = self.make_request('get', '/api/queries', user=self.admin)
        self.assertEqual(response.status_code, 200, response.json)
        expected = {q.id for q in models.Query.query.filter_by(org_id=self.factory.org.id)}
        self.assertTrue({row['id'] for row in response.json['results']}.issubset(expected))

    def test_reader_assigned_dashboard_gets_no_sql_or_query_key(self):
        models.db.session.add(models.DashboardReadGrant(dashboard_id=self.board.id,user_id=self.reader.id,
            grantor_id=self.editor.id)); models.db.session.commit()
        response = self.make_request('get', f'/api/dashboards/{self.board.id}', user=self.reader)
        self.assertEqual(response.status_code, 200, response.json)
        body=response.get_data(as_text=True)
        self.assertNotIn(self.query.query_text, body); self.assertNotIn(self.query.api_key, body)

    def test_disabled_admin_cannot_access_migration_or_monitoring(self):
        from redash.utils import utcnow
        self.admin.disabled_at=utcnow();models.db.session.commit()
        for method,path,data in (('get','/api/admin/monitoring',None),
                                 ('post','/api/admin/migration/export',{})):
            response=self.make_request(method,path,user=self.admin,data=data)
            self.assertNotEqual(response.status_code,200)

    def test_alert_listing_filters_hidden_queries_instead_of_failing_whole_list(self):
        other = self.factory.create_user(details={'role':'editor'})
        visible = self.factory.create_query(user=other,data_source=self.source,is_draft=False)
        visible_alert = models.Alert(user=other,query_rel=visible,name='Visible alert',options={})
        models.db.session.add(visible_alert);models.db.session.commit()
        # self.query is attached to an unassigned dashboard, so the other Editor
        # must neither see that alert nor lose their accessible alert list.
        response=self.make_request('get','/api/alerts',user=other)
        self.assertEqual(response.status_code,200,response.json)
        self.assertIn(visible_alert.id,[row['id'] for row in response.json])
        self.assertNotIn(self.local_alert.id,[row['id'] for row in response.json])

    def test_reader_organization_dashboard_count_respects_visibility(self):
        hidden=self.factory.create_dashboard(user=self.editor,is_draft=False)
        models.db.session.add(models.DashboardReadGrant(dashboard_id=self.board.id,user_id=self.reader.id,
            grantor_id=self.editor.id));models.db.session.commit()
        response=self.make_request('get','/api/organization/status',user=self.reader)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['object_counters']['dashboards'],1)
