"""Authenticated lifecycle adapters; no provider configuration or call dispatch.

Vonage Voice API must actually observe the outbound PSTN leg and deliver signed
POST events. An SLNG SIP trunk alone does not establish that integration. A trusted
operator must bind the exact Vonage UUID to an existing bound SLNG request first.
No association is inferred from a phone number, conversation UUID or event fields.

References:
https://developer.vonage.com/en/voice/voice-api/webhook-reference
https://developer.vonage.com/es/getting-started/concepts/webhooks?source=getting-started
https://docs.slng.ai/guides/agents/tools-and-mcp/run-on-call-events.md
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import sqlite3
from uuid import UUID

from .voice_models import identifier, utc
from .voice_store import digest

MAX_BODY = 32768
MAX_TOKEN = 8192
MAX_TOKEN_AGE_SECONDS = 300
CLOCK_SKEW_SECONDS = 30
STATUS_MAP = {
    "ringing": "ringing",
    "answered": "in_progress",
    "busy": "no_answer",
    "unanswered": "no_answer",
    "failed": "failed",
    "cancelled": "failed",
    "completed": "completed",
}


def _uuid(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})",
        value,
    ):
        raise ValueError("invalid provider call identifier")
    return str(UUID(value))


def _schema(store):
    # execute (not executescript) preserves the coordinator's enclosing transaction.
    store.conn.execute("""CREATE TABLE IF NOT EXISTS voice_vonage_bindings (
        vonage_call_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        provider_call_id TEXT NOT NULL UNIQUE)""")


def bind_vonage_call(store, *, vonage_call_id, request_id):
    """Trusted local association, immutable and one-to-one; never a webhook input."""
    call_id = _uuid(vonage_call_id)
    identifier(request_id, "request_id")
    with store._transaction():
        _schema(store)
        record = store.get(request_id)
        if not record["provider_call_id"]:
            raise ValueError("provider call must already be bound")
        row = store.conn.execute(
            "SELECT request_id, provider_call_id FROM voice_vonage_bindings "
            "WHERE vonage_call_id=?",
            (call_id,),
        ).fetchone()
        expected = (request_id, record["provider_call_id"])
        if row:
            if tuple(row) != expected:
                raise ValueError("provider association is immutable")
            return
        try:
            store.conn.execute(
                "INSERT INTO voice_vonage_bindings VALUES (?, ?, ?)",
                (call_id, *expected),
            )
        except sqlite3.IntegrityError:
            raise ValueError("provider association is immutable") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _json(data):
    value = json.loads(
        data,
        object_pairs_hook=_unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
    )
    if not isinstance(value, dict):
        raise ValueError("invalid JSON object")  # noqa: TRY004 — uniform input validation contract
    return value


def _decode(segment):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
        raise ValueError()
    return base64.b64decode(
        segment + "=" * (-len(segment) % 4), altchars=b"-_", validate=True
    )


def _verify(store, body, authorization, *, signature_secret, api_key, application_id):
    if (
        not isinstance(signature_secret, str)
        or not signature_secret
        or not isinstance(api_key, str)
        or not api_key
    ):
        raise ValueError("provider event verification is not configured")
    if application_id is not None and (
        not isinstance(application_id, str) or not application_id
    ):
        raise ValueError("provider event verification is not configured")
    try:
        if (
            not isinstance(authorization, str)
            or len(authorization) > MAX_TOKEN
            or not authorization.startswith("Bearer ")
        ):
            raise ValueError()
        head, payload, signature = authorization[7:].split(".")
        header = _json(_decode(head))
        if (
            header.get("alg") != "HS256"
            or header.get("typ", "JWT") != "JWT"
            or "crit" in header
            or "b64" in header
        ):
            raise ValueError()
        expected = hmac.digest(
            signature_secret.encode(), (head + "." + payload).encode("ascii"), "sha256"
        )
        if not hmac.compare_digest(expected, _decode(signature)):
            raise ValueError()
        claims = _json(_decode(payload))
        if claims.get("iss") != "Vonage" or claims.get("api_key") != api_key:
            raise ValueError()
        if (
            application_id is not None
            and claims.get("application_id") != application_id
        ):
            raise ValueError()
        identifier(claims.get("jti"), "token identifier")
        now = store.clock().timestamp()
        issued = claims.get("iat")
        if (
            type(issued) is not int
            or not now - MAX_TOKEN_AGE_SECONDS <= issued <= now + CLOCK_SKEW_SECONDS
        ):
            raise ValueError()
        for name in ("exp", "nbf"):
            if name in claims and type(claims[name]) is not int:
                raise ValueError()
        if ("exp" in claims and claims["exp"] <= now) or (
            "nbf" in claims and claims["nbf"] > now + CLOCK_SKEW_SECONDS
        ):
            raise ValueError()
        payload_hash = claims.get("payload_hash")
        if not isinstance(payload_hash, str) or not re.fullmatch(
            "[0-9a-f]{64}", payload_hash
        ):
            raise ValueError()
        if not hmac.compare_digest(payload_hash, hashlib.sha256(body).hexdigest()):
            raise ValueError()
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        raise PermissionError("unauthorized") from None


def _body(body):
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY:
        raise ValueError("invalid provider event")
    try:
        return _json(body)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid provider event") from None


def ingest_vonage_event(
    store, body, authorization, *, signature_secret, api_key, application_id=None
):
    """Verify HS256 JWT, account/app, raw-body hash and time before recording facts.

    Policy: token age <=300s and future skew <=30s; exp/nbf enforced if provided.
    Mandatory payload_hash binds authenticated identity to these exact body bytes.
    Lifecycle timestamp must be within the store's incident epoch and current time.
    Semantic event IDs deduplicate retries even when Vonage re-signs the delivery.
    Returns accepted, duplicate or ignored. Authentication failures raise
    PermissionError; configuration/payload/association failures raise ValueError.
    """
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY:
        raise ValueError("invalid provider event")
    _verify(
        store,
        body,
        authorization,
        signature_secret=signature_secret,
        api_key=api_key,
        application_id=application_id,
    )
    payload = _body(body)
    call_id = _uuid(payload.get("uuid"))
    status = payload.get("status")
    if not isinstance(status, str):
        raise ValueError("invalid provider event")  # noqa: TRY004 — uniform input validation contract
    observed_at = utc(payload.get("timestamp")).isoformat()
    store._time(observed_at)
    with store._transaction():
        _schema(store)
        binding = store.conn.execute(
            "SELECT request_id, provider_call_id FROM voice_vonage_bindings "
            "WHERE vonage_call_id=?",
            (call_id,),
        ).fetchone()
        if not binding:
            raise ValueError("provider call association required")
        record = store.get(binding["request_id"])
        if record["provider_call_id"] != binding["provider_call_id"]:
            raise ValueError("provider call association mismatch")
        if status not in STATUS_MAP:
            return "ignored"
        req = record["request"]
        event_id = "vonage:" + digest(
            {"uuid": call_id, "status": status, "timestamp": observed_at}
        )
        return store.record_lifecycle(
            event_id=event_id,
            request_id=req["request_id"],
            asset_id=req["asset_id"],
            snapshot_id=req["snapshot_id"],
            provider_call_id=binding["provider_call_id"],
            status=STATUS_MAP[status],
            observed_at=observed_at,
        )


def ingest_slng_call_end(store, client, body, authorization, *, token):
    """Authenticated wake signal: only the subsequent SLNG GET supplies call facts.

    Configure a dedicated Vault-backed bearer token on an SLNG system API Request
    tool and source call_id from system arguments. Other callback fields, including
    statuses, end reasons, answers and request IDs, are deliberately not evidence.
    """
    if not isinstance(token, str) or len(token) < 32 or any(c.isspace() for c in token):
        raise ValueError(
            "a dedicated event token of at least 32 characters is required"
        )
    if not isinstance(authorization, str) or not hmac.compare_digest(
        authorization.encode(), ("Bearer " + token).encode()
    ):
        raise PermissionError("unauthorized")
    payload = _body(body)
    call_id = payload.get("call_id")
    identifier(call_id, "provider_call_id")
    row = store.conn.execute(
        "SELECT request_id FROM voice_calls WHERE provider_call_id=?", (call_id,)
    ).fetchone()
    if not row:
        raise ValueError("provider call association required")
    return store.sync(client, row["request_id"]) | {"request_id": row["request_id"]}
