"""Independent adversarial migration checks against disposable QA infrastructure."""
import os
from unittest.mock import patch
from sqlalchemy.engine import make_url
from rq import SimpleWorker
from redash import models, rq_redis_connection
from redash.tasks.worker import Queue
from redash.utils.configuration import ConfigurationContainer
from redash.migration_package import seal, unseal
from tests import BaseTestCase
from tests.handlers import test_content_migration as baseline


class TestQAMigrationCampaign(BaseTestCase):
    call = baseline.TestContentMigration.call
    exported = baseline.TestContentMigration.exported
    preview = baseline.TestContentMigration.preview
    apply = baseline.TestContentMigration.apply

    def setUp(self):
        url = make_url(os.environ.get('REDASH_DATABASE_URL', 'postgresql:///unset'))
        if url.host != 'bidash-rigorous-pg' or url.database != 'bidash_access_app_test_migration_campaign':
            self.skipTest('Exclusive disposable migration campaign database required')
        super().setUp()
        self.admin = self.factory.create_admin()
        self.editor = self.factory.create_user(details={'role': 'editor'})
        self.reader = self.factory.create_user(details={'role': 'reader'})
        self.target = self.factory.create_org()
        self.importer = self.factory.create_admin(org=self.target)
        self.source = self.factory.data_source
        self.source.type = 'pg'
        self.source.options = ConfigurationContainer({'host': 'synthetic.invalid', 'dbname': 'sample', 'user': 'qa', 'password': 'PRIVATE_SOURCE_PASSWORD'})
        self.query = self.factory.create_query(user=self.editor, data_source=self.source, is_draft=False,
            name='Portable query', query_text='SELECT 7 AS migration_amount', options={'parameters': [], 'data_access_scope': 'PRIVATE_SCOPE'})
        self.board = self.factory.create_dashboard(user=self.editor, name='Portable dashboard', is_draft=False)
        self.vis = models.Visualization(query_rel=self.query, type="TABLE", name="Portable table", options={})
        models.db.session.add(self.vis)
        self.widget = models.Widget(dashboard=self.board, visualization=self.vis, width=1,
            options={'position': {'col': 0, 'row': 0, 'sizeX': 3, 'sizeY': 3}})
        models.db.session.add(self.widget)
        models.db.session.flush()
        self.board.layout = [[self.widget.id]]
        models.db.session.commit()
        self.app.config['PROPAGATE_EXCEPTIONS'] = False

    def test_imported_query_run_attaches_result_without_an_extra_save(self):
        url = make_url(os.environ['REDASH_DATABASE_URL'])
        self.source.options = ConfigurationContainer(dict(host=url.host, port=url.port or 5432,
            dbname=url.database, user=url.username, password=url.password))
        self.query.query_text = 'SELECT 42 AS amount'
        self.query.options = {'parameters': [], 'apply_auto_limit': False}
        models.db.session.commit()
        response, _ = self.apply(self.exported(credentials=True))
        self.assertEqual(response.status_code, 200, response.json)
        imported = models.Query.query.filter_by(org_id=self.target.id).one()
        imported_id = imported.id
        source = imported.data_source
        source.resume()
        queue = Queue(source.queue_name, connection=rq_redis_connection)
        from redash.tasks.queries.execution import execute_query
        job = queue.enqueue(execute_query, imported.query_text, source.id,
            {'query_id': imported.id}, user_id=self.importer.id)
        SimpleWorker([queue], connection=rq_redis_connection, job_class=queue.job_class).work(
            burst=True, with_scheduler=False, logging_level='ERROR')
        job.refresh()
        self.assertEqual(job.get_status().value, 'finished', job.exc_info)
        models.db.session.expire_all()
        imported = models.db.session.get(models.Query, imported_id)
        result = models.QueryResult.query.filter_by(data_source_id=source.id).one()
        self.assertEqual(result.data['rows'], [{'amount': 42}])
        self.assertEqual(imported.query_hash, result.query_hash, 'Imported execution hash must match the worker result')
        self.assertEqual(imported.latest_query_data_id, result.id,
            'Successful imported query run must populate the saved report/dashboard result pointer')

    def test_falsy_object_description_is_rejected_before_import(self):
        content = unseal(self.exported(), baseline.PASSWORD)
        content['queries'][0]['description'] = {}
        response, _ = self.preview(seal(content, baseline.PASSWORD))
        self.assertTrue(response.json['errors'], response.json)

    def test_falsy_object_description_import_returns_client_error_and_rolls_back(self):
        content = unseal(self.exported(), baseline.PASSWORD)
        content['queries'][0]['description'] = {}
        response, body = self.preview(seal(content, baseline.PASSWORD))
        if response.json.get('errors'):
            return
        body['preview_token'] = response.json['preview_token']
        result = self.call('import', body, user=self.importer)
        self.assertEqual(models.Query.query.filter_by(org_id=self.target.id).count(), 0)
        self.assertEqual(models.MigrationRun.query.filter_by(org_id=self.target.id).count(), 0)
        self.assertIn(result.status_code, (400, 409), result.get_data(as_text=True))

    def test_history_sql_object_is_rejected(self):
        content = unseal(self.exported(['queries', 'sources', 'history']), baseline.PASSWORD)
        self.assertTrue(content['history'])
        content['history'][0]['snapshot']['query'] = {'unexpected': 'object'}
        response, _ = self.preview(seal(content, baseline.PASSWORD))
        self.assertTrue(response.json['errors'], response.json)

    def test_history_revision_integer_overflow_is_rejected(self):
        content = unseal(self.exported(['queries', 'sources', 'history']), baseline.PASSWORD)
        self.assertTrue(content['history'])
        content['history'][0]['revision'] = 10 ** 100
        response, _ = self.preview(seal(content, baseline.PASSWORD))
        self.assertTrue(response.json['errors'], response.json)

    def test_history_sql_object_cannot_be_persisted(self):
        content = unseal(self.exported(['queries', 'sources', 'history']), baseline.PASSWORD)
        content['history'][0]['snapshot']['query'] = {'unexpected': 'object'}
        response, body = self.preview(seal(content, baseline.PASSWORD))
        if response.json.get('errors'):
            return
        body['preview_token'] = response.json['preview_token']
        result = self.call('import', body, user=self.importer)
        if result.status_code in (400, 409):
            return
        self.assertEqual(result.status_code, 200, result.json)
        imported = models.Query.query.filter_by(org_id=self.target.id).one()
        snapshots = models.QueryRevision.query.filter_by(query_id=imported.id).all()
        self.assertTrue(all(isinstance(row.snapshot['query'], str) for row in snapshots),
            'Import committed a history SQL object instead of SQL text')

    def test_revision_integer_overflow_import_is_client_error(self):
        content = unseal(self.exported(['queries', 'sources', 'history']), baseline.PASSWORD)
        content['history'][0]['revision'] = 10 ** 100
        response, body = self.preview(seal(content, baseline.PASSWORD))
        if response.json.get('errors'):
            return
        body['preview_token'] = response.json['preview_token']
        result = self.call('import', body, user=self.importer)
        self.assertEqual(models.Query.query.filter_by(org_id=self.target.id).count(), 0)
        self.assertEqual(models.MigrationRun.query.filter_by(org_id=self.target.id).count(), 0)
        self.assertIn(result.status_code, (400, 409), result.get_data(as_text=True))

    def test_import_hash_matches_normal_default_rendering_and_auto_limit(self):
        from redash.content_migration import _definition_hash
        from redash.models.parameterized_query import ParameterizedQuery
        cases = [
            ('SELECT 42 AS amount -- trailing comment', []),
            ('SELECT {{number}} AS amount', [{'name': 'number', 'type': 'number', 'value': 42}]),
            ("SELECT '{{choice}}' AS choice", [{'name': 'choice', 'type': 'enum', 'value': 'hello', 'enumOptions': 'hello\nworld'}]),
            ("SELECT '{{day}}'::date AS day", [{'name': 'day', 'type': 'date', 'value': '2026-11-22'}]),
            ('SELECT {{choices}} AS choices', [{'name': 'choices', 'type': 'enum', 'value': ['42', '43'],
                'enumOptions': '42\n43', 'multiValuesOptions': {'separator': ',', 'prefix': '', 'suffix': ''}}]),
        ]
        for sql, parameters in cases:
            for auto_limit in (False, True):
                with self.subTest(sql=sql, auto_limit=auto_limit):
                    values = {row['name']: row['value'] for row in parameters}
                    rendered = ParameterizedQuery(sql, parameters).apply(values).query if values else sql
                    expected = self.source.query_runner.gen_query_hash(rendered, auto_limit)
                    definition = {'query': sql, 'options': {'parameters': parameters, 'apply_auto_limit': auto_limit}}
                    self.assertEqual(_definition_hash(definition, self.source), expected)

    def test_import_dropdown_hash_does_not_load_cached_values(self):
        from redash.content_migration import _definition_hash
        definition = {'query': 'SELECT {{choice}} AS amount', 'options': {'apply_auto_limit': True,
            'parameters': [{'name': 'choice', 'type': 'query', 'queryId': self.query.id, 'value': '42'}]}}
        with patch('redash.models.parameterized_query.dropdown_values', side_effect=AssertionError('No result reads during import')):
            actual = _definition_hash(definition, self.source)
        self.assertEqual(actual, self.source.query_runner.gen_query_hash('SELECT 42 AS amount', True))
