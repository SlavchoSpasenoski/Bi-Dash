import os

from flask import request, session
from flask_restful import abort

from redash import limiter
from redash.ai_assistant import AssistantError, AssistantTimeoutError, ask_assistant, validate_messages
from redash.handlers.base import BaseResource
from redash.permissions import require_permission
from redash.security import csrf


def ai_configuration():
    provider = os.environ.get("REDASH_AI_PROVIDER", "ollama").lower()
    model = os.environ.get("REDASH_AI_MODEL") or ("qwen2.5-coder:7b" if provider == "ollama" else "gpt-5.4-mini")
    enabled = provider == "ollama" or (provider == "openai" and bool(os.environ.get("OPENAI_API_KEY")))
    try:
        timeout = int(os.environ.get("REDASH_OLLAMA_TIMEOUT", "1800")) if provider == "ollama" else 45
    except ValueError:
        abort(503, message="REDASH_OLLAMA_TIMEOUT must be a positive number of seconds.")
    if timeout <= 0:
        abort(503, message="REDASH_OLLAMA_TIMEOUT must be a positive number of seconds.")
    return {"provider": provider, "model": model, "enabled": enabled, "timeout_seconds": timeout}


class AssistantResource(BaseResource):
    @require_permission("create_query")
    def get(self):
        return ai_configuration()

    @require_permission("create_query")
    @limiter.limit("10/minute")
    def post(self):
        # Flask-Login stores cookie logins under _user_id. Require CSRF for
        # browser sessions even on upstream deployments with CSRF disabled.
        if "_user_id" in session or "user_id" in session:
            csrf.protect()
        if request.content_length and request.content_length > 160000:
            abort(413, message="Conversation is too large.")
        configuration = ai_configuration()
        if not configuration["enabled"]:
            abort(503, message="The selected AI provider is not configured.")
        body = request.get_json()
        if not isinstance(body, dict):
            abort(400, message="Expected a JSON object.")
        try:
            messages = validate_messages(body.get("messages"))
        except ValueError as error:
            abort(400, message=str(error))
        schema = body.get("schema", "")
        if not isinstance(schema, str) or len(schema) > 24000:
            abort(400, message="Schema must be text of at most 24000 characters.")
        try:
            return {"answer": ask_assistant(
                os.environ.get("OPENAI_API_KEY", ""), configuration["model"], messages, schema,
                provider=configuration["provider"],
                ollama_url=os.environ.get("REDASH_OLLAMA_URL", "http://ollama:11434"),
                ollama_timeout=configuration["timeout_seconds"],
                ollama_keep_alive=os.environ.get("REDASH_OLLAMA_KEEP_ALIVE", "30m"),
            )}
        except AssistantTimeoutError as error:
            abort(504, message=str(error))
        except AssistantError as error:
            abort(502, message=str(error))
