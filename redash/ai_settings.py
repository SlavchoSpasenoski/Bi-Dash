# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Organization AI preferences, independent of Flask and database initialization."""

import base64
import hashlib
import os
from urllib.parse import urlsplit


PROVIDERS = {
    "ollama": {"model": "qwen2.5-coder:7b", "base_url": "http://ollama:11434"},
    "openai": {"model": "", "base_url": "https://api.openai.com/v1"},
    "anthropic": {"model": "", "base_url": "https://api.anthropic.com/v1"},
    "compatible": {"model": "", "base_url": ""},
}
CONTEXT_LIMIT = 24000


def validate_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("API base URL must be text of at most 2048 characters.")
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError("Enter an HTTP(S) API base URL without credentials, query parameters or fragments.")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Invalid API base URL port.") from error
    return value.rstrip("/")


def secret_cipher(secret):
    from cryptography.fernet import Fernet

    if not secret:
        raise ValueError("Configure REDASH_SECRET_KEY before saving an AI API key.")
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def environment_profile(provider):
    defaults = PROVIDERS.get(provider, PROVIDERS["ollama"])
    return {
        "model": os.environ.get("REDASH_AI_MODEL") or ("gpt-5.4-mini" if provider == "openai" else defaults["model"]),
        "base_url": os.environ.get("REDASH_OLLAMA_URL", defaults["base_url"])
        if provider == "ollama" else defaults["base_url"],
    }


def public_configuration(stored):
    provider = stored.get("provider", os.environ.get("REDASH_AI_PROVIDER", "ollama").lower())
    profiles = {}
    for name, defaults in PROVIDERS.items():
        profile = stored.get("profiles", {}).get(name)
        values = profile if profile is not None else (environment_profile(name) if name == provider else defaults)
        has_key = bool(profile.get("encrypted_api_key")) if profile is not None else (
            not stored and name == provider == "openai" and bool(os.environ.get("OPENAI_API_KEY")))
        profiles[name] = {"model": values.get("model", ""), "base_url": values.get("base_url", ""),
                          "has_api_key": has_key}
    return {"provider": provider, "profiles": profiles}


def update_configuration(stored, body, secret):
    if (not isinstance(body, dict) or not isinstance(body.get("provider"), str)
            or body["provider"] not in PROVIDERS):
        raise ValueError("Choose a supported AI provider.")
    provider = body["provider"]
    model = body.get("model")
    if not isinstance(model, str) or not 1 <= len(model.strip()) <= 200:
        raise ValueError("Enter a model ID of 1–200 characters.")
    base_url = validate_url(body.get("base_url", ""))
    if provider in ("openai", "anthropic") and base_url != PROVIDERS[provider]["base_url"]:
        raise ValueError("Use OpenAI-compatible for a custom API base URL.")
    key = body.get("api_key", "")
    if not isinstance(key, str) or len(key) > 4096 or any(ord(c) < 32 for c in key):
        raise ValueError("Invalid API key.")
    if "clear_api_key" in body and not isinstance(body["clear_api_key"], bool):
        raise ValueError("clear_api_key must be a boolean.")
    if key.strip() and body.get("clear_api_key"):
        raise ValueError("Choose either replacing or removing the API key.")
    profiles = dict(stored.get("profiles", {}))
    previous = profiles.get(provider, {})
    encrypted = previous.get("encrypted_api_key", "")
    # Never forward a saved key to a newly selected host.
    if previous.get("base_url") != base_url or body.get("clear_api_key") or provider == "ollama":
        encrypted = ""
    if key.strip() and provider != "ollama":
        encrypted = secret_cipher(secret).encrypt(key.strip().encode("utf-8")).decode("ascii")
    profiles[provider] = {"model": model.strip(), "base_url": base_url, "encrypted_api_key": encrypted}
    return {"provider": provider, "profiles": profiles}


def runtime_configuration(stored, secret):
    public = public_configuration(stored)
    provider = public["provider"]
    if provider not in PROVIDERS:
        raise ValueError("Unsupported AI provider. Configure AI in Settings.")
    profile = public["profiles"][provider]
    encrypted = stored.get("profiles", {}).get(provider, {}).get("encrypted_api_key")
    key = ""
    if encrypted:
        try:
            key = secret_cipher(secret).decrypt(encrypted.encode("ascii")).decode("utf-8")
        except Exception as error:
            raise ValueError("Cannot read the saved AI key. Save it again in AI Configuration.") from error
    elif not stored and provider == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
    timeout = int(os.environ.get("REDASH_OLLAMA_TIMEOUT", "1800")) if provider == "ollama" else 120
    if timeout <= 0:
        raise ValueError("AI timeout must be positive.")
    return {**profile, "provider": provider, "api_key": key, "timeout_seconds": timeout,
            "enabled": bool(profile["model"] and profile["base_url"]
                            and (key or provider in ("ollama", "compatible")))}
