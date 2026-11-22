"""Bounded, authenticated portable content envelopes, independent of install keys."""
import base64
import json
import os
import math
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CONTENT_BYTES = 12 * 1024 * 1024
FORMAT = "bi-dash-encrypted-content"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def _password(passphrase):
    if not isinstance(passphrase, str) or not 12 <= len(passphrase) <= 1024:
        raise ValueError("Use a package passphrase of 12–1024 characters.")
    return passphrase.encode("utf-8")


def _cipher(password, salt):
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000).derive(password)
    return Fernet(base64.urlsafe_b64encode(key))


def _depth(value, level=0):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Invalid numeric package content.")
    if level > 40:
        raise ValueError("Package content is too deeply nested.")
    if isinstance(value, dict):
        for child in value.values():
            _depth(child, level + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, level + 1)


def seal(content, passphrase):
    password = _password(passphrase)
    _depth(content)
    raw = canonical(content)
    if len(raw) > MAX_CONTENT_BYTES:
        raise ValueError("Selected content exceeds the 12 MiB package limit.")
    salt = os.urandom(16)
    return {"format": FORMAT, "version": 1, "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
            "ciphertext": _cipher(password, salt).encrypt(raw).decode("ascii")}


def unseal(envelope, passphrase):
    password = _password(passphrase)
    if (not isinstance(envelope, dict) or set(envelope) != {"format", "version", "salt", "ciphertext"}
            or envelope.get("format") != FORMAT or type(envelope.get("version")) is not int or envelope["version"] != 1
            or not isinstance(envelope.get("salt"), str) or len(envelope["salt"]) != 24
            or not isinstance(envelope.get("ciphertext"), str) or len(envelope["ciphertext"]) > MAX_FILE_BYTES):
        raise ValueError("Unsupported or invalid migration package.")
    try:
        salt = base64.b64decode(envelope["salt"], altchars=b"-_", validate=True)
        if len(salt) != 16:
            raise ValueError()
        raw = _cipher(password, salt).decrypt(envelope["ciphertext"].encode("ascii"))
        if len(raw) > MAX_CONTENT_BYTES:
            raise ValueError()
        content = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        _depth(content)
        if not isinstance(content, dict):
            raise ValueError()
        return content
    except (ValueError, InvalidToken, UnicodeError, RecursionError, TypeError):
        raise ValueError("Unable to open package. Check the passphrase and file.") from None
