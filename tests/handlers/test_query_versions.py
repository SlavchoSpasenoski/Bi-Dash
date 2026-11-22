import json
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from sqlalchemy.engine import make_url
from redash import models
from redash.utils.configuration import ConfigurationContainer
from tests import BaseTestCase

class TestQueryVersions(BaseTestCase):
    def setUp(self):
        if not (make_url(os.environ.get('REDASH_DATABASE_URL','postgresql:///unset')).database or '').startswith('bidash_access_app_test'):
            self.skipTest('Disposable metadata database required')
        super().setUp()
        self.admin=self.factory.create_admin()
        self.editor=self.factory.create_user(details={'role':'editor'})
        self.reader=self.factory.create_user(details={'role':'reader'})
        self.foreign=self.factory.create_admin(org=self.factory.create_org())
        self.source=self.factory.data_source
        self.query=self.factory.create_query(user=self.editor,data_source=self.source,query_text='SELECT 1 AS amount',is_draft=False,
            options={'parameters':[],'data_access_scope':'SCOPE_SECRET','api_key':'OPTIONS_SECRET'})
        models.db.session.commit()

    def make_request(self,*args,**kwargs):
        from flask import g
        g.pop('org',None)
        return super().make_request(*args,**kwargs)

    def listing(self,user=None,params=''):
        return self.make_request('get',f'/api/queries/{self.query.id}/versions'+params,user=user or self.editor)

    def revisions(self):
        return models.QueryRevision.query.filter_by(query_id=self.query.id).order_by(models.QueryRevision.revision).all()

    def save(self,**changes):
        return self.make_request('post',f'/api/queries/{self.query.id}',user=self.editor,
            data={'version':self.query.version,**changes})

    def test_create_revision_safe_and_no_raw_change_or_query_key(self):
        r=self.listing();self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['count'],1)
        first=self.revisions()[0];self.assertEqual(first.kind,'created');self.assertEqual(first.revision,1)
        detail=self.make_request('get',f'/api/queries/{self.query.id}/versions/{first.id}',user=self.editor)
        self.assertEqual(detail.status_code,200,detail.json)
        for marker in ('SCOPE_SECRET','OPTIONS_SECRET',self.query.api_key,self.editor.api_key):
            self.assertNotIn(marker,detail.get_data(as_text=True))
        self.assertEqual(detail.headers['Cache-Control'],'private, no-store')
        self.assertEqual(detail.json['snapshot']['query'],'SELECT 1 AS amount')

    def test_material_save_noop_tags_and_publish(self):
        self.assertEqual(self.save(query='SELECT 2 AS amount').status_code,200)
        self.assertEqual(len(self.revisions()),2)
        models.db.session.refresh(self.query)
        self.assertEqual(self.save(query='SELECT 2 AS amount').status_code,200)
        self.assertEqual(len(self.revisions()),2)
        self.assertEqual(self.save(tags=['Folder-like label'],is_draft=False).status_code,200)
        self.assertEqual(len(self.revisions()),2)

    def test_metadata_result_updates_do_not_add_revision(self):
        self.query.latest_query_data=self.factory.create_query_result(data_source=self.source)
        self.query.tags=['tag'];models.db.session.commit()
        self.assertEqual(len(self.revisions()),1)

    def test_stale_save_conflict_does_not_create_revision(self):
        version=self.query.version
        self.assertEqual(self.save(query='SELECT 2').status_code,200)
        response=self.make_request('post',f'/api/queries/{self.query.id}',user=self.editor,data={'version':version,'query':'SELECT 3'})
        self.assertEqual(response.status_code,409);self.assertEqual(len(self.revisions()),2)

    def test_reader_foreign_and_personal_api_denied(self):
        self.assertNotEqual(self.listing(self.reader).status_code,200)
        self.assertNotEqual(self.listing(self.foreign).status_code,200)
        client=self.app.test_client();prefix='/'+self.factory.org.slug
        for key in (self.editor.api_key,self.admin.api_key,self.query.api_key):
            self.assertNotEqual(client.get(prefix+f'/api/queries/{self.query.id}/versions?api_key='+key).status_code,200)

    def test_comparison_safe_and_other_query_revision_not_readable(self):
        self.save(query='SELECT 2');old,new=self.revisions()
        path=f'/api/queries/{self.query.id}/versions/{new.id}'
        r=self.make_request('get',path+f'?compare_to={old.id}',user=self.editor)
        self.assertEqual(r.status_code,200);self.assertEqual(r.json['comparison']['snapshot']['query'],'SELECT 1 AS amount')
        other=self.factory.create_query(user=self.editor,data_source=self.source);models.db.session.commit()
        foreign_revision=models.QueryRevision.query.filter_by(query_id=other.id).first()
        self.assertEqual(self.make_request('get',path+f'?compare_to={foreign_revision.id}',user=self.editor).status_code,404)

    def test_options_defaults_preserve_enum_list_and_drop_private_fields(self):
        self.query.options={'parameters':[{'name':'status','type':'enum','value':'A','enumOptions':['A','B'],'private':'HIDDEN'}],
            'apply_auto_limit':True,'api_key':'SECRET'}
        models.db.session.commit();s=self.revisions()[-1].snapshot
        self.assertEqual(s['options']['parameters'][0]['enumOptions'],['A','B'])
        self.assertNotIn('HIDDEN',json.dumps(s));self.assertNotIn('SECRET',json.dumps(s))

    def test_rollback_discards_query_and_revision(self):
        self.query.query_text='SELECT 900';models.db.session.flush();models.db.session.rollback()
        self.assertEqual(len(self.revisions()),1);self.assertEqual(self.query.query_text,'SELECT 1 AS amount')

    def test_immutable_revisions(self):
        revision=self.revisions()[0];revision.kind='update'
        with self.assertRaises(ValueError):models.db.session.commit()
        models.db.session.rollback()

    def test_pagination(self):
        self.save(query='SELECT 2');self.save(query='SELECT 3')
        r=self.listing(params='?page=2&page_size=1');self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['count'],3);self.assertEqual(r.json['results'][0]['version'],2)
        self.assertEqual(self.listing(params='?page_size=101').status_code,400)

    def test_fork_creates_independent_history(self):
        r=self.make_request('post',f'/api/queries/{self.query.id}/fork',user=self.editor)
        self.assertEqual(r.status_code,200,r.json)
        revisions=models.QueryRevision.query.filter_by(query_id=r.json['id']).all()
        self.assertGreaterEqual(len(revisions),1);self.assertEqual(revisions[0].revision,1)
        self.assertEqual(len(self.revisions()),1)

    def test_protected_old_columns_hidden_before_count_and_detail(self):
        self.source.type='mysql';self.source.options=ConfigurationContainer({'db':'history','host':'unused','user':'fake','passwd':'private'})
        group=self.factory.create_group(name='History policy');models.db.session.flush()
        self.editor.group_ids=self.editor.group_ids+[group.id]
        policy={'id':'history-policy','name':'History','data_source_id':self.source.id,'status':'active','default_effect':'deny',
            'user_ids':[],'group_ids':[group.id],'rules':[{'catalog':None,'schema':'history','table':'orders','column_mode':'explicit','columns':['id','secret']}]}
        self.factory.org.settings['data_access_group_rules']={'revision':1,'policies':[policy]}
        self.factory.org.settings['data_access_protected_sources']=[self.source.id]
        self.query.query_text='SELECT secret FROM orders';models.db.session.commit();denied=self.revisions()[-1].id
        self.query.query_text='SELECT id FROM orders';models.db.session.commit()
        import copy
        config=copy.deepcopy(self.factory.org.settings['data_access_group_rules']);config['policies'][0]['rules'][0]['columns']=['id']
        self.factory.org.settings['data_access_group_rules']=config;models.db.session.commit()
        with patch('redash.data_access_mysql._connect',side_effect=AssertionError('History must not connect to source')):
            r=self.listing();self.assertEqual(r.status_code,200,r.json)
            self.assertEqual(r.json['count'],2,r.json)
            self.assertNotIn(denied,[row['id'] for row in r.json['results']])
            visible=self.revisions()[-1].id
            self.assertEqual(self.make_request('get',f'/api/queries/{self.query.id}/versions/{visible}?compare_to={denied}',user=self.editor).status_code,404)
            self.assertEqual(self.make_request('get',f'/api/queries/{self.query.id}/versions/{denied}',user=self.editor).status_code,404)

    def test_concurrent_save_one_winner(self):
        version=self.query.version;ident=self.query.id;org=self.factory.org.slug;user_id=self.editor.get_id();app=self.app
        def save(n):
            client=app.test_client()
            with client.session_transaction() as s:s['_user_id']=user_id;s['_fresh']=True
            return client.post(f'/{org}/api/queries/{ident}',json={'version':version,'query':f'SELECT {n}'}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(save,[200,300]))
        self.assertEqual(sorted(statuses),[200,409]);models.db.session.expire_all();self.assertEqual(len(self.revisions()),2)

    def test_ai_style_creation_and_new_query_rollback_are_atomic(self):
        before=models.QueryRevision.query.count()
        created=models.Query.create(user=self.editor,org=self.factory.org,data_source=self.source,
            name='Synthetic AI draft',query_text='SELECT 42',options={'ai_generated':True,'data_access_scope':'INTERNAL_SCOPE'})
        models.db.session.flush()
        models.db.session.rollback()
        self.assertEqual(models.QueryRevision.query.count(),before)
        created=models.Query.create(user=self.editor,org=self.factory.org,data_source=self.source,
            name='Synthetic AI draft',query_text='SELECT 42',options={'ai_generated':True,'data_access_scope':'INTERNAL_SCOPE'})
        models.db.session.commit()
        rows=models.QueryRevision.query.filter_by(query_id=created.id).all()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].kind,'created')
        self.assertNotIn('INTERNAL_SCOPE',json.dumps(rows[0].snapshot))

    def test_revision_actor_is_saver_and_never_full_user_record(self):
        r=self.make_request('post',f'/api/queries/{self.query.id}',user=self.admin,data={'query':'SELECT 77','version':self.query.version})
        self.assertEqual(r.status_code,200,r.json)
        listed=self.listing(self.admin).json['results'][0]
        self.assertEqual(listed['author'],{'id':self.admin.id,'name':self.admin.name})
        self.assertEqual(set(listed['author']),{'id','name'})
