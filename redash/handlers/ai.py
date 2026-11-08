# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
import os

from flask import request, session
from flask_restful import abort

from redash import limiter, settings
from redash.ai_assistant import AssistantError, AssistantTimeoutError, ask_assistant, validate_messages
from redash.ai_settings import CONTEXT_LIMIT, public_configuration, runtime_configuration, update_configuration
from redash.handlers.base import BaseResource
from redash.models import db
from redash.permissions import require_admin, require_permission
from redash.security import csrf


def json_body(limit=160000):
    if "_user_id" in session or "user_id" in session:
        csrf.protect()
    if request.content_length and request.content_length > limit:
        abort(413, message="Request is too large.")
    body = request.get_json()
    if not isinstance(body, dict):
        abort(400, message="Expected a JSON object.")
    return body


def ai_configuration(org):
    try:
        return runtime_configuration(org.settings.get("ai_configuration", {}), settings.DATASOURCE_SECRET_KEY)
    except ValueError as error:
        abort(503, message=str(error))


class AssistantSettingsResource(BaseResource):
    @require_admin
    def get(self):
        return public_configuration(self.current_org.settings.get("ai_configuration", {}))

    @require_admin
    def post(self):
        body = json_body(32000)
        try:
            configuration = update_configuration(
                self.current_org.settings.get("ai_configuration", {}), body, settings.DATASOURCE_SECRET_KEY)
        except ValueError as error:
            abort(400, message=str(error))
        self.current_org.settings["ai_configuration"] = configuration
        db.session.add(self.current_org)
        db.session.commit()
        self.record_event({"action": "edit", "object_type": "ai_configuration", "object_id": self.current_org.id})
        return public_configuration(configuration)


class AssistantContextResource(BaseResource):
    @require_admin
    def get(self):
        return {"context": self.current_org.settings.get("ai_context", "")}

    @require_admin
    def post(self):
        body = json_body()
        context = body.get("context")
        if not isinstance(context, str) or len(context) > CONTEXT_LIMIT:
            abort(400, message=f"Context must be text of at most {CONTEXT_LIMIT} characters.")
        self.current_org.settings["ai_context"] = context
        db.session.add(self.current_org)
        db.session.commit()
        self.record_event({"action": "edit", "object_type": "ai_context", "object_id": self.current_org.id})
        return {"context": context}


class AssistantResource(BaseResource):
    @require_permission("use_ai")
    def get(self):
        configuration = ai_configuration(self.current_org)
        return {key: configuration[key] for key in ("provider", "model", "enabled", "timeout_seconds")} | {
            "has_context": bool(self.current_org.settings.get("ai_context")),
            "destination": configuration["base_url"],
        }

    @require_permission("use_ai")
    @limiter.limit("10/minute")
    def post(self):
        body = json_body()
        configuration = ai_configuration(self.current_org)
        if not configuration["enabled"]:
            abort(503, message="Configure the AI provider and model in Settings → AI Configuration.")
        try:
            messages = validate_messages(body.get("messages"))
        except ValueError as error:
            abort(400, message=str(error))
        schema = body.get("schema", "")
        if not isinstance(schema, str) or len(schema) > 24000:
            abort(400, message="Schema must be text of at most 24000 characters.")
        try:
            return {"answer": ask_assistant(
                configuration["api_key"], configuration["model"], messages, schema,
                provider=configuration["provider"], base_url=configuration["base_url"],
                context=self.current_org.settings.get("ai_context", ""),
                ollama_timeout=configuration["timeout_seconds"],
                request_timeout=configuration["timeout_seconds"],
                ollama_keep_alive=os.environ.get("REDASH_OLLAMA_KEEP_ALIVE", "30m"),
            )}
        except AssistantTimeoutError as error:
            abort(504, message=str(error))
        except AssistantError as error:
            abort(502, message=str(error))
