"""Synthetic signed callbacks and real SQLite/provider adapters; no live calls."""

import base64
import hashlib
import hmac
import importlib
import json
from datetime import timedelta

import pytest

from tests.test_voice_interview import EPOCH, request
from tests.test_voice_provider import AGENT, CALL, Transport, client
from tests.test_voice_store import store

VONAGE = "aaaaaaaa-bbbb-cccc-dddd-0123456789ab"
SECRET = "synthetic-signature-secret-32-characters"
API_KEY = "synthetic-account"
APP = "aaaaaaaa-0000-4000-8000-0123456789ab"
TOKEN = "synthetic-wake-token-32-characters-long"
NOW = EPOCH + timedelta(minutes=10)


def api():
    return importlib.import_module("fireline.call_events")


def prepared(path=":memory:"):
    s = store(path, now=NOW)
    s.register(request())
    s.bind("req-B", CALL)
    api().bind_vonage_call(s, vonage_call_id=VONAGE, request_id="req-B")
    return s


def body(status="unanswered", **changes):
    return json.dumps(
        {"uuid": VONAGE, "status": status, "timestamp": NOW.isoformat()} | changes
    ).encode()


def signed(data, *, secret=SECRET, claims=None, header=None):
    payload = {
        "iss": "Vonage",
        "api_key": API_KEY,
        "application_id": APP,
        "iat": int(NOW.timestamp()),
        "jti": "synthetic-jwt-id",
        "payload_hash": hashlib.sha256(data).hexdigest(),
    } | (claims or {})
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(
        b"="
    )
    content = encode(header or {"alg": "HS256", "typ": "JWT"}) + b"." + encode(payload)
    signature = base64.urlsafe_b64encode(
        hmac.digest(secret.encode(), content, "sha256")
    ).rstrip(b"=")
    return "Bearer " + (content + b"." + signature).decode()


def ingest(s, data=None, authorization=None):
    data = data or body()
    return api().ingest_vonage_event(
        s,
        data,
        authorization or signed(data),
        signature_secret=SECRET,
        api_key=API_KEY,
        application_id=APP,
    )


def test_unanswered_retains_unknown_answers_and_open_human_task():
    s = prepared()
    assert ingest(s) == "accepted"
    assert s.get("req-B")["status"] == "no_answer"
    assert s.get("req-B")["result"] is None
    a = s.assessment("req-B")
    assert a.can_self_evacuate is None and a.transport_available is None
    assert a.acknowledged_road_warning_version is None and a.confidence is None
    assert s.human_tasks("B")[0]["status"] != "done"


def test_duplicate_persists_and_generic_completion_cannot_erase_unanswered(tmp_path):
    path = tmp_path / "calls.sqlite"
    s = prepared(path)
    assert ingest(s) == "accepted"
    s.close()
    s = store(path, now=NOW)
    assert ingest(s) == "duplicate"
    assert ingest(s, body("completed")) == "ignored"
    assert ingest(s, body("ringing", timestamp=EPOCH.isoformat())) == "ignored"
    assert s.get("req-B")["status"] == "no_answer"
    assert len(s.human_tasks("B")) == 1


@pytest.mark.parametrize(
    "status, expected",
    [
        ("ringing", "ringing"),
        ("answered", "in_progress"),
        ("busy", "no_answer"),
        ("failed", "failed"),
        ("cancelled", "failed"),
        ("completed", "completed"),
    ],
)
def test_provider_statuses_never_imply_answers(status, expected):
    s = prepared()
    assert ingest(s, body(status)) == "accepted"
    assert s.get("req-B")["status"] == expected
    assert s.get("req-B")["result"] is None


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "attacker"},
        {"api_key": "other-account"},
        {"application_id": "other-app"},
        {"application_id": None},
        {"payload_hash": None},
        {"payload_hash": "0" * 64},
        {"iat": None},
        {"iat": True},
        {"iat": int(NOW.timestamp()) - 301},
        {"iat": int(NOW.timestamp()) + 31},
        {"exp": int(NOW.timestamp()) - 1},
        {"nbf": int(NOW.timestamp()) + 31},
        {"jti": None},
    ],
)
def test_invalid_signed_claims_cannot_mutate_calls(claims):
    s = prepared()
    with pytest.raises(PermissionError, match="unauthorized"):
        ingest(s, authorization=signed(body(), claims=claims))
    assert s.get("req-B")["status"] == "queued"


@pytest.mark.parametrize("authorization", ["", "Bearer invalid", "Bearer a.b.c", None])
def test_missing_or_malformed_authentication_is_rejected(authorization):
    s = prepared()
    with pytest.raises(PermissionError):
        api().ingest_vonage_event(
            s,
            body(),
            authorization,
            signature_secret=SECRET,
            api_key=API_KEY,
            application_id=APP,
        )


def test_wrong_signature_algorithm_and_changed_body_are_rejected():
    s = prepared()
    for authorization in (
        signed(body(), secret="wrong"),
        signed(body(), header={"alg": "none"}),
        signed(body("completed")),
    ):
        with pytest.raises(PermissionError):
            ingest(s, authorization=authorization)
    assert s.get("req-B")["status"] == "queued"


def test_unknown_uuid_never_uses_callback_request_or_phone():
    s = prepared()
    data = body(
        uuid="bbbbbbbb-bbbb-cccc-dddd-0123456789ab",
        request_id="req-B",
        to=request().contact_number,
    )
    with pytest.raises(ValueError):
        ingest(s, data)
    assert s.get("req-B")["status"] == "queued"


def test_bindings_are_immutable_require_bound_call_and_survive_restart(tmp_path):
    path = tmp_path / "bindings.sqlite"
    s = prepared(path)
    s.register(request(request_id="req-C"))
    with pytest.raises(ValueError):
        api().bind_vonage_call(s, vonage_call_id=VONAGE, request_id="req-C")
    with pytest.raises(ValueError):
        api().bind_vonage_call(
            s, vonage_call_id="bbbbbbbb-bbbb-cccc-dddd-0123456789ab", request_id="req-C"
        )
    s.bind("req-C", "other-slng-call")
    with pytest.raises(ValueError):
        api().bind_vonage_call(s, vonage_call_id=VONAGE, request_id="req-C")
    with pytest.raises(ValueError):
        api().bind_vonage_call(
            s, vonage_call_id="bbbbbbbb-bbbb-cccc-dddd-0123456789ab", request_id="req-B"
        )
    s.close()
    s = store(path, now=NOW)
    api().bind_vonage_call(s, vonage_call_id=VONAGE, request_id="req-B")
    assert ingest(s) == "accepted"


@pytest.mark.parametrize(
    "changes",
    [
        {"timestamp": (NOW + timedelta(seconds=1)).isoformat()},
        {"timestamp": (EPOCH - timedelta(seconds=1)).isoformat()},
        {"timestamp": None},
        {"uuid": "not-a-uuid"},
        {"status": ["unanswered"]},
    ],
)
def test_invalid_lifecycle_payload_is_rejected_without_mutation(changes):
    s = prepared()
    with pytest.raises(ValueError):
        ingest(s, body(**changes))
    assert s.get("req-B")["status"] == "queued"


def test_unknown_status_is_ignored_without_asserting_call_success():
    s = prepared()
    assert ingest(s, body("machine")) == "ignored"
    assert s.get("req-B")["status"] == "queued"


def test_slng_notification_polls_real_adapter_ignoring_agent_lifecycle_claims():
    s = prepared()
    provider = {
        "id": CALL,
        "agent_id": AGENT,
        "status": "no_answer",
        "updated_at": NOW.isoformat(),
        "arguments": {
            "request_id": "req-B",
            "asset_id": "B",
            "snapshot_id": "snapshot-demo",
        },
    }
    transport = Transport(provider)
    data = json.dumps(
        {"call_id": CALL, "status": "completed", "can_self_evacuate": True}
    ).encode()
    response = api().ingest_slng_call_end(
        s, client(transport), data, "Bearer " + TOKEN, token=TOKEN
    )
    assert response["request_id"] == "req-B"
    assert response["lifecycle"] == "accepted"
    assert s.get("req-B")["status"] == "no_answer"
    assert s.get("req-B")["result"] is None
    assert transport.calls[0][0] == "GET"


def test_slng_wake_requires_dedicated_token_and_known_call():
    s = prepared()
    transport = Transport()
    with pytest.raises(PermissionError):
        api().ingest_slng_call_end(
            s, client(transport), b"{}", "Bearer wrong", token=TOKEN
        )
    with pytest.raises(ValueError):
        api().ingest_slng_call_end(
            s,
            client(transport),
            b'{"call_id":"unknown","request_id":"req-B"}',
            "Bearer " + TOKEN,
            token=TOKEN,
        )
    assert not transport.calls


def test_resigned_delivery_is_duplicate_and_cannot_reopen_finished_task():
    s = prepared()
    assert ingest(s) == "accepted"
    task = s.human_tasks("B")[0]
    # TaskStore owns workflow changes; a replay must never manufacture fresh work.
    s.conn.execute("UPDATE tasks SET status='done' WHERE task_id=?", (task["task_id"],))
    s.conn.commit()
    assert (
        ingest(
            s, authorization=signed(body(), claims={"jti": "different-delivery-jwt"})
        )
        == "duplicate"
    )
    assert s.human_tasks("B")[0]["status"] == "done"


@pytest.mark.parametrize(
    "data",
    [b"[]", b'{"uuid":"a","uuid":"b"}', b"not-json", b'{"value":NaN}', b"{" * 1500],
)
def test_signed_invalid_json_fails_without_leaking_payload(data):
    s = prepared()
    with pytest.raises(ValueError, match="invalid provider event"):
        ingest(s, data)
    assert s.get("req-B")["status"] == "queued"


def test_vonage_binding_and_callback_participate_in_coordinator_transaction():
    s = store(now=NOW)
    s.register(request())
    s.bind("req-B", CALL)
    with pytest.raises(RuntimeError), s._transaction():
        api().bind_vonage_call(s, vonage_call_id=VONAGE, request_id="req-B")
        assert ingest(s) == "accepted"
        raise RuntimeError("coordinator publication failed")
    assert s.get("req-B")["status"] == "queued"
    with pytest.raises(ValueError, match="association required"):
        ingest(s)
