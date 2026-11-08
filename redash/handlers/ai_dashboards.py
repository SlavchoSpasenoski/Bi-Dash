# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
import os
import uuid

from flask_restful import abort
from redis.exceptions import LockError

from redash import limiter, models, redis_connection
from redash.ai_assistant import AssistantError, AssistantTimeoutError, ask_assistant
from redash.ai_dashboard import SUPPORTED_SOURCES, parse_plan, reference_schema, visualization_options
from redash.handlers.ai import ai_configuration, json_body
from redash.handlers.base import BaseResource, get_object_or_404
from redash.permissions import has_access, not_view_only, require_permissions

BUILDER_PERMISSIONS = ("use_ai", "view_source", "view_query", "create_query", "create_dashboard",
                       "edit_query", "edit_dashboard", "execute_query")


def draft_response(dashboard):
    widgets = [w for w in dashboard.widgets if w.visualization_id]
    return {"dashboard_id": dashboard.id, "url": f"dashboards/{dashboard.id}", "title": dashboard.name,
            "description": dashboard.options.get("ai_description", ""),
            "queries": [{"id": w.visualization.query_rel.id, "title": w.visualization.name,
                         "visualization_id": w.visualization_id, "type": w.visualization.type,
                         "columns": list(w.visualization.options.get("columnMapping", {}))
                         if w.visualization.type == "CHART" else
                         ([w.visualization.options["counterColName"]] if w.visualization.type == "COUNTER" else [])}
                        for w in sorted(widgets, key=lambda w: w.id)]}


class AssistantDashboardResource(BaseResource):
    @require_permissions(BUILDER_PERMISSIONS)
    def get(self):
        sources = models.DataSource.query.filter_by(org_id=self.current_org.id).all()
        return {"data_sources": [{"id": ds.id, "name": ds.name, "type": ds.type} for ds in sources
                                 if ds.type in SUPPORTED_SOURCES and not ds.paused
                                 and has_access(ds, self.current_user, not_view_only)]}

    @require_permissions(BUILDER_PERMISSIONS)
    @limiter.limit("4/minute")
    def post(self):
        body = json_body()
        prompt = body.get("prompt")
        extra = body.get("context", "")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 8000:
            abort(400, message="Describe the report in 1–8000 characters.")
        if not isinstance(extra, str) or len(extra) > 12000:
            abort(400, message="Extra context must be at most 12000 characters.")
        if type(body.get("data_source_id")) is not int:
            abort(400, message="Select a data source.")
        try:
            request_id = str(uuid.UUID(body.get("request_id", "")))
        except (ValueError, TypeError, AttributeError):
            abort(400, message="A valid dashboard request ID is required.")
        org_id, user_id = self.current_org.id, self.current_user.id

        def existing_draft():
            return models.Dashboard.query.filter_by(org_id=org_id, user_id=user_id).filter(
                models.Dashboard.options["ai_request_id"].astext == request_id).first()

        source = get_object_or_404(models.DataSource.get_by_id_and_org, body["data_source_id"], self.current_org)
        if source.type not in SUPPORTED_SOURCES or source.paused or not has_access(source, self.current_user, not_view_only):
            abort(403, message="Choose an accessible, active PostgreSQL or MySQL/MariaDB data source.")
        existing = existing_draft()
        if existing:
            if any(w.visualization and w.visualization.query_rel.data_source_id != source.id for w in existing.widgets):
                abort(409, message="Use a new request ID when changing the data source.")
            return draft_response(existing)
        schema = source.get_cached_schema()
        if not schema:
            abort(409, message="Load the data-source schema before creating a dashboard.")
        try:
            reference = reference_schema(schema, body.get("tables"), source.type)
        except ValueError as error:
            abort(400, message=str(error))
        configuration = ai_configuration(self.current_org)
        if not configuration["enabled"]:
            abort(503, message="Configure the AI provider in Settings → AI Configuration.")
        context = self.current_org.settings.get("ai_context", "")
        if len(context) + len(reference) + len(extra) > 30000:
            abort(400, message="The combined context is too large. Choose fewer tables or shorten the AI context.")
        # Do not hold a database connection while a local model is generating its plan.
        models.db.session.close()
        try:
            answer = ask_assistant(
                configuration["api_key"], configuration["model"], [{"role": "user", "content": prompt.strip()}],
                reference, provider=configuration["provider"], base_url=configuration["base_url"],
                context="\n\n".join(part for part in (context, extra) if part), dashboard=True,
                ollama_timeout=configuration["timeout_seconds"], request_timeout=configuration["timeout_seconds"],
                ollama_keep_alive=os.environ.get("REDASH_OLLAMA_KEEP_ALIVE", "30m"),
            )
            plan = parse_plan(answer)
        except AssistantTimeoutError as error:
            abort(504, message=str(error))
        except AssistantError as error:
            abort(502, message=str(error))
        except ValueError as error:
            abort(422, message=str(error))
        if "clarification" in plan:
            return plan
        # Recheck permissions and source access after the provider request.
        user = models.User.get_by_id(user_id)
        source = models.DataSource.query.filter_by(id=body["data_source_id"], org_id=org_id).first()
        if (user is None or user.is_disabled or user.org_id != org_id or not user.has_permissions(BUILDER_PERMISSIONS)
                or source is None or source.paused or source.type not in SUPPORTED_SOURCES
                or not has_access(source, user, not_view_only)):
            abort(403, message="Your permissions or data-source access changed while the plan was being generated.")
        try:
            with redis_connection.lock(f"ai:draft:{org_id}:{user_id}:{request_id}", timeout=20, blocking_timeout=5):
                existing = existing_draft()
                if existing:
                    return draft_response(existing)
                dashboard = models.Dashboard(org_id=org_id, user=user, name=plan["title"], is_draft=True,
                                             layout=[], options={"ai_request_id": request_id,
                                                                 "ai_description": plan["description"]})
                models.db.session.add(dashboard)
                models.db.session.flush()
                layout = []
                for index, widget in enumerate(plan["widgets"]):
                    query = models.Query.create(org_id=org_id, user=user, data_source=source,
                                                name=widget["title"], query_text=widget["sql"],
                                                description="AI-generated draft. Review its business assumptions.",
                                                is_draft=True, options={"parameters": [], "ai_generated": True,
                                                                        "apply_auto_limit": False})
                    models.db.session.add(query)
                    models.db.session.flush()
                    kind, options = visualization_options(widget)
                    visualization = models.Visualization(query_rel=query, type=kind, name=widget["title"], options=options)
                    models.db.session.add(visualization)
                    models.db.session.flush()
                    item = models.Widget(dashboard=dashboard, visualization=visualization, width=1,
                                         options={"position": {"col": (index % 2) * 6, "row": (index // 2) * 8,
                                                               "sizeX": 6, "sizeY": 8}})
                    models.db.session.add(item)
                    models.db.session.flush()
                    if index % 2 == 0:
                        layout.append([])
                    layout[-1].append(item.id)
                dashboard.layout = layout
                models.db.session.commit()
        except LockError:
            models.db.session.rollback()
            abort(409, message="This dashboard is already being created. Retry the same request shortly.")
        except Exception:
            models.db.session.rollback()
            raise
        self.record_event({"action": "create", "object_type": "ai_dashboard", "object_id": dashboard.id,
                           "data_source_id": source.id, "visualizations": len(plan["widgets"])})
        return draft_response(dashboard)
