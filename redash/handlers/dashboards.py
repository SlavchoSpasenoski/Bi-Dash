from flask import request, session
from flask_restful import abort
from funcy import partial, project
from sqlalchemy.orm.exc import StaleDataError

from redash import models
from redash.handlers.base import (
    BaseResource,
    filter_by_tags,
    get_object_or_404,
    paginate,
)
from redash.handlers.base import order_results as _order_results
from redash.permissions import (
    require_access,
    require_permission,
    view_only,
)
from redash.security import csp_allows_embeding, csrf
from redash.serializers import DashboardSerializer
from redash.dashboard_access import (
    can_edit_dashboard, can_manage_dashboard_access,
    require_dashboard_edit, require_dashboard_view,
)

# Ordering map for relationships
order_map = {
    "name": "lowercase_name",
    "-name": "-lowercase_name",
    "created_at": "created_at",
    "-created_at": "-created_at",
    "starred_at": "favorites-created_at",
    "-starred_at": "-favorites-created_at",
}

order_results = partial(_order_results, default_order="-created_at", allowed_orders=order_map)


class DashboardListResource(BaseResource):
    @require_permission("list_dashboards")
    def get(self):
        """
        Lists all accessible dashboards.

        :qparam number page_size: Number of queries to return per page
        :qparam number page: Page number to retrieve
        :qparam number order: Name of column to order by
        :qparam number q: Full text search term

        Responds with an array of :ref:`dashboard <dashboard-response-label>`
        objects.
        """
        search_term = request.args.get("q")

        if search_term:
            results = models.Dashboard.search(
                self.current_org,
                self.current_user.group_ids,
                self.current_user.id,
                search_term,
                include_all=self.current_user.has_permission("admin"),
            )
        else:
            results = models.Dashboard.all(self.current_org, self.current_user.group_ids, self.current_user.id,
                                          include_all=self.current_user.has_permission("admin"))

        results = filter_by_tags(results, models.Dashboard.tags)

        # order results according to passed order parameter,
        # special-casing search queries where the database
        # provides an order by search rank
        ordered_results = order_results(results, fallback=not bool(search_term))

        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", 25, type=int)

        response = paginate(
            ordered_results,
            page=page,
            page_size=page_size,
            serializer=DashboardSerializer,
        )

        if search_term:
            self.record_event({"action": "search", "object_type": "dashboard", "term": search_term})
        else:
            self.record_event({"action": "list", "object_type": "dashboard"})

        return response

    @require_permission("create_dashboard")
    def post(self):
        """
        Creates a new dashboard.

        :<json string name: Dashboard name

        Responds with a :ref:`dashboard <dashboard-response-label>`.
        """
        dashboard_properties = request.get_json(force=True)
        dashboard = models.Dashboard(
            name=dashboard_properties["name"],
            org=self.current_org,
            user=self.current_user,
            is_draft=True,
            layout=[],
        )
        models.db.session.add(dashboard)
        models.db.session.commit()
        return DashboardSerializer(dashboard).serialize()


class MyDashboardsResource(BaseResource):
    @require_permission("list_dashboards")
    def get(self):
        """
        Retrieve a list of dashboards created by the current user.

        :qparam number page_size: Number of dashboards to return per page
        :qparam number page: Page number to retrieve
        :qparam number order: Name of column to order by
        :qparam number search: Full text search term

        Responds with an array of :ref:`dashboard <dashboard-response-label>`
        objects.
        """
        search_term = request.args.get("q", "")
        if search_term:
            results = models.Dashboard.search_by_user(search_term, self.current_user)
        else:
            results = models.Dashboard.by_user(self.current_user)

        results = filter_by_tags(results, models.Dashboard.tags)

        # order results according to passed order parameter,
        # special-casing search queries where the database
        # provides an order by search rank
        ordered_results = order_results(results, fallback=not bool(search_term))

        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", 25, type=int)
        return paginate(ordered_results, page, page_size, DashboardSerializer)


class DashboardResource(BaseResource):
    @require_permission("list_dashboards")
    def get(self, dashboard_id=None):
        """
        Retrieves a dashboard.

        :qparam number id: Id of dashboard to retrieve.

        .. _dashboard-response-label:

        :>json number id: Dashboard ID
        :>json string name:
        :>json string slug:
        :>json number user_id: ID of the dashboard creator
        :>json string created_at: ISO format timestamp for dashboard creation
        :>json string updated_at: ISO format timestamp for last dashboard modification
        :>json number version: Revision number of dashboard
        :>json boolean dashboard_filters_enabled: Whether filters are enabled or not
        :>json boolean is_archived: Whether this dashboard has been removed from the index or not
        :>json boolean is_draft: Whether this dashboard is a draft or not.
        :>json array layout: Array of arrays containing widget IDs, corresponding to the rows and columns the widgets are displayed in
        :>json array widgets: Array of arrays containing :ref:`widget <widget-response-label>` data
        :>json object options: Dashboard options

        .. _widget-response-label:

        Widget structure:

        :>json number widget.id: Widget ID
        :>json number widget.width: Widget size
        :>json object widget.options: Widget options
        :>json number widget.dashboard_id: ID of dashboard containing this widget
        :>json string widget.text: Widget contents, if this is a text-box widget
        :>json object widget.visualization: Widget contents, if this is a visualization widget
        :>json string widget.created_at: ISO format timestamp for widget creation
        :>json string widget.updated_at: ISO format timestamp for last widget modification
        """
        if request.args.get("legacy") is not None:
            fn = models.Dashboard.get_by_slug_and_org
        else:
            fn = models.Dashboard.get_by_id_and_org

        dashboard = get_object_or_404(fn, dashboard_id, self.current_org)
        require_dashboard_view(dashboard, self.current_user)
        response = DashboardSerializer(dashboard, with_widgets=True, user=self.current_user).serialize()

        response["can_edit"] = can_edit_dashboard(dashboard, self.current_user)
        response["can_manage_access"] = can_manage_dashboard_access(dashboard, self.current_user)
        response["public_access_available"] = False

        self.record_event({"action": "view", "object_id": dashboard.id, "object_type": "dashboard"})

        return response

    @require_permission("edit_dashboard")
    def post(self, dashboard_id):
        """
        Modifies a dashboard.

        :qparam number id: Id of dashboard to retrieve.

        Responds with the updated :ref:`dashboard <dashboard-response-label>`.

        :status 200: success
        :status 409: Version conflict -- dashboard modified since last read
        """
        dashboard_properties = request.get_json(force=True)
        # TODO: either convert all requests to use slugs or ids
        dashboard = get_object_or_404(models.Dashboard.get_by_id_and_org, dashboard_id, self.current_org)
        require_dashboard_edit(dashboard, self.current_user)

        updates = project(
            dashboard_properties,
            (
                "name",
                "layout",
                "version",
                "tags",
                "is_draft",
                "is_archived",
                "dashboard_filters_enabled",
                "options",
            ),
        )

        # SQLAlchemy handles the case where a concurrent transaction beats us
        # to the update. But we still have to make sure that we're not starting
        # out behind.
        if "version" in updates and updates["version"] != dashboard.version:
            abort(409)

        updates["changed_by"] = self.current_user

        self.update_model(dashboard, updates)
        models.db.session.add(dashboard)
        try:
            models.db.session.commit()
        except StaleDataError:
            abort(409)

        result = DashboardSerializer(dashboard, with_widgets=True, user=self.current_user).serialize()

        self.record_event({"action": "edit", "object_id": dashboard.id, "object_type": "dashboard"})

        return result

    @require_permission("edit_dashboard")
    def delete(self, dashboard_id):
        """
        Archives a dashboard.

        :qparam number id: Id of dashboard to retrieve.

        Responds with the archived :ref:`dashboard <dashboard-response-label>`.
        """
        dashboard = get_object_or_404(models.Dashboard.get_by_id_and_org, dashboard_id, self.current_org)
        require_dashboard_edit(dashboard, self.current_user)
        dashboard.is_archived = True
        dashboard.record_changes(changed_by=self.current_user)
        models.db.session.add(dashboard)
        d = DashboardSerializer(dashboard, with_widgets=True, user=self.current_user).serialize()
        models.db.session.commit()

        self.record_event({"action": "archive", "object_id": dashboard.id, "object_type": "dashboard"})

        return d


class PublicDashboardResource(BaseResource):
    decorators = BaseResource.decorators + [csp_allows_embeding]

    def get(self, token):
        """
        Retrieve a public dashboard.

        :param token: An API key for a public dashboard.
        :>json array widgets: An array of arrays of :ref:`public widgets <public-widget-label>`, corresponding to the rows and columns the widgets are displayed in
        """
        abort(403, message="Anonymous dashboard access is disabled. Sign in with an assigned user account.")


class DashboardShareResource(BaseResource):
    def post(self, dashboard_id):
        """
        Allow anonymous access to a dashboard.

        :param dashboard_id: The numeric ID of the dashboard to share.
        :>json string public_url: The URL for anonymous access to the dashboard.
        :>json api_key: The API key to use when accessing it.
        """
        dashboard = get_object_or_404(models.Dashboard.get_by_id_and_org, dashboard_id, self.current_org)
        if not can_manage_dashboard_access(dashboard, self.current_user):
            abort(403)
        abort(403, message="Public links are disabled. Use User access to assign signed-in users.")

    def delete(self, dashboard_id):
        """
        Disable anonymous access to a dashboard.

        :param dashboard_id: The numeric ID of the dashboard to unshare.
        """
        dashboard = get_object_or_404(models.Dashboard.get_by_id_and_org, dashboard_id, self.current_org)
        if not can_manage_dashboard_access(dashboard, self.current_user):
            abort(403)
        api_key = models.ApiKey.get_by_object(dashboard)

        if api_key:
            api_key.active = False
            models.db.session.add(api_key)
            models.db.session.commit()

        self.record_event(
            {
                "action": "deactivate_api_key",
                "object_id": dashboard.id,
                "object_type": "dashboard",
            }
        )


class DashboardAccessResource(BaseResource):
    """Explicit signed-in user grants; unrelated to anonymous public links."""

    def _dashboard(self, dashboard_id, lock=False):
        try:
            dashboard_id = int(dashboard_id)
        except (TypeError, ValueError):
            abort(404)
        query = models.Dashboard.query.filter_by(id=dashboard_id, org_id=self.current_org.id)
        if lock:
            query = query.populate_existing().with_for_update()
        dashboard = get_object_or_404(query.first)
        if not can_manage_dashboard_access(dashboard, self.current_user):
            abort(403, message="Only an administrator or the dashboard owner can manage user access.")
        return dashboard

    def _response(self, dashboard):
        users = [user for user in models.User.query.filter_by(org_id=self.current_org.id).all()
                 if not user.is_disabled and user.role in ("editor", "reader")
                 and (user.id != dashboard.user_id or user.role == "reader")]
        return {
            "read_user_ids": [grant.user_id for grant in models.DashboardReadGrant.query.filter_by(
                dashboard_id=dashboard.id).order_by(models.DashboardReadGrant.user_id).all()],
            "candidates": [{"id": user.id, "name": user.name, "role": user.role}
                           for user in sorted(users, key=lambda user: (user.name.lower(), user.id))],
            "read_group_ids": [g.group_id for g in models.DashboardGroupReadGrant.query.filter_by(dashboard_id=dashboard.id).order_by(models.DashboardGroupReadGrant.group_id)],
            "group_candidates": [{"id": g.id, "name": g.name} for g in models.Group.query.filter_by(org_id=self.current_org.id).order_by(models.Group.name)],
            "revision": dashboard.version,
            "can_manage": True,
            "is_draft": dashboard.is_draft,
            "is_archived": dashboard.is_archived,
            "owner_edit_only": False,
        }

    def get(self, dashboard_id):
        return self._response(self._dashboard(dashboard_id))

    def post(self, dashboard_id):
        # Explicit CSRF defense applies even where global enforcement is disabled.
        if "_user_id" in session or "user_id" in session:
            csrf.protect()
        if request.content_length and request.content_length > 32000:
            abort(413, message="User access request is too large.")
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) not in ({"read_user_ids", "revision"}, {"read_user_ids", "read_group_ids", "revision"})
                or type(body["revision"]) is not int or not isinstance(body["read_user_ids"], list)
                or len(body["read_user_ids"]) > 1000
                or any(type(value) is not int or value < 1 for value in body["read_user_ids"])
                or len(set(body["read_user_ids"])) != len(body["read_user_ids"])):
            abort(400, message="Provide unique user IDs and the current access revision.")
        if "read_group_ids" in body:
            ids = body["read_group_ids"]
            if (not isinstance(ids, list) or len(ids) > 1000 or
                    any(type(value) is not int or value < 1 for value in ids) or len(set(ids)) != len(ids)):
                abort(400, message="Provide unique positive group IDs.")
        dashboard = self._dashboard(dashboard_id, lock=True)
        if dashboard.version != body["revision"]:
            abort(409, message="Dashboard access changed. Reload before saving.")
        user_ids = set(body["read_user_ids"])
        group_ids = set(body.get("read_group_ids", [g.group_id for g in models.DashboardGroupReadGrant.query.filter_by(dashboard_id=dashboard.id)]))
        existing_users = {g.user_id for g in models.DashboardReadGrant.query.filter_by(dashboard_id=dashboard.id)}
        existing_groups = {g.group_id for g in models.DashboardGroupReadGrant.query.filter_by(dashboard_id=dashboard.id)}
        if (user_ids - existing_users or group_ids - existing_groups) and (dashboard.is_draft or dashboard.is_archived):
            abort(400, message="Publish the dashboard before assigning user access.")
        allowed = {candidate["id"] for candidate in self._response(dashboard)["candidates"]}
        if not user_ids.issubset(allowed):
            abort(400, message="Select active Editors or Readers in this organization.")
        allowed_groups = {g.id for g in models.Group.query.filter_by(org_id=self.current_org.id)}
        if not group_ids.issubset(allowed_groups):
            abort(400, message="Choose groups from this organization.")
        if "read_group_ids" in body:
            models.DashboardGroupReadGrant.query.filter_by(dashboard_id=dashboard.id).delete(synchronize_session=False)
            for group_id in sorted(group_ids):
                models.db.session.add(models.DashboardGroupReadGrant(dashboard_id=dashboard.id, group_id=group_id, grantor_id=self.current_user.id))
        models.DashboardReadGrant.query.filter_by(dashboard_id=dashboard.id).delete(synchronize_session=False)
        for user_id in sorted(user_ids):
            models.db.session.add(models.DashboardReadGrant(
                dashboard_id=dashboard.id, user_id=user_id, grantor_id=self.current_user.id))
        # Revision changes bind queued jobs and cached report provenance too.
        dashboard.version = (dashboard.version or 0) + 1
        models.db.session.add(dashboard)
        try:
            models.db.session.commit()
        except StaleDataError:
            models.db.session.rollback()
            abort(409, message="Dashboard access changed. Reload before saving.")
        self.record_event({"action": "edit_user_access", "object_type": "dashboard", "object_id": dashboard.id})
        return self._response(dashboard)


class DashboardTagsResource(BaseResource):
    @require_permission("list_dashboards")
    def get(self):
        """
        Lists all accessible dashboards.
        """
        tags = models.Dashboard.all_tags(self.current_org, self.current_user)
        return {"tags": [{"name": name, "count": count} for name, count in tags]}


class DashboardFavoriteListResource(BaseResource):
    def get(self):
        search_term = request.args.get("q")

        if search_term:
            base_query = models.Dashboard.search(
                self.current_org,
                self.current_user.group_ids,
                self.current_user.id,
                search_term,
                include_all=self.current_user.has_permission("admin"),
            )
            favorites = models.Dashboard.favorites(self.current_user, base_query=base_query)
        else:
            favorites = models.Dashboard.favorites(self.current_user)

        favorites = filter_by_tags(favorites, models.Dashboard.tags)

        # order results according to passed order parameter,
        # special-casing search queries where the database
        # provides an order by search rank
        favorites = order_results(favorites, fallback=not bool(search_term))

        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", 25, type=int)
        # TODO: we don't need to check for favorite status here
        response = paginate(favorites, page, page_size, DashboardSerializer)

        self.record_event(
            {
                "action": "load_favorites",
                "object_type": "dashboard",
                "params": {
                    "q": search_term,
                    "tags": request.args.getlist("tags"),
                    "page": page,
                },
            }
        )

        return response


class DashboardForkResource(BaseResource):
    @require_permission("edit_dashboard")
    def post(self, dashboard_id):
        dashboard = get_object_or_404(models.Dashboard.get_by_id_and_org, dashboard_id, self.current_org)
        require_dashboard_view(dashboard, self.current_user)
        require_dashboard_edit(dashboard, self.current_user)

        for widget in dashboard.widgets:
            if widget.visualization is not None:
                if self.current_user.role != "admin" and widget.visualization.query_rel.user_id != self.current_user.id:
                    abort(403, message="Create your own query copies before duplicating this dashboard.")
                require_access(widget.visualization.query_rel, self.current_user, view_only)

        fork_dashboard = dashboard.fork(self.current_user)
        models.db.session.commit()

        self.record_event({"action": "fork", "object_id": dashboard_id, "object_type": "dashboard"})

        return DashboardSerializer(fork_dashboard, with_widgets=True, user=self.current_user).serialize()
