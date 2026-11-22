"""Portable package/API security; disposable metadata only."""
import copy
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from sqlalchemy.engine import make_url
from flask import g
from redash import models
from redash.migration_package import seal, unseal
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase

PASSWORD = 'synthetic-package-passphrase-2026'


class TestContentMigration(BaseTestCase):
    def setUp(self):
        if not (make_url(os.environ.get('REDASH_DATABASE_URL', 'postgresql:///unset')).database or '').startswith('bidash_access_app_test'):
            self.skipTest('Disposable metadata required')
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

    def call(self, action, data, user=None, csrf=True, org=None, api_key=None):
        user = user or self.admin
        org = org or user.org
        g.pop('org', None)
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = user.get_id(); session['_fresh'] = True
        g.pop('csrf_token', None)
        client.get('/ping')
        headers = {}
        if csrf:
            cookie = client.get_cookie('csrf_token')
            headers['X-CSRF-TOKEN'] = cookie.value
        g.pop('org', None)
        return client.post('/' + org.slug + '/api/admin/migration/' + action + ('?api_key=' + api_key if api_key else ''), json=data, headers=headers)

    def exported(self, components=None, credentials=False):
        response = self.call('export', {'components': components or ['queries', 'dashboards', 'sources'],
            'include_credentials': credentials, 'passphrase': PASSWORD})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        return response.json['package']

    def preview(self, envelope, mapping=None, user=None):
        body = {'package': envelope, 'passphrase': PASSWORD, 'source_mapping': mapping or {}}
        response = self.call('inspect', body, user=user or self.importer)
        self.assertEqual(response.status_code, 200, response.json)
        return response, body

    def apply(self, envelope, mapping=None):
        response, body = self.preview(envelope, mapping)
        self.assertFalse(response.json.get('errors'), response.json)
        body['preview_token'] = response.json['preview_token']
        return self.call('import', body, user=self.importer), body

    def test_default_roundtrip_admin_ownership_ids_and_no_automatic_access(self):
        envelope = self.exported()
        plaintext = unseal(envelope, PASSWORD)
        for marker in ('PRIVATE_SCOPE', self.query.api_key, self.editor.api_key, 'PRIVATE_SOURCE_PASSWORD'):
            self.assertNotIn(marker, str(plaintext))
            self.assertNotIn(marker, str(envelope))
        response, body = self.apply(envelope)
        self.assertEqual(response.status_code, 200, response.json)
        query = models.Query.query.filter_by(org_id=self.target.id, name=self.query.name).one()
        board = models.Dashboard.query.filter_by(org_id=self.target.id, name=self.board.name).one()
        self.assertNotEqual(query.id, self.query.id)
        self.assertEqual(query.user_id, self.importer.id); self.assertEqual(board.user_id, self.importer.id)
        self.assertIsNone(query.schedule); self.assertIsNone(query.latest_query_data_id)
        self.assertNotEqual(query.api_key, self.query.api_key)
        self.assertEqual(models.User.query.filter_by(org_id=self.target.id).count(), 1)
        self.assertEqual(models.DashboardReadGrant.query.filter_by(dashboard_id=board.id).count(), 0)
        source = models.DataSource.query.filter_by(org_id=self.target.id).one()
        self.assertEqual(models.DataSourceGroup.query.filter_by(data_source_id=source.id).count(), 0)
        widget = models.Widget.query.filter_by(dashboard_id=board.id).one()
        self.assertEqual(widget.visualization.query_id, query.id)
        self.assertEqual(board.layout, [[widget.id]])
        listed = self.make_request('get', '/api/queries', org=self.target, user=self.importer)
        self.assertEqual(listed.status_code,200,listed.json)
        self.assertIn(query.id,[row['id'] for row in listed.json['results']])
        self.assertEqual(models.MigrationRun.query.filter_by(org_id=self.target.id).count(), 1)
        replay = self.call('import', body, user=self.importer)
        self.assertIn(replay.status_code, (400, 409))
        self.assertEqual(models.Query.query.filter_by(org_id=self.target.id).count(), 1)

    def test_credentials_encrypted_and_recreated_under_target_settings(self):
        envelope = self.exported(credentials=True)
        self.assertNotIn('PRIVATE_SOURCE_PASSWORD', str(envelope))
        response, _ = self.apply(envelope)
        self.assertEqual(response.status_code, 200, response.json)
        source = models.DataSource.query.filter_by(org_id=self.target.id).one()
        self.assertEqual(source.options.to_dict()['password'], 'PRIVATE_SOURCE_PASSWORD')

    def test_queries_without_sources_require_explicit_same_type_mapping(self):
        envelope = self.exported(['queries'])
        preview, _ = self.preview(envelope)
        self.assertTrue(preview.json['errors']); self.assertNotIn('preview_token', preview.json)
        source = self.factory.create_data_source(org=self.target, type='pg', name='Mapped target')
        models.db.session.commit()
        response, _ = self.apply(envelope, {str(self.source.id): source.id})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(models.Query.query.filter_by(org_id=self.target.id).one().data_source_id, source.id)

    def test_wrong_password_tamper_and_bad_format_write_nothing(self):
        envelope = self.exported()
        for package, password in ((envelope, 'wrong-long-passphrase'), ({**envelope, 'ciphertext': envelope['ciphertext'][:-3]+'xxx'}, PASSWORD),
                                  ({**envelope, 'version': 999}, PASSWORD)):
            response = self.call('inspect', {'package': package, 'passphrase': password}, user=self.importer)
            self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(models.Query.query.filter_by(org_id=self.target.id).count(), 0)

    def test_admin_browser_and_csrf_required_for_every_action(self):
        for action in ('export', 'inspect', 'import'):
            for user in (self.editor, self.reader):
                self.assertEqual(self.call(action, {}, user=user).status_code, 403)
            self.assertEqual(self.call(action, {}, csrf=False).status_code, 400)
            self.assertNotEqual(self.call(action, {}, api_key=self.admin.api_key).status_code, 200)
            self.assertNotEqual(self.call(action, {}, user=self.importer, org=self.factory.org).status_code, 200)

    def test_import_requires_matching_unchanged_preview(self):
        envelope = self.exported()
        response, body = self.preview(envelope)
        self.assertEqual(self.call('import', body, user=self.importer).status_code, 400)
        body['preview_token'] = response.json['preview_token']
        changed = {**body, 'source_mapping': {'999': 999}}
        self.assertEqual(self.call('import', changed, user=self.importer).status_code, 409)
        self.importer.name = 'Changed destination user'; models.db.session.commit()
        self.assertEqual(self.call('import', body, user=self.importer).status_code, 409)
        self.assertEqual(models.Query.query.filter_by(org_id=self.target.id).count(), 0)

    def test_malformed_dependencies_abort_without_partial_writes(self):
        envelope = self.exported()
        content = unseal(envelope, PASSWORD)
        content['version'] = 999
        broken = seal(content, PASSWORD)
        response = self.call('inspect', {'package': broken, 'passphrase': PASSWORD}, user=self.importer)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['errors'])
        self.assertNotIn('preview_token', response.json)
        self.assertEqual(models.MigrationRun.query.filter_by(org_id=self.target.id).count(), 0)

    def test_failed_import_rolls_back_everything_and_receipt(self):
        envelope = self.exported()
        response, body = self.preview(envelope)
        body['preview_token'] = response.json['preview_token']
        def fail(*args):
            models.db.session.add(models.Dashboard(org=self.target, user=self.importer, name='Must roll back', layout=[]))
            models.db.session.flush()
            raise ValueError('Synthetic import failure')
        with patch('redash.content_migration.import_content', side_effect=fail):
            result = self.call('import', body, user=self.importer)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(models.Dashboard.query.filter_by(org_id=self.target.id).count(), 0)
        self.assertEqual(models.MigrationRun.query.count(), 0)

    def test_unsupported_components_and_weak_passphrase(self):
        for body in ({'components': ['unknown'], 'passphrase': PASSWORD},
                     {'components': ['queries'], 'passphrase': 'short'}, []):
            self.assertEqual(self.call('export', body).status_code, 400)

    def test_invalid_component_payloads_are_preview_errors_not_server_errors(self):
        envelope = self.exported()
        for components in (None, 12, {'queries':True}, [True]):
            content = unseal(envelope, PASSWORD); content['components'] = components
            response = self.call('inspect', {'package':seal(content,PASSWORD),'passphrase':PASSWORD}, user=self.importer)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertTrue(response.json['errors']); self.assertNotIn('preview_token', response.json)

    def test_foreign_source_mapping_is_rejected(self):
        envelope = self.exported(['queries'])
        response, _ = self.preview(envelope, {str(self.source.id):self.source.id})
        self.assertTrue(response.json['errors']); self.assertNotIn('preview_token', response.json)

    def test_preview_expiry_and_different_actor_are_rejected(self):
        envelope = self.exported(); response, body = self.preview(envelope)
        body['preview_token'] = response.json['preview_token']
        from itsdangerous import SignatureExpired
        with patch('redash.handlers.content_migration.MigrationBase.signer') as signer:
            signer.return_value.loads.side_effect = SignatureExpired('expired')
            self.assertEqual(self.call('import',body,user=self.importer).status_code,400)
        other = self.factory.create_admin(org=self.target); models.db.session.commit()
        self.assertEqual(self.call('import',body,user=other).status_code,409)

    def test_simultaneous_import_commits_once(self):
        envelope = self.exported(); response, body = self.preview(envelope)
        body['preview_token'] = response.json['preview_token']
        ident=self.importer.get_id(); slug=self.target.slug; app=self.app
        def apply(_):
            client=app.test_client()
            with client.session_transaction() as session:session['_user_id']=ident;session['_fresh']=True
            client.get('/ping');cookie=client.get_cookie('csrf_token')
            return client.post('/'+slug+'/api/admin/migration/import', json=body,
                headers={'X-CSRF-TOKEN':cookie.value}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(apply,[1,2]))
        self.assertEqual(sorted(statuses),[200,409])
        models.db.session.expire_all()
        self.assertEqual(models.MigrationRun.query.filter_by(org_id=self.target.id).count(),1)
        self.assertEqual(models.Query.query.filter_by(org_id=self.target.id).count(),1)

    def test_invalid_unicode_is_rejected_before_database_writes(self):
        envelope = self.exported(); content=unseal(envelope,PASSWORD)
        content['queries'][0]['query']='SELECT '+chr(0xd800)
        response=self.call('inspect',{'package':seal(content,PASSWORD),'passphrase':PASSWORD},user=self.importer)
        self.assertEqual(response.status_code,200,response.json)
        self.assertTrue(response.json['errors']);self.assertNotIn('preview_token',response.json)
        self.assertEqual(models.Query.query.filter_by(org_id=self.target.id).count(),0)

    def test_isolated_source_query_listing_admin_only_and_org_scoped(self):
        source=self.factory.create_data_source(org=self.target,type='pg',name='Isolated source')
        query=self.factory.create_query(org=self.target,user=self.importer,data_source=source,is_draft=True)
        other=self.factory.create_user(org=self.target,details={'role':'editor'})
        models.db.session.commit()
        self.assertIn(query.id,[q.id for q in models.Query.all_queries(self.importer.group_ids,self.importer.id,include_drafts=True)])
        self.assertNotIn(query.id,[q.id for q in models.Query.all_queries(other.group_ids,other.id,include_drafts=True)])
        self.assertNotIn(query.id,[q.id for q in models.Query.all_queries(self.admin.group_ids,self.admin.id,include_drafts=True)])

    def test_admin_listing_keeps_retired_sources_hidden(self):
        self.factory.org.settings['data_access_internal_sources']=[self.source.id]
        models.db.session.commit()
        listed=models.Query.all_queries(self.admin.group_ids,self.admin.id,include_drafts=True)
        self.assertNotIn(self.query.id,[q.id for q in listed])

    def test_optional_rules_without_users_restore_only_empty_cloned_group_bindings(self):
        envelope=self.exported(['rules'])
        response,_=self.apply(envelope)
        self.assertEqual(response.status_code,200,response.json)
        source=models.DataSource.query.filter_by(org_id=self.target.id).one()
        bindings=models.DataSourceGroup.query.filter_by(data_source_id=source.id).all()
        self.assertTrue(bindings)
        for binding in bindings:
            group=models.Group.query.get(binding.group_id)
            self.assertEqual(group.org_id,self.target.id)
            self.assertEqual(group.type,models.Group.REGULAR_GROUP)
            self.assertNotEqual(group.id,self.target.default_group.id)
            self.assertEqual(models.User.query.filter(models.User.group_ids.any(group.id)).count(),0)
        self.assertEqual(models.User.query.filter_by(org_id=self.target.id).count(),1)
