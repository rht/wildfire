"""End-to-end HTTP/WS tests use synthetic incidents and offline transports only."""

import importlib
import json
import time
from datetime import timedelta

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from fireline.incident_runtime import IncidentRuntime, demo_trigger
from tests.test_call_events import API_KEY, APP, SECRET, VONAGE, signed
from tests.test_incident_runtime import NOW, settings
from tests.test_voice_provider import AGENT, CALL, Transport
from tests.test_voice_provider import client as provider_client

FIRE_TOKEN = "synthetic-fire-token-at-least-32-characters"
RESULT_TOKEN = "synthetic-results-token-at-least-32-characters"
WAKE_TOKEN = "synthetic-slng-event-token-at-least-32-characters"
DASHBOARD_TOKEN = "synthetic-dashboard-token-at-least-32-characters"
AUTH = {"Authorization": "Bearer " + FIRE_TOKEN}


def app(runtime, **options):
    return importlib.import_module("fireline.incident_server").create_app(
        runtime,
        fire_token=FIRE_TOKEN,
        result_token=RESULT_TOKEN,
        tick_interval=0,
        **options,
    )


def runtime(tmp_path, **cfg):
    return IncidentRuntime(tmp_path, settings() | cfg)


def bind_call(r, vonage=False):
    from fireline.call_events import bind_vonage_call

    saved = r._load()
    coordinator = r._coordinator(saved)
    record = coordinator.voice.get(r.state()["calls"][0]["request_id"])
    coordinator.voice.bind(record["request_id"], CALL)
    if vonage:
        bind_vonage_call(
            coordinator.voice, request_id=record["request_id"], vonage_call_id=VONAGE
        )
    coordinator.close()
    return record["request"]


def test_fire_authentication_duplicate_trigger_and_dashboard_stream(tmp_path):
    r = runtime(tmp_path)
    with TestClient(app(r, poll_interval=0.01)) as c:
        trigger = demo_trigger(NOW)
        assert c.get("/api/state").status_code == 503
        assert c.post("/api/fire", json=trigger).status_code == 401
        assert r.state() is None
        first = c.post("/api/fire", json=trigger, headers=AUTH)
        assert first.status_code == 200
        assert first.json()["input_mode"] == "synthetic"
        assert len(first.json()["calls"]) == 3
        assert "+1202555" not in first.text
        assert c.post("/api/fire", json=trigger, headers=AUTH).json() == first.json()
        revision = first.json()["revision"]
        with c.websocket_connect(f"/api/updates?after_revision={revision - 1}") as ws:
            assert ws.receive_json()["revision"] == revision
            simulated = c.post(
                "/api/simulate",
                json={"asset_id": "A", "status": "no_answer"},
                headers=AUTH,
            )
            assert simulated.status_code == 200
            assert ws.receive_json()["revision"] == simulated.json()["revision"]
        with c.websocket_connect(f"/api/updates?after_revision={revision}") as ws:
            latest = ws.receive_json()
            assert (
                next(x for x in latest["calls"] if x["asset_id"] == "A")["status"]
                == "no_answer"
            )
            assert any(
                t["kind"] == "human_callback" and t["asset_id"] == "A"
                for t in latest["tasks"]
            )


def test_connected_incident_dashboard_has_separate_persistent_approval_store(tmp_path):
    r = runtime(tmp_path)
    with TestClient(app(r)) as client:
        state = client.post('/api/fire', json=demo_trigger(NOW), headers=AUTH).json()
        response = client.get('/api/crew-approvals', params={
            'source': 'connected', 'incident_id': state.get('incident_id') or state['scenario_id'],
            'snapshot_id': state['snapshot_id'], 'revision': str(state['revision'])})
        assert response.status_code == 200
        assert response.json()['events'] == []
        assert (tmp_path / 'dashboard-approvals.sqlite3').is_file()


def test_interview_receiver_authenticates_and_refreshes_public_state(tmp_path):
    r = runtime(tmp_path)
    r.trigger(demo_trigger(NOW))
    req = bind_call(r)
    payload = {k: req[k] for k in ("request_id", "asset_id", "snapshot_id")}
    payload.update(
        provider_call_id=CALL,
        can_self_evacuate=False,
        evidence={"can_self_evacuate": "Synthetic participant: I need help."},
    )
    with TestClient(app(r)) as c:
        assert c.post("/voice/results", json=payload, headers=AUTH).status_code == 401
        result = c.post(
            "/voice/results",
            json=payload,
            headers={"Authorization": "Bearer " + RESULT_TOKEN},
        )
        assert result.status_code == 200
        state = c.get("/api/state").json()
        call = next(
            row for row in state["calls"] if row["request_id"] == req["request_id"]
        )
        assert call["reported_needs_assistance"] is True
        assert "Synthetic participant" not in json.dumps(state)


def test_signed_vonage_event_produces_no_answer_and_followup_over_http(tmp_path):
    r = runtime(tmp_path)
    r.trigger(demo_trigger(NOW))
    req = bind_call(r, vonage=True)
    options = {"signature_secret": SECRET, "api_key": API_KEY, "application_id": APP}
    data = json.dumps(
        {"uuid": VONAGE, "status": "unanswered", "timestamp": NOW.isoformat()}
    ).encode()
    auth = {"Authorization": signed(data, claims={"iat": int(NOW.timestamp())})}
    with TestClient(app(r, vonage=options)) as c:
        assert c.post("/voice/events/vonage", content=data).status_code == 401
        assert (
            c.post("/voice/events/vonage", content=data, headers=auth).json()["outcome"]
            == "accepted"
        )
        state = c.get("/api/state").json()
        call = next(
            row for row in state["calls"] if row["request_id"] == req["request_id"]
        )
        assert call["status"] == "no_answer" and call["can_self_evacuate"] is None
        assert (
            c.post("/voice/events/vonage", content=data, headers=auth).json()["outcome"]
            == "duplicate"
        )
        assert c.get("/api/state").json()["revision"] == state["revision"]


def test_slng_event_polls_provider_and_ignores_supplied_claims(tmp_path):
    r = runtime(tmp_path)
    r.trigger(demo_trigger(NOW))
    req = bind_call(r)
    transport = Transport(
        {
            "id": CALL,
            "agent_id": AGENT,
            "status": "no_answer",
            "updated_at": NOW.isoformat(),
            "arguments": {k: req[k] for k in ("request_id", "asset_id", "snapshot_id")},
        }
    )
    r.client = provider_client(transport)
    with TestClient(app(r, slng_event_token=WAKE_TOKEN)) as c:
        response = c.post(
            "/voice/events/slng",
            json={"call_id": CALL, "status": "completed", "can_self_evacuate": True},
            headers={"Authorization": "Bearer " + WAKE_TOKEN},
        )
        assert response.status_code == 200
        assert response.json()["outcome"] == "accepted"
        assert (
            next(
                x
                for x in c.get("/api/state").json()["calls"]
                if x["request_id"] == req["request_id"]
            )["status"]
            == "no_answer"
        )
        assert transport.calls[0][0] == "GET"


def test_request_limits_generic_errors_and_missing_provider_configuration(tmp_path):
    r = runtime(tmp_path)
    with TestClient(app(r)) as c:
        assert (
            c.post(
                "/api/fire", content=b"{" * (1024 * 1024 + 1), headers=AUTH
            ).status_code
            == 413
        )
        response = c.post(
            "/api/fire", content=b'{"secret":"do-not-echo"}', headers=AUTH
        )
        assert response.status_code == 400 and "do-not-echo" not in response.text
        assert c.post("/voice/events/vonage", content=b"{}").status_code == 503
        assert c.post("/voice/events/slng", content=b"{}").status_code == 503
        assert c.get("/.env").status_code == 404
        assert c.get("/health").json()["worker_error"] is None


def test_public_dashboard_requires_login_for_rest_websocket_and_html(tmp_path):
    r = runtime(tmp_path)
    r.trigger(demo_trigger(NOW))
    server = app(r, dashboard_token=DASHBOARD_TOKEN, allowed_hosts=["incident.example"])
    with TestClient(server, base_url="https://incident.example") as c:
        assert c.get("/api/state").status_code == 401
        assert c.get("/", follow_redirects=False).status_code == 303
        with (
            pytest.raises(WebSocketDisconnect),
            c.websocket_connect("wss://incident.example/api/updates"),
        ):
            pass
        assert (
            c.post(
                "/login", data={"token": "wrong"}, follow_redirects=False
            ).status_code
            == 401
        )
        assert (
            c.post(
                "/login", data={"token": DASHBOARD_TOKEN}, follow_redirects=False
            ).status_code
            == 303
        )
        assert c.get("/api/state").status_code == 200
        cookie = c.cookies.get("incident_session")
        assert DASHBOARD_TOKEN not in cookie
        with c.websocket_connect("wss://incident.example/api/updates") as ws:
            assert ws.receive_json()["input_mode"] == "synthetic"
        assert (
            c.post(
                "/api/simulate", json={"asset_id": "A", "status": "no_answer"}
            ).status_code
            == 401
        )
        assert c.get("/api/state", headers={"host": "evil.example"}).status_code == 400


def test_public_host_without_dashboard_token_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        app(runtime(tmp_path), allowed_hosts=["*"])


def test_background_worker_advances_synthetic_queue_without_external_dispatch(tmp_path):
    r = runtime(tmp_path, call_mode="simulate_no_answer")
    server = importlib.import_module("fireline.incident_server").create_app(
        r, fire_token=FIRE_TOKEN, result_token=RESULT_TOKEN, tick_interval=0.02
    )
    with TestClient(server) as c:
        assert (
            c.post("/api/fire", json=demo_trigger(NOW), headers=AUTH).status_code == 200
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            calls = c.get("/api/state").json()["calls"]
            if calls and all(x["status"] == "no_answer" for x in calls):
                break
            time.sleep(0.02)
        assert all(x["status"] == "no_answer" for x in calls)
        assert c.get("/health").json()["worker_error"] is None


def test_worker_failure_is_generic_and_a_later_tick_recovers(tmp_path):
    r = runtime(tmp_path)
    original = r.tick

    def broken_tick():
        raise RuntimeError("private upstream credential do-not-echo")

    r.tick = broken_tick
    server = importlib.import_module("fireline.incident_server").create_app(
        r, fire_token=FIRE_TOKEN, result_token=RESULT_TOKEN, tick_interval=0.01
    )
    with TestClient(server) as c:
        deadline = time.monotonic() + 1
        while (
            time.monotonic() < deadline
            and c.get("/health").json()["worker_error"] is None
        ):
            time.sleep(0.01)
        health = c.get("/health")
        assert health.json() == {"status": "degraded", "worker_error": "tick_failed"}
        assert "credential" not in health.text
        r.tick = original
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and c.get("/health").json()["worker_error"]:
            time.sleep(0.01)
        assert c.get("/health").json()["status"] == "ok"


def test_dashboard_cookie_cannot_be_forged_or_reused_as_write_authorization(tmp_path):
    r = runtime(tmp_path)
    with TestClient(app(r, dashboard_token=DASHBOARD_TOKEN)) as c:
        c.cookies.set("incident_session", f"{int(time.time())}.{'a' * 32}.{'b' * 64}")
        assert c.get("/api/state").status_code == 401
        assert (
            c.post(
                "/login",
                data={"token": DASHBOARD_TOKEN},
                headers={"Origin": "https://evil.example"},
                follow_redirects=False,
            ).status_code
            == 401
        )
        assert (
            c.post(
                "/api/fire",
                json=demo_trigger(NOW),
                headers={"Authorization": "Bearer " + DASHBOARD_TOKEN},
            ).status_code
            == 401
        )
        assert r.state() is None


def test_cli_resolves_catalog_relative_to_settings_and_rejects_missing_secrets(
    tmp_path, monkeypatch
):
    server = importlib.import_module("fireline.incident_server")
    config = tmp_path / "config.json"
    config.write_text('{"catalog_file":"catalog.json"}')
    assert server._settings(config)["catalog_file"] == str(tmp_path / "catalog.json")
    monkeypatch.delenv("FIRE_TRIGGER_TOKEN", raising=False)
    with pytest.raises(SystemExit) as error:
        server.main(
            ["serve", "--settings", str(config), "--data-dir", str(tmp_path / "data")]
        )
    assert error.value.code == 1
    assert not (tmp_path / "data").exists()


def test_fire_requests_wait_for_running_tick_before_mutating_incident(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    tick_entered, release_tick, trigger_entered = Event(), Event(), Event()

    class ObservedRuntime(IncidentRuntime):
        def tick(self):
            tick_entered.set()
            assert release_tick.wait(2)
            return super().tick()

        def trigger(self, value):
            trigger_entered.set()
            return super().trigger(value)

    r = ObservedRuntime(tmp_path, settings())
    server = importlib.import_module("fireline.incident_server").create_app(
        r, fire_token=FIRE_TOKEN, result_token=RESULT_TOKEN, tick_interval=0.01
    )
    with TestClient(server) as c:
        assert tick_entered.wait(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                c.post, "/api/fire", json=demo_trigger(NOW), headers=AUTH
            )
            try:
                assert not trigger_entered.wait(0.05)
            finally:
                release_tick.set()
            assert future.result(timeout=2).status_code == 200
        assert len(c.get("/api/state").json()["calls"]) == 3


def test_local_cli_binds_existing_request_without_http_tokens(tmp_path, monkeypatch):
    from fireline.call_events import ingest_vonage_event

    server = importlib.import_module("fireline.incident_server")
    data_dir = tmp_path / "incident"
    r = runtime(data_dir)
    r.trigger(demo_trigger(NOW))
    req = bind_call(r)
    config = tmp_path / "settings.json"
    config.write_text(json.dumps(settings()))
    monkeypatch.delenv("FIRE_TRIGGER_TOKEN", raising=False)
    args = [
        "bind-vonage",
        "--settings",
        str(config),
        "--data-dir",
        str(data_dir),
        "--request-id",
        req["request_id"],
        "--call-id",
        VONAGE,
    ]
    server.main(args)
    server.main(args)  # unchanged local association is idempotent
    data = json.dumps(
        {"uuid": VONAGE, "status": "unanswered", "timestamp": NOW.isoformat()}
    ).encode()
    coordinator = r._coordinator(r._load())
    try:
        assert (
            ingest_vonage_event(
                coordinator.voice,
                data,
                signed(data, claims={"iat": int(NOW.timestamp())}),
                signature_secret=SECRET,
                api_key=API_KEY,
                application_id=APP,
            )
            == "accepted"
        )
        assert coordinator.voice.get(req["request_id"])["status"] == "no_answer"
    finally:
        coordinator.close()


def test_bind_cli_does_not_create_a_missing_incident_directory(tmp_path, monkeypatch):
    server = importlib.import_module("fireline.incident_server")
    monkeypatch.delenv("FIRE_TRIGGER_TOKEN", raising=False)
    config = tmp_path / "settings.json"
    config.write_text(json.dumps(settings()))
    missing = tmp_path / "missing"
    with pytest.raises(SystemExit) as error:
        server.main(
            [
                "bind-vonage",
                "--settings",
                str(config),
                "--data-dir",
                str(missing),
                "--request-id",
                "unknown",
                "--call-id",
                VONAGE,
            ]
        )
    assert error.value.code == 1
    assert not missing.exists()


def test_provider_notification_without_lifecycle_is_acknowledged_as_ignored(tmp_path):
    class IgnoredWakeRuntime(IncidentRuntime):
        # Models the adapter contract when a wake has no fresh lifecycle fact.
        def provider_event(self, provider, body, authorization, options):
            return {"request_id": "existing-request"}

    r = IgnoredWakeRuntime(tmp_path, settings())
    with TestClient(app(r, slng_event_token=WAKE_TOKEN)) as c:
        response = c.post(
            "/voice/events/slng",
            json={"call_id": CALL},
            headers={"Authorization": "Bearer " + WAKE_TOKEN},
        )
        assert response.status_code == 200
        assert response.json() == {"outcome": "ignored"}


def test_demo_cli_rebases_synthetic_crew_time_and_preserves_existing_incident(
    tmp_path, monkeypatch
):
    import uvicorn

    server = importlib.import_module("fireline.incident_server")
    monkeypatch.setenv("FIRE_TRIGGER_TOKEN", FIRE_TOKEN)
    monkeypatch.setenv("VOICE_RESULT_TOKEN", RESULT_TOKEN)
    monkeypatch.delenv("SLNG_EVENT_TOKEN", raising=False)
    future = NOW + timedelta(days=50)
    monkeypatch.setattr(server, "demo_trigger", lambda: demo_trigger(future))
    applications = []
    server_options = []

    def capture_server(application, **kwargs):
        applications.append(application)
        server_options.append(kwargs)

    # Replace the blocking socket server; incident setup and SQLite writes are real.
    monkeypatch.setattr(uvicorn, "run", capture_server)
    args = ["demo", "--data-dir", str(tmp_path / "demo"), "--tick-interval", "0"]
    server.main(args)
    assert server_options[0]["workers"] == 1
    r = applications[0].state.runtime
    saved = r._load()
    assert saved["epoch"] == future.isoformat()
    assert all(
        team["current_location"]["observed_at"] == saved["epoch"]
        for team in saved["operations"]["teams"]
    )
    first = r.state()
    monkeypatch.setattr(
        server, "demo_trigger", lambda: demo_trigger(future + timedelta(days=1))
    )
    server.main(args)
    assert applications[-1].state.runtime.state() == first
    assert applications[-1].state.runtime._load()["operations"] == saved["operations"]


def test_dashboard_login_refuses_plain_http_and_only_sets_secure_cookie(tmp_path):
    server = app(
        runtime(tmp_path),
        dashboard_token=DASHBOARD_TOKEN,
        allowed_hosts=["incident.example"],
    )
    with TestClient(server, base_url="http://incident.example") as c:
        response = c.post(
            "/login", data={"token": DASHBOARD_TOKEN}, follow_redirects=False
        )
        assert response.status_code == 400
        assert "set-cookie" not in response.headers
    with TestClient(server, base_url="https://incident.example") as c:
        response = c.post(
            "/login", data={"token": DASHBOARD_TOKEN}, follow_redirects=False
        )
        assert response.status_code == 303
        assert "secure" in response.headers["set-cookie"].lower()
