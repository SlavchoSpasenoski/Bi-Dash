"""Selective migration round trips against disposable metadata only."""
import copy
import os
from unittest.mock import patch
from sqlalchemy.engine import make_url
from tests import BaseTestCase
from redash import models, settings
from redash.content_migration import export_content, inspect_content, import_content, normalize_components
from redash.utils.configuration import ConfigurationContainer


class TestContentMigrationEngine(BaseTestCase):
    def setUp(self):
        if not (make_url(os.environ.get('REDASH_DATABASE_URL', 'postgresql:///unset')).database or '').startswith('bidash_access_app_test'):
            self.skipTest('Disposable metadata required')
        super().setUp()
        self.admin = self.factory.create_admin(email='origin-admin@example.test')
        self.editor = self.factory.create_user(email='origin-editor@example.test', details={'role': 'editor'})
        self.source = self.factory.data_source
        self.source.type = 'pg'
        self.source.options = ConfigurationContainer({'host': 'unused.invalid', 'dbname': 'synthetic', 'user': 'fake', 'password': 'SOURCE_SECRET'})
        self.query = self.factory.create_query(user=self.editor, data_source=self.source, query_text='SELECT 1 AS amount',
            is_draft=False, options={'parameters': [], 'data_access_scope': 'PRIVATE_SCOPE', 'api_key': 'PRIVATE_OPTION'}, tags=['finance'])
        self.vis = self.factory.create_visualization(query_rel=self.query, name='Amount table')
        self.board = self.factory.create_dashboard(user=self.editor, name='Finance', is_draft=False)
        self.widget = self.factory.create_widget(dashboard=self.board, visualization=self.vis,
            options={'position': {'col': 0, 'row': 0, 'sizeX': 6, 'sizeY': 5}, 'parameterMappings': {}})
        self.board.layout = [[self.widget.id]]
        self.target = self.factory.create_org(name='Clean destination')
        self.importer = self.factory.create_admin(org=self.target, email='destination-admin@example.test')
        models.db.session.commit()

    def export(self, components=None, credentials=False):
        return export_content(self.factory.org, self.admin, components or ['dashboards', 'sources'], credentials)

    def import_package(self, package, mapping=None):
        result = import_content(package, self.target, self.importer, mapping)
        models.db.session.commit()
        return result

    def test_roundtrip_no_users_maps_owners_and_relations_and_no_secrets(self):
        package = self.export()
        text = str(package)
        for secret in ('SOURCE_SECRET', 'PRIVATE_SCOPE', 'PRIVATE_OPTION', self.admin.api_key, self.query.api_key):
            self.assertNotIn(secret, text)
        self.assertEqual(package['users'], [])
        before_users = models.User.query.count()
        result = self.import_package(package)
        query = models.Query.query.get(result['query_map'][str(self.query.id)])
        board = models.Dashboard.query.get(result['dashboard_map'][str(self.board.id)])
        self.assertEqual(query.user_id, self.importer.id); self.assertEqual(board.user_id, self.importer.id)
        self.assertIsNone(query.latest_query_data_id); self.assertIsNone(query.schedule)
        self.assertNotEqual(query.api_key, self.query.api_key)
        self.assertEqual(models.User.query.count(), before_users)
        widget = board.widgets.first()
        self.assertEqual(widget.visualization.query_id, query.id)
        self.assertEqual(board.layout, [[widget.id]])
        self.assertNotEqual(widget.id, self.widget.id)
        self.assertEqual(query.tags, ['finance'])
        self.assertEqual(models.DataSourceGroup.query.filter_by(data_source_id=query.data_source_id).count(), 0)
        self.assertNotIn('password', query.data_source.options.to_dict())
        self.assertEqual(models.QueryRevision.query.filter_by(query_id=query.id).count(), 1)

    def test_queries_only_require_explicit_compatible_same_org_mapping(self):
        package = self.export(['queries'])
        report = inspect_content(package, self.target, self.importer)
        self.assertFalse(report['valid']); self.assertEqual(len(report['source_requirements']), 1)
        self.assertFalse(inspect_content(package, self.target, self.importer, {str(self.source.id): self.source.id})['valid'])
        target_source = self.factory.create_data_source(org=self.target, type='pg', options=ConfigurationContainer({'host': 'unused'}))
        models.db.session.commit()
        mapping = {str(self.source.id): target_source.id}
        report = inspect_content(package, self.target, self.importer, mapping)
        self.assertTrue(report['valid'], report)
        self.assertTrue(any('existing Editors' in warning for warning in report['warnings']))
        result = self.import_package(package, mapping)
        self.assertEqual(result['counts']['dashboards'], 0)
        self.assertEqual(models.Query.query.get(result['query_map'][str(self.query.id)]).data_source_id, target_source.id)

    def test_users_import_disabled_without_passwords_and_owner_remapped(self):
        package = self.export(['dashboards', 'sources', 'users'])
        self.assertNotIn('password_hash', str(package))
        result = self.import_package(package)
        owner = models.Query.query.get(result['query_map'][str(self.query.id)]).user
        self.assertEqual(owner.email, self.editor.email); self.assertNotEqual(owner.id, self.editor.id)
        self.assertTrue(owner.is_disabled); self.assertIsNone(owner.password_hash)
        self.assertNotEqual(owner.api_key, self.editor.api_key)
        for group in models.Group.query.filter_by(org_id=self.target.id, type='regular'):
            self.assertNotIn('admin', group.permissions); self.assertNotIn('super_admin', group.permissions)

    def test_user_email_conflict_blocks_without_partial_writes(self):
        package = self.export(['users'])
        self.factory.create_user(org=self.target, email=self.editor.email); models.db.session.commit()
        before = (models.User.query.count(), models.Group.query.count())
        with self.assertRaises(ValueError): self.import_package(package)
        self.assertEqual(before, (models.User.query.count(), models.Group.query.count()))

    def test_rules_without_users_clone_empty_groups_preserve_restrictions(self):
        group = self.factory.create_group(name='Finance rules')
        models.db.session.flush()
        self.source.type = 'mysql'; self.source.options = ConfigurationContainer({'host': 'unused', 'db': 'sales', 'user': 'fake', 'passwd': 'SECRET'})
        self.factory.org.settings['data_access_group_rules'] = {'revision': 1, 'policies': [{
            'id': 'finance-rule', 'name': 'Finance', 'data_source_id': self.source.id, 'status': 'active', 'default_effect': 'deny',
            'user_ids': [], 'group_ids': [group.id], 'rules': [{'catalog': None, 'schema': 'sales', 'table': 'orders', 'column_mode': 'explicit', 'columns': ['id']}]}]}
        models.db.session.add(models.DataSourceGroup(data_source=self.source, group=group, view_only=False))
        models.db.session.commit()
        package = self.export(['rules'])
        self.assertEqual(package['users'], []); self.assertEqual(package['queries'], [])
        result = self.import_package(package)
        config = self.target.settings['data_access_group_rules']
        policy = config['policies'][0]
        self.assertEqual(policy['rules'][0]['columns'], ['id'])
        self.assertNotEqual(policy['data_source_id'], self.source.id)
        self.assertEqual(policy['data_source_id'], result['source_map'][str(self.source.id)])
        self.assertEqual(models.User.query.filter(models.User.group_ids.any(policy['group_ids'][0])).count(), 0)
        self.assertEqual(models.DataSourceGroup.query.filter_by(data_source_id=policy['data_source_id']).count(),
            models.DataSourceGroup.query.filter_by(data_source_id=self.source.id).count())

    def test_groups_without_rules_do_not_restore_source_grants(self):
        package = self.export(['sources', 'groups', 'users'])
        self.assertEqual(package['source_groups'], [])
        result = self.import_package(package)
        for source_id in result['created_source_ids']:
            self.assertEqual(models.DataSourceGroup.query.filter_by(data_source_id=source_id).count(), 0)

    def test_malformed_refs_or_scope_options_block_before_writes(self):
        package = self.export(); package['widgets'][0]['visualization_id'] = 999999
        before = models.Query.query.count()
        self.assertFalse(inspect_content(package, self.target, self.importer)['valid'])
        with self.assertRaises(ValueError): self.import_package(package)
        self.assertEqual(models.Query.query.count(), before)
        package = self.export(); package['queries'][0]['options']['data_access_scope'] = 'FORGED'
        self.assertFalse(inspect_content(package, self.target, self.importer)['valid'])

    def test_history_preserves_snapshots_and_unknown_actor_without_users(self):
        self.query.query_text = 'SELECT 2 AS amount'; models.db.session.commit()
        package = self.export(['queries', 'sources', 'history'])
        expected = [r['snapshot']['query'] for r in package['history'] if r['query_id'] == self.query.id]
        result = self.import_package(package)
        revisions = models.QueryRevision.query.filter_by(query_id=result['query_map'][str(self.query.id)]).order_by(models.QueryRevision.revision).all()
        self.assertEqual([r.snapshot['query'] for r in revisions], expected)
        self.assertTrue(all(r.actor_user_id is None for r in revisions))

    def test_ai_secret_portability_with_new_destination_key(self):
        from redash.ai_settings import secret_cipher
        self.factory.org.settings['ai_context'] = 'Synthetic context'
        self.factory.org.settings['ai_configuration'] = {'provider': 'compatible', 'profiles': {'compatible': {
            'model': 'synthetic-model', 'base_url': 'https://example.invalid/v1',
            'encrypted_api_key': secret_cipher(settings.DATASOURCE_SECRET_KEY).encrypt(b'AI_SECRET').decode()}}}
        models.db.session.commit()
        package = self.export(['ai'], True)
        with patch.object(settings, 'DATASOURCE_SECRET_KEY', 'DIFFERENT_DESTINATION_KEY'):
            self.import_package(package)
            encrypted = self.target.settings['ai_configuration']['profiles']['compatible']['encrypted_api_key']
            self.assertEqual(secret_cipher('DIFFERENT_DESTINATION_KEY').decrypt(encrypted.encode()), b'AI_SECRET')
        self.assertEqual(self.target.settings['ai_context'], 'Synthetic context')

    def test_derived_connector_and_unknown_format_rejected(self):
        self.source.type = 'results'; models.db.session.commit()
        with self.assertRaises(ValueError): self.export()
        self.source.type = 'pg'; models.db.session.commit()
        package = self.export(); package['version'] = 999
        self.assertFalse(inspect_content(package, self.target, self.importer)['valid'])

    def test_atomic_savepoint_rolls_back_late_failure(self):
        package = self.export()
        before = (models.Query.query.count(), models.DataSource.query.count(), models.Dashboard.query.count())
        with patch('redash.content_migration._remap_query_options', side_effect=ValueError('Synthetic failure')):
            with self.assertRaises(ValueError): self.import_package(package)
        self.assertEqual(before, (models.Query.query.count(), models.DataSource.query.count(), models.Dashboard.query.count()))

    def test_dependencies_auto_selected_and_source_secrets_optional(self):
        self.assertEqual(normalize_components(['dashboards']), ['dashboards', 'queries'])
        self.assertEqual(normalize_components(['rules']), ['groups', 'rules', 'sources'])
        package = self.export(['sources'], True)
        self.assertEqual(package['sources'][0]['options']['password'], 'SOURCE_SECRET')

    def test_legacy_rules_export_and_import_block_without_silent_transition(self):
        self.factory.org.settings['data_access_protected_sources'] = [self.source.id]
        models.db.session.commit()
        with self.assertRaises(ValueError): self.export(['rules'])
        self.factory.org.settings['data_access_group_rules'] = {'revision': 0, 'policies': []}
        models.db.session.commit()
        package = self.export(['rules'])
        self.target.settings['data_access_protected_sources'] = [123]
        models.db.session.commit()
        self.assertFalse(inspect_content(package, self.target, self.importer)['valid'])

    def test_credential_uri_scrub_and_strict_references(self):
        from redash.content_migration import _safe_source_options
        self.source.options = ConfigurationContainer({'host': 'unused', 'connectionString': 'mongodb://u:p@host',
            'dsn': 'user=x password=y', 'url': 'https://u:p@example.invalid/path', 'password': 'SECRET'})
        with patch('redash.content_migration.get_configuration_schema_for_query_runner_type', return_value={'secret': ['password']}):
            result = _safe_source_options(self.source, False)
        self.assertEqual(result, {'host': 'unused'})
        package = self.export(); package['version'] = True
        self.assertFalse(inspect_content(package, self.target, self.importer)['valid'])
        package = self.export(); package['widgets'][0]['options']['queryId'] = 1.5
        self.assertFalse(inspect_content(package, self.target, self.importer)['valid'])

    def test_dropdown_and_embedded_widget_references_remap_without_results(self):
        from sqlalchemy import update
        dependency=self.factory.create_query(user=self.editor,data_source=self.source,is_draft=False,query_text='SELECT 9 AS id')
        models.db.session.flush()
        options={'parameters':[{'name':'lookup','type':'query','value':9,'queryId':str(dependency.id)}]}
        models.db.session.execute(update(models.Query.__table__).where(models.Query.id==self.query.id).values(options=options))
        self.widget.options={'parameterMappings':{'lookup':{'type':'query','queryId':str(dependency.id)}}}
        self.board.options={'nested':{'widgetId':self.widget.id}}
        models.db.session.commit();models.db.session.expire_all()
        package=self.export();result=self.import_package(package)
        query=models.Query.query.get(result['query_map'][str(self.query.id)])
        board=models.Dashboard.query.get(result['dashboard_map'][str(self.board.id)])
        dependency_id=result['query_map'][str(dependency.id)]
        self.assertNotEqual(dependency_id,dependency.id)
        self.assertEqual(query.options['parameters'][0]['queryId'],dependency_id)
        self.assertEqual(board.widgets.first().options['parameterMappings']['lookup']['queryId'],dependency_id)
        self.assertEqual(board.options['nested']['widgetId'],board.widgets.first().id)
        self.assertIsNone(models.Query.query.get(dependency_id).latest_query_data_id)

    def test_rules_selection_preserves_ordinary_source_group_access_without_members(self):
        package=self.export(['rules'])
        self.assertEqual(package['rules']['policies'],[])
        self.assertTrue(package['source_groups'])
        result=self.import_package(package)
        source_id=result['source_map'][str(self.source.id)]
        bindings=models.DataSourceGroup.query.filter_by(data_source_id=source_id).all()
        self.assertTrue(bindings)
        for binding in bindings:
            group=models.Group.query.get(binding.group_id)
            self.assertEqual(group.type,models.Group.REGULAR_GROUP)
            self.assertNotEqual(group.id,self.target.default_group.id)
            self.assertEqual(models.User.query.filter(models.User.group_ids.any(group.id)).count(),0)
