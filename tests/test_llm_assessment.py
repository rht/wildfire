import json
from copy import deepcopy

import pytest

from fireline.incident_runtime import IncidentRuntime, demo_trigger
from fireline.llm import FakeResponse, TextBlock
from fireline.llm_assessment import assess_snapshot
from fireline.snapshot import validate_snapshot
from tests.test_incident_runtime import NOW, settings


def snapshot(tmp_path):
    runtime = IncidentRuntime(tmp_path / "base", settings())
    runtime.trigger(demo_trigger(NOW))
    return runtime._load()["snapshot"]


class Model:
    model = "test-model"

    def __init__(self, invalid=False):
        self.calls = []
        self.invalid = invalid

    def create(self, *, system, messages, tools):
        item = json.loads(messages[0]["content"])
        self.calls.append(item)
        answer = {
            "asset_id": item["asset"]["asset_id"],
            "value_score": 0.91,
            "replacement_value_eur": 1234567,
            "damage_ratios": [0.1, 0.3, 0.7],
            "risk_score": 0.8,
            "mobility_concern": "likely",
            "evacuation_min": 12,
            "confidence": 0.9,
            "reasoning": "Illustrative estimate based on asset type.",
            "assumptions": ["Current headcount is supplied, not inferred."],
            "evidence_fields": ["asset_type"],
        }
        if self.invalid:
            answer["burn_probability"] = 0.99
        return FakeResponse(
            content=[TextBlock(text=json.dumps(answer))], stop_reason="end_turn"
        )


def test_every_location_assessed_values_applied_forecast_preserved_and_cache_replayed(
    tmp_path,
):
    snap = snapshot(tmp_path)
    original = deepcopy(snap)
    model = Model()
    opts = {"mode": "live", "concurrency": 2}
    result = assess_snapshot(snap, {}, tmp_path / "cache.sqlite", opts, backend=model)
    assert len(model.calls) == len(snap["assets"])
    assert snap == original
    assert not validate_snapshot(result)
    for before, after in zip(snap["assets"], result["assets"]):
        assert after["asset_id"] == before["asset_id"]
        assert after["value_score"] == 0.91
        assert after["replacement_value_eur"] == 1234567
        assert after["expected_loss_eur_mid"] == round(
            before["burn_probability"] * 0.3 * 1234567
        )
        assert after["fire_arrival_at"] == before["fire_arrival_at"]
        assert after["estimated_occupancy"] == before["estimated_occupancy"]
        assert after["evacuation_min"] == before["evacuation_min"]
        assert after["llm_assessment"]["status"] == "assessed"
    again = assess_snapshot(snap, {}, tmp_path / "cache.sqlite", opts, backend=model)
    assert again == result
    assert len(model.calls) == len(snap["assets"])


def test_invalid_model_claims_fail_closed_without_fabricating_forecasts(tmp_path):
    snap = snapshot(tmp_path)
    result = assess_snapshot(
        snap, {}, tmp_path / "cache.sqlite", {"mode": "live"}, backend=Model(True)
    )
    assert not validate_snapshot(result)
    assert all(a["llm_assessment"]["status"] == "failed" for a in result["assets"])
    assert all("llm_assessment_failed" in a["review_reasons"] for a in result["assets"])
    assert [a["value_score"] for a in result["assets"]] == [
        a["value_score"] for a in snap["assets"]
    ]


def test_missing_key_does_not_invoke_fake(tmp_path, monkeypatch):
    snap = snapshot(tmp_path)
    monkeypatch.setattr("fireline.llm.live_llm", lambda: None)
    result = assess_snapshot(snap, {}, tmp_path / "cache.sqlite", {"mode": "live"})
    assert all(a["llm_assessment"]["status"] == "unavailable" for a in result["assets"])


def test_disabled_mode_is_unchanged_and_invalid_config_rejected(tmp_path):
    snap = snapshot(tmp_path)
    assert (
        assess_snapshot(snap, {}, tmp_path / "cache.sqlite", {"mode": "disabled"})
        == snap
    )
    for opts in (
        {"mode": "bad"},
        {"mode": "live", "concurrency": True},
        {"mode": "live", "concurrency": 0},
    ):
        with pytest.raises(ValueError):
            assess_snapshot(snap, {}, tmp_path / "cache.sqlite", opts)


def test_runtime_feeds_assessed_values_and_durations_to_both_algorithms(tmp_path):
    cfg = settings()
    cfg["llm_assessment"] = {"mode": "live"}
    model = Model()
    runtime = IncidentRuntime(tmp_path, cfg, assessment_backend=model)
    state = runtime.trigger(demo_trigger(NOW))
    assert len(model.calls) == 3
    assert all(a["value_score"] == 0.91 for a in state["assets"])
    assert all(a["llm_assessment"]["status"] == "assessed" for a in state["assets"])
    assert state["plan"]["response"]["objective"]["value_units"] == 0.91
    assert len(state["contacts"]["ranked"]) == 3
    assert runtime.trigger(demo_trigger(NOW)) == state
    assert len(model.calls) == 3


def test_prepared_assessment_cannot_replace_newer_fire(tmp_path):
    cfg = settings()
    cfg["llm_assessment"] = {"mode": "live"}
    runtime = IncidentRuntime(tmp_path, cfg, assessment_backend=Model())
    prepared = runtime.prepare_trigger(demo_trigger(NOW))
    newer = demo_trigger(NOW)
    newer["trigger_id"] = "different-trigger"
    runtime.trigger(newer)
    state = runtime.state()
    with pytest.raises(ValueError, match="changed"):
        runtime.activate_trigger(prepared)
    assert runtime.state() == state


def test_estimated_evacuation_requires_occupancy_and_confirmed_route(tmp_path):
    snap = snapshot(tmp_path)
    a = snap["assets"][0]
    a["evacuation_min"] = a["evacuation_source"] = None
    a["people_at_risk_p10"] = a["people_at_risk_p50"] = None
    a["review_reasons"].append("evacuation_unknown")
    a["needs_review"] = True
    no_context = assess_snapshot(
        snap, {}, tmp_path / "cache.sqlite", {"mode": "live"}, backend=Model()
    )
    assert no_context["assets"][0]["evacuation_min"] is None
    assert "llm_evacuation_context_missing" in no_context["assets"][0]["review_reasons"]
    ops = settings()["operations"]
    for route in ops["evacuation_routes"]:
        route["evacuation_min"] = None
    with_context = assess_snapshot(
        snap,
        ops,
        tmp_path / "cache.sqlite",
        {"mode": "live"},
        backend=Model(),
    )
    assert with_context["assets"][0]["evacuation_min"] == 12
    from fireline.contact_priority import ContactPolicy, rank_contacts
    from fireline.incident_planning import scenario_from_snapshot

    scenario = scenario_from_snapshot(with_context, settings()["operations"], NOW)
    ranked = rank_contacts(scenario.locations, ContactPolicy(buffer_min=30))["ranked"]
    item = next(r for r in ranked if r["asset_id"] == a["asset_id"])
    assert item["components"]["evacuation_min"] == 12
    assert not validate_snapshot(with_context)


def test_public_projection_carries_assessment_but_redacts_contact_text(tmp_path):
    from fireline.dashboard_public import public_state

    cfg = settings() | {"llm_assessment": {"mode": "live"}}
    runtime = IncidentRuntime(tmp_path, cfg, assessment_backend=Model())
    state = runtime.trigger(demo_trigger(NOW))
    record = state["assets"][0]["llm_assessment"]
    record["assessment"]["reasoning"] = "Contact +12025550123; token=secret"
    record["private_provider_response"] = "hidden"
    public = public_state(state)["assets"][0]["llm_assessment"]
    assert public["status"] == "assessed"
    assert public["assessment"]["value_score"] == 0.91
    assert public["assessment"]["confidence"] == 0.9
    assert "+12025550123" not in str(public)
    assert "secret" not in str(public)
    assert "private_provider_response" not in public


def test_slow_assessment_does_not_hold_voice_mutation_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from starlette.testclient import TestClient

    from fireline.incident_server import create_app
    from tests.test_incident_server import AUTH, FIRE_TOKEN, RESULT_TOKEN

    entered, release = Event(), Event()

    class Slow(Model):
        def create(self, **kwargs):
            entered.set()
            assert release.wait(4)
            return super().create(**kwargs)

    cfg = settings()
    runtime = IncidentRuntime(tmp_path, cfg)
    runtime.trigger(demo_trigger(NOW))
    runtime.assessment_options = {"mode": "live"}
    runtime.assessment_backend = Slow()
    update = demo_trigger(NOW)
    update["trigger_id"] = "next-fire"
    with (
        TestClient(
            create_app(
                runtime,
                fire_token=FIRE_TOKEN,
                result_token=RESULT_TOKEN,
                tick_interval=0,
            )
        ) as client,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        future = pool.submit(client.post, "/api/fire", json=update, headers=AUTH)
        try:
            assert entered.wait(2)
            callback = pool.submit(
                client.post,
                "/api/simulate",
                json={"asset_id": "A", "status": "no_answer"},
                headers=AUTH,
            )
            assert callback.result(timeout=1).status_code == 200
        finally:
            release.set()
        assert future.result(timeout=3).status_code == 200


def test_nebius_plain_json_request_omits_empty_tools():
    from fireline.llm import NebiusLLM
    from tests.test_llm_nebius import StubSession

    session = StubSession([{"content": '{"ok":true}'}])
    NebiusLLM(api_key="test-key", session=session).create(
        "JSON only", [{"role": "user", "content": "test"}], []
    )
    assert "tools" not in session.requests[0]["body"]


def test_low_confidence_preserves_existing_facts_and_requires_review(tmp_path):
    class Uncertain(Model):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            answer = json.loads(response.content[0].text)
            answer["confidence"] = 0.2
            response.content[0].text = json.dumps(answer)
            return response

    snap = snapshot(tmp_path)
    result = assess_snapshot(
        snap, {}, tmp_path / "cache.sqlite", {"mode": "live"}, backend=Uncertain()
    )
    assert all(
        a["llm_assessment"]["status"] == "needs_review" for a in result["assets"]
    )
    assert [a["value_score"] for a in result["assets"]] == [
        a["value_score"] for a in snap["assets"]
    ]
    assert all(
        "llm_assessment_needs_review" in a["review_reasons"] for a in result["assets"]
    )


def test_trigger_cli_supports_longer_assessment_timeout(tmp_path, monkeypatch, capsys):
    from fireline.incident_server import main

    payload = tmp_path / "fire.json"
    payload.write_text("{}", encoding="utf-8")
    captured = {}

    def post(url, **kwargs):
        captured.update(kwargs)

        class Response:
            status_code = 200

            def json(self):
                return {"ok": True}

        return Response()

    monkeypatch.setenv("FIRE_TRIGGER_TOKEN", "test-only-" * 8)
    monkeypatch.setattr("requests.post", post)
    main(["trigger", "--file", str(payload), "--timeout", "900"])
    assert captured["timeout"] == (5, 900)
    assert captured["allow_redirects"] is False
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_known_route_total_cannot_be_shortened_by_model(tmp_path):
    snap = snapshot(tmp_path)
    a = snap["assets"][0]
    a["evacuation_min"] = a["evacuation_source"] = None
    a["people_at_risk_p10"] = a["people_at_risk_p50"] = None
    a["review_reasons"].append("evacuation_unknown")
    a["needs_review"] = True
    ops = settings()["operations"]
    ops["evacuation_routes"][0]["evacuation_min"] = 80
    result = assess_snapshot(
        snap, ops, tmp_path / "cache.sqlite", {"mode": "live"}, backend=Model()
    )
    assert result["assets"][0]["evacuation_min"] == 80
    assert "operational route" in result["assets"][0]["evacuation_source"]


def test_null_covered_quantile_remains_not_reached_when_duration_is_filled(tmp_path):
    snap = snapshot(tmp_path)
    a = snap["assets"][0]
    a["evacuation_min"] = a["evacuation_source"] = None
    a["people_at_risk_p10"] = a["people_at_risk_p50"] = None
    a["arrival_p50_at"] = None
    a["review_reasons"].append("evacuation_unknown")
    a["needs_review"] = True
    result = assess_snapshot(
        snap,
        settings()["operations"],
        tmp_path / "cache.sqlite",
        {"mode": "live"},
        backend=Model(),
    )
    assert result["assets"][0]["people_at_risk_p50"] == 0
