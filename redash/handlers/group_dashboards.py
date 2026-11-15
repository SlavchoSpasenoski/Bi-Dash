"""Admin group-side view of the same dashboard group grants."""
from flask import request
from flask_restful import abort
from sqlalchemy.orm.exc import StaleDataError
from redash import models
from redash.handlers.access_policy import access_body, lock_administrator
from redash.handlers.base import BaseResource, get_object_or_404
from redash.permissions import require_admin


class GroupDashboardListResource(BaseResource):
    def _group(self, group_id):
        return get_object_or_404(models.Group.get_by_id_and_org, group_id, self.current_org)

    def _row(self, board, group_id):
        return {"id": board.id, "name": board.name, "is_draft": board.is_draft, "is_archived": board.is_archived,
                "revision": board.version, "assigned": models.DashboardGroupReadGrant.query.filter_by(
                    dashboard_id=board.id, group_id=group_id).first() is not None}

    @require_admin
    def get(self, group_id):
        group = self._group(group_id)
        try:
            page = int(request.args.get("page", "1"))
            size = int(request.args.get("page_size", "25"))
        except ValueError:
            abort(400, message="Choose a valid page and page size (1–100).")
        if page < 1 or not 1 <= size <= 100:
            abort(400, message="Choose a valid page and page size (1–100).")
        query = models.Dashboard.query.filter_by(org_id=self.current_org.id)
        term = request.args.get("q", "").strip()
        if len(term) > 200:
            abort(400, message="Search is too long.")
        if term:
            query = query.filter(models.Dashboard.name.ilike("%" + term + "%"))
        count = query.count()
        boards = query.order_by(models.Dashboard.name, models.Dashboard.id).offset((page - 1) * size).limit(size).all()
        return {"results": [self._row(board, group.id) for board in boards], "count": count, "page": page, "page_size": size}

    @require_admin
    def post(self, group_id):
        body = access_body()
        if (set(body) != {"dashboard_id", "revision", "assigned"} or type(body["dashboard_id"]) is not int
                or body["dashboard_id"] < 1 or type(body["revision"]) is not int or type(body["assigned"]) is not bool):
            abort(400, message="Provide dashboard_id, revision and assigned.")
        lock_administrator(self.current_org, self.current_user)
        group = self._group(group_id)
        board = get_object_or_404(models.Dashboard.query.filter_by(id=body["dashboard_id"], org_id=self.current_org.id)
                                 .populate_existing().with_for_update().first)
        if board.version != body["revision"]:
            abort(409, message="Dashboard access changed. Reload before saving.")
        if body["assigned"] and (board.is_draft or board.is_archived):
            abort(400, message="Publish the dashboard before assigning group access.")
        grant = models.DashboardGroupReadGrant.query.filter_by(dashboard_id=board.id, group_id=group.id).first()
        if body["assigned"] and grant is None:
            models.db.session.add(models.DashboardGroupReadGrant(dashboard_id=board.id, group_id=group.id, grantor_id=self.current_user.id))
        elif not body["assigned"] and grant is not None:
            models.db.session.delete(grant)
        board.version = (board.version or 0) + 1
        try:
            models.db.session.commit()
        except StaleDataError:
            models.db.session.rollback()
            abort(409, message="Dashboard access changed. Reload before saving.")
        self.record_event({"action": "edit_group_access", "object_type": "dashboard", "object_id": board.id, "group_id": group.id})
        return self._row(board, group.id)
