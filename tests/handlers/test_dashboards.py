from redash.models import AccessPermission, ApiKey, Dashboard, DashboardReadGrant, db
from redash.permissions import ACCESS_TYPE_MODIFY
from redash.serializers import serialize_dashboard
from redash.utils import json_loads
from tests import BaseTestCase


class TestDashboardListResource(BaseTestCase):
    def test_create_new_dashboard(self):
        dashboard_name = "Test Dashboard"
        rv = self.make_request("post", "/api/dashboards", data={"name": dashboard_name})
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.json["name"], "Test Dashboard")
        self.assertEqual(rv.json["user_id"], self.factory.user.id)
        self.assertEqual(rv.json["layout"], [])


class TestDashboardListGetResource(BaseTestCase):
    def test_admin_list_search_tags_and_favorites_include_other_owners_drafts(self):
        admin=self.factory.create_admin()
        owner=self.factory.create_user()
        draft=self.factory.create_dashboard(user=owner,name="Admin visibility draft",is_draft=True,tags=["private-draft"])
        empty=self.factory.create_dashboard(user=owner,name="Admin visibility empty")
        archived=self.factory.create_dashboard(user=owner,name="Admin visibility archived",is_archived=True)
        foreign=self.factory.create_dashboard(org=self.factory.create_org(),name="Admin visibility foreign")
        db.session.commit()
        from redash.models import Favorite
        db.session.add(Favorite(user=admin,object=draft,org_id=self.factory.org.id))
        db.session.commit()
        for path in ("/api/dashboards","/api/dashboards?q=Admin%20visibility"):
            response=self.make_request("get",path,user=admin)
            self.assertEqual(response.status_code,200,response.get_data()[:200])
            ids={item["id"] for item in response.json["results"]}
            self.assertTrue({draft.id,empty.id}.issubset(ids))
            self.assertNotIn(archived.id,ids)
            self.assertNotIn(foreign.id,ids)
        response=self.make_request("get","/api/dashboards/tags",user=admin)
        self.assertIn("private-draft",[tag["name"] for tag in response.json["tags"]])
        for path in ("/api/dashboards/favorites","/api/dashboards/favorites?q=Admin%20visibility"):
            response=self.make_request("get",path,user=admin)
            self.assertEqual(response.status_code,200,response.get_data()[:200])
            self.assertIn(draft.id,[item["id"] for item in response.json["results"]])
        outsider=self.factory.create_user(details={"role":"editor"})
        response=self.make_request("get","/api/dashboards?include_all=true",user=outsider)
        self.assertNotIn(draft.id,[item["id"] for item in response.json["results"]])

    def test_returns_dashboards(self):
        d1 = self.factory.create_dashboard()
        d2 = self.factory.create_dashboard()
        d3 = self.factory.create_dashboard()

        rv = self.make_request("get", "/api/dashboards")

        assert len(rv.json["results"]) == 3
        assert set([result["id"] for result in rv.json["results"]]) == set([d1.id, d2.id, d3.id])

    def test_filters_with_tags(self):
        d1 = self.factory.create_dashboard(tags=["test"])
        self.factory.create_dashboard()
        self.factory.create_dashboard()

        rv = self.make_request("get", "/api/dashboards?tags=test")
        assert len(rv.json["results"]) == 1
        assert set([result["id"] for result in rv.json["results"]]) == set([d1.id])

    def test_search_term(self):
        d1 = self.factory.create_dashboard(name="Sales")
        d2 = self.factory.create_dashboard(name="Q1 sales")
        self.factory.create_dashboard(name="Ops")

        rv = self.make_request("get", "/api/dashboards?q=sales")
        assert len(rv.json["results"]) == 2
        assert set([result["id"] for result in rv.json["results"]]) == set([d1.id, d2.id])


class TestDashboardResourceGet(BaseTestCase):
    def test_get_dashboard(self):
        d1 = self.factory.create_dashboard()
        rv = self.make_request("get", "/api/dashboards/{0}".format(d1.id))
        self.assertEqual(rv.status_code, 200)

        expected = serialize_dashboard(d1, with_widgets=True, with_favorite_state=False)
        actual = json_loads(rv.data)

        self.assertResponseEqual(expected, actual)

    def test_named_viewers_never_receive_legacy_public_tokens(self):
        self.factory.org.settings["access_policy"] = {"editor": {"allow_public_sharing": True}}
        d1 = self.factory.create_dashboard(is_draft=False)
        ApiKey.create_for_object(d1, self.factory.user)
        db.session.commit()

        rv = self.make_request("get", "/api/dashboards/{0}".format(d1.id))
        self.assertEqual(rv.status_code, 200)
        self.assertNotIn("public_url", rv.json)
        self.assertNotIn("api_key", rv.json)

        viewer = self.factory.create_user()
        db.session.add(DashboardReadGrant(dashboard_id=d1.id, user_id=viewer.id, grantor_id=d1.user_id))
        db.session.commit()
        rv = self.make_request("get", "/api/dashboards/{0}".format(d1.id), user=viewer)
        self.assertEqual(rv.status_code, 200)
        self.assertNotIn("public_url", rv.json)
        self.assertNotIn("api_key", rv.json)

    def test_get_dashboard_with_slug(self):
        d1 = self.factory.create_dashboard()
        rv = self.make_request("get", "/api/dashboards/{0}?legacy".format(d1.slug))
        self.assertEqual(rv.status_code, 200)

        expected = serialize_dashboard(d1, with_widgets=True, with_favorite_state=False)
        actual = json_loads(rv.data)

        self.assertResponseEqual(expected, actual)

    def test_get_dashboard_filters_unauthorized_widgets(self):
        dashboard = self.factory.create_dashboard()

        restricted_ds = self.factory.create_data_source(group=self.factory.create_group())
        query = self.factory.create_query(data_source=restricted_ds)
        vis = self.factory.create_visualization(query_rel=query)
        restricted_widget = self.factory.create_widget(visualization=vis, dashboard=dashboard)
        widget = self.factory.create_widget(dashboard=dashboard)
        dashboard.layout = [[widget.id, restricted_widget.id]]
        db.session.commit()

        rv = self.make_request("get", "/api/dashboards/{0}".format(dashboard.id))
        self.assertEqual(rv.status_code, 200)
        self.assertTrue(rv.json["widgets"][0]["restricted"])
        self.assertNotIn("restricted", rv.json["widgets"][1])

    def test_get_non_existing_dashboard(self):
        rv = self.make_request("get", "/api/dashboards/-1")
        self.assertEqual(rv.status_code, 404)


class TestDashboardResourcePost(BaseTestCase):
    def test_update_dashboard(self):
        d = self.factory.create_dashboard()
        new_name = "New Name"
        rv = self.make_request(
            "post",
            "/api/dashboards/{0}".format(d.id),
            data={"name": new_name, "layout": []},
        )
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.json["name"], new_name)

    def test_raises_error_in_case_of_conflict(self):
        d = self.factory.create_dashboard()
        d.name = "Updated"
        db.session.commit()
        new_name = "New Name"
        rv = self.make_request(
            "post",
            "/api/dashboards/{0}".format(d.id),
            data={"name": new_name, "layout": [], "version": d.version - 1},
        )

        self.assertEqual(rv.status_code, 409)

    def test_overrides_existing_if_no_version_specified(self):
        d = self.factory.create_dashboard()
        d.name = "Updated"

        new_name = "New Name"
        rv = self.make_request(
            "post",
            "/api/dashboards/{0}".format(d.id),
            data={"name": new_name, "layout": []},
        )

        self.assertEqual(rv.status_code, 200)

    def test_legacy_modify_grant_does_not_override_content_owner(self):
        d = self.factory.create_dashboard()
        user = self.factory.create_user()

        new_name = "New Name"
        rv = self.make_request(
            "post",
            "/api/dashboards/{0}".format(d.id),
            data={"name": new_name, "layout": [], "version": d.version},
            user=user,
        )
        self.assertEqual(rv.status_code, 403)

        AccessPermission.grant(obj=d, access_type=ACCESS_TYPE_MODIFY, grantee=user, grantor=d.user)

        rv = self.make_request(
            "post",
            "/api/dashboards/{0}".format(d.id),
            data={"name": new_name, "layout": [], "version": d.version},
            user=user,
        )

        self.assertEqual(rv.status_code, 403)
        self.assertNotEqual(d.name, new_name)


class TestDashboardForkResourcePost(BaseTestCase):
    def test_forks_a_dashboard(self):
        dashboard = self.factory.create_dashboard()

        rv = self.make_request("post", "/api/dashboards/{}/fork".format(dashboard.id))

        self.assertEqual(rv.status_code, 200)

    def test_returns_403_when_user_cannot_access_a_widget_query(self):
        data_source = self.factory.create_data_source(group=self.factory.create_group())
        query = self.factory.create_query(data_source=data_source)
        visualization = self.factory.create_visualization(query_rel=query)
        widget = self.factory.create_widget(visualization=visualization)
        other_user = self.factory.create_user()

        rv = self.make_request("post", "/api/dashboards/{}/fork".format(widget.dashboard.id), user=other_user)

        self.assertEqual(rv.status_code, 403)


class TestDashboardResourceDelete(BaseTestCase):
    def test_delete_dashboard(self):
        self.factory.org.settings["access_policy"] = {"editor": {"allow_delete": True}}
        d = self.factory.create_dashboard()

        rv = self.make_request("delete", "/api/dashboards/{0}".format(d.id))
        self.assertEqual(rv.status_code, 200)

        d = Dashboard.get_by_id_and_org(d.id, d.org)
        self.assertTrue(d.is_archived)

    def test_returns_403_for_non_owner(self):
        d = self.factory.create_dashboard()
        other_user = self.factory.create_user()

        rv = self.make_request("delete", "/api/dashboards/{0}".format(d.id), user=other_user)
        self.assertEqual(rv.status_code, 403)

        d = Dashboard.get_by_id_and_org(d.id, d.org)
        self.assertFalse(d.is_archived)


class TestDashboardShareResourcePost(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.factory.org.settings["access_policy"] = {"editor": {"allow_public_sharing": True}}

    def test_anonymous_key_creation_is_disabled(self):
        dashboard = self.factory.create_dashboard()

        res = self.make_request("post", "/api/dashboards/{}/share".format(dashboard.id))
        self.assertEqual(res.status_code, 403)
        self.assertIsNone(ApiKey.get_by_object(dashboard))

    def test_requires_admin_or_owner(self):
        dashboard = self.factory.create_dashboard()
        user = self.factory.create_user()

        res = self.make_request("post", "/api/dashboards/{}/share".format(dashboard.id), user=user)
        self.assertEqual(res.status_code, 403)

        user.group_ids.append(self.factory.org.admin_group.id)

        res = self.make_request("post", "/api/dashboards/{}/share".format(dashboard.id), user=user)
        self.assertEqual(res.status_code, 403)


class TestDashboardShareResourceDelete(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.factory.org.settings["access_policy"] = {"editor": {"allow_public_sharing": True}}

    def test_disables_api_key(self):
        dashboard = self.factory.create_dashboard()
        ApiKey.create_for_object(dashboard, self.factory.user)

        res = self.make_request("delete", "/api/dashboards/{}/share".format(dashboard.id))
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(ApiKey.get_by_object(dashboard))

    def test_ignores_when_no_api_key_exists(self):
        dashboard = self.factory.create_dashboard()

        res = self.make_request("delete", "/api/dashboards/{}/share".format(dashboard.id))
        self.assertEqual(res.status_code, 200)

    def test_requires_admin_or_owner(self):
        dashboard = self.factory.create_dashboard()
        user = self.factory.create_user()

        res = self.make_request("delete", "/api/dashboards/{}/share".format(dashboard.id), user=user)
        self.assertEqual(res.status_code, 403)

        user.group_ids.append(self.factory.org.admin_group.id)

        res = self.make_request("delete", "/api/dashboards/{}/share".format(dashboard.id), user=user)
        self.assertEqual(res.status_code, 200)
