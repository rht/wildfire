"""One incident service: authenticated inputs, persistent plans and dashboard updates.

Run one worker process. API mutations and background ticks share a lock. The UI
uses a separate login secret when exposed beyond localhost; provider secrets are
never included in public state, HTTP errors or request access logs.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from .dashboard_public import public_state
from .dashboard_server import HEADERS, CoordinationDatabase
from .dashboard_server import create_app as dashboard_app
from .incident_runtime import IncidentRuntime, demo_trigger

LOCAL_HOSTS = ["127.0.0.1", "localhost", "[::1]", "testserver"]
INPUT_PATHS = {
    "/api/fire",
    "/api/simulate",
    "/voice/results",
    "/voice/events/slng",
    "/voice/events/vonage",
}
SESSION_SECONDS = 8 * 60 * 60
MAX_TRIGGER_BODY = 1024 * 1024
MAX_EVENT_BODY = 32768
LOGIN_HTML = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ResponsAra · Sign in</title><body><main><h1>ResponsAra</h1>
<p>Enter the dashboard access token supplied by your coordinator.</p>
<form action="/login" method="post"><label>Access token
<input name="token" type="password" autocomplete="current-password" required></label>
<button type="submit">Sign in</button></form></main></body></html>"""


def _token(value):
    if not isinstance(value, str) or len(value) < 32 or any(c.isspace() for c in value):
        raise ValueError("dedicated access tokens must have at least 32 characters")
    return value


def _authenticated(authorization, token):
    return isinstance(authorization, str) and hmac.compare_digest(
        authorization.encode(), ("Bearer " + token).encode()
    )


def _session(token):
    payload = str(int(time.time())) + "." + secrets.token_hex(16)
    signature = hmac.new(
        token.encode(), ("incident-session:" + payload).encode(), hashlib.sha256
    ).hexdigest()
    return payload + "." + signature


def _valid_session(value, token):
    try:
        issued, nonce, signature = value.split(".")
        timestamp = int(issued)
        if not 0 <= time.time() - timestamp <= SESSION_SECONDS or len(nonce) != 32:
            return False
        expected = hmac.new(
            token.encode(),
            ("incident-session:" + issued + "." + nonce).encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature.encode(), expected.encode())
    except (AttributeError, TypeError, ValueError):
        return False


class DashboardSession:
    """Only dashboard reads use cookies; write endpoints always require own auth."""

    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or scope[
            "path"
        ] in INPUT_PATHS | {"/login", "/health"}:
            return await self.app(scope, receive, send)
        conn = HTTPConnection(scope)
        if _valid_session(conn.cookies.get("incident_session"), self.token):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            response = (
                RedirectResponse("/login", status_code=303, headers=HEADERS)
                if scope["path"] == "/"
                else JSONResponse({"error": "unauthorized"}, 401, headers=HEADERS)
            )
            await response(scope, receive, send)


class BodyTooLarge(ValueError):
    pass


async def _body(request, limit):
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > limit:
            raise BodyTooLarge()
        content.extend(chunk)
    if not content:
        raise ValueError("empty request")
    return bytes(content)


def _object(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise TypeError()
    return value


def create_app(
    runtime,
    *,
    fire_token,
    result_token,
    slng_event_token=None,
    vonage=None,
    dashboard_token=None,
    allowed_hosts=None,
    tick_interval=2,
    poll_interval=0.5,
):
    """Compose the existing read-only dashboard with serialized operational inputs.

    tick_interval=0 disables background work for controlled tests/manual refresh.
    Vonage options: signature_secret, api_key, optional application_id. Supply only
    after the actual SIP/Voice API path and exact local call UUID are established.
    """
    fire_token, result_token = _token(fire_token), _token(result_token)
    if fire_token == result_token:
        raise ValueError("trigger and result tokens must be different")
    if slng_event_token is not None:
        _token(slng_event_token)
        if slng_event_token in (fire_token, result_token):
            raise ValueError("provider event token must be separate")
    hosts = list(allowed_hosts or LOCAL_HOSTS)
    if any(host not in LOCAL_HOSTS for host in hosts) and dashboard_token is None:
        raise ValueError("public dashboard hosts require a separate dashboard token")
    if dashboard_token is not None:
        _token(dashboard_token)
        if dashboard_token in (fire_token, result_token, slng_event_token):
            raise ValueError("dashboard token must be separate")
    if (
        type(tick_interval) not in (int, float)
        or not math.isfinite(tick_interval)
        or tick_interval < 0
    ):
        raise ValueError("invalid worker interval")
    lock = asyncio.Lock()
    stop = asyncio.Event()

    async def run(operation, *args):
        async with lock:
            return await asyncio.to_thread(operation, *args)

    async def worker():
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick_interval)
                return
            except TimeoutError:
                pass
            try:
                await run(runtime.tick)
                app.state.worker_error = None
            except Exception:  # noqa: BLE001 — isolate failed ticks and never expose provider secrets
                app.state.worker_error = "tick_failed"

    @asynccontextmanager
    async def lifespan(app):
        stop.clear()
        task = asyncio.create_task(worker()) if tick_interval else None
        try:
            yield
        finally:
            stop.set()
            if task:
                await task

    async def operational(request):
        path = request.url.path
        try:
            if path in ("/api/fire", "/api/simulate"):
                if not _authenticated(request.headers.get("authorization"), fire_token):
                    raise PermissionError()
                payload = _object(await _body(request, MAX_TRIGGER_BODY))
                if path == "/api/fire":
                    result = await run(runtime.trigger, payload)
                else:
                    if set(payload) - {"asset_id", "status", "answers"}:
                        raise ValueError()
                    result = await run(
                        runtime.simulate,
                        payload["asset_id"],
                        payload["status"],
                        payload.get("answers"),
                    )
                return JSONResponse(public_state(result), headers=HEADERS)
            if path == "/voice/results":
                if not _authenticated(
                    request.headers.get("authorization"), result_token
                ):
                    raise PermissionError()
                data = await _body(request, MAX_EVENT_BODY)
                result = await run(
                    runtime.interview,
                    data,
                    request.headers.get("authorization"),
                    result_token,
                )
                return JSONResponse(public_state(result), headers=HEADERS)
            provider = request.path_params["provider"]
            options = (
                {"token": slng_event_token}
                if provider == "slng" and slng_event_token
                else vonage
                if provider == "vonage"
                else None
            )
            if not options:
                return JSONResponse(
                    {"error": "provider_not_configured"}, 503, headers=HEADERS
                )
            if provider == "slng" and not _authenticated(
                request.headers.get("authorization"), slng_event_token
            ):
                raise PermissionError()
            data = await _body(request, MAX_EVENT_BODY)
            result = await run(
                runtime.provider_event,
                provider,
                data,
                request.headers.get("authorization"),
                options,
            )
            return JSONResponse(
                {
                    "outcome": result
                    if isinstance(result, str)
                    else result.get("lifecycle", "ignored")
                },
                headers=HEADERS,
            )
        except PermissionError:
            return JSONResponse({"error": "unauthorized"}, 401, headers=HEADERS)
        except BodyTooLarge:
            return JSONResponse({"error": "body_too_large"}, 413, headers=HEADERS)
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            return JSONResponse({"error": "invalid_input"}, 400, headers=HEADERS)
        except Exception:  # noqa: BLE001 — public boundary must not expose provider exceptions
            return JSONResponse({"error": "operation_failed"}, 502, headers=HEADERS)

    async def health(request):
        return JSONResponse(
            {
                "status": "degraded" if app.state.worker_error else "ok",
                "worker_error": app.state.worker_error,
            },
            headers=HEADERS,
        )

    async def login(request):
        if dashboard_token is None:
            return RedirectResponse("/", status_code=303, headers=HEADERS)
        if request.method == "GET":
            return HTMLResponse(LOGIN_HTML, headers=HEADERS)
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"error": "unauthorized"}, 401, headers=HEADERS)
        try:
            data = await _body(request, 8192)
            token = parse_qs(data.decode(), strict_parsing=True).get("token", [""])[0]
            if not hmac.compare_digest(token.encode(), dashboard_token.encode()):
                raise PermissionError()
        except (ValueError, UnicodeError, PermissionError):
            return JSONResponse({"error": "unauthorized"}, 401, headers=HEADERS)
        response = RedirectResponse("/", status_code=303, headers=HEADERS)
        response.set_cookie(
            "incident_session",
            _session(dashboard_token),
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return response

    app = dashboard_app(
        CoordinationDatabase(runtime.database), poll_interval=poll_interval
    )
    app.router.routes[0:0] = [
        Route("/api/fire", operational, methods=["POST"]),
        Route("/api/simulate", operational, methods=["POST"]),
        Route("/voice/results", operational, methods=["POST"]),
        Route("/voice/events/{provider}", operational, methods=["POST"]),
        Route("/health", health),
        Route("/login", login, methods=["GET", "POST"]),
    ]
    app.router.lifespan_context = lifespan
    app.state.worker_error = None
    app.state.runtime = runtime
    app.user_middleware = [
        item for item in app.user_middleware if item.cls is not TrustedHostMiddleware
    ]
    if dashboard_token is not None:
        app.add_middleware(DashboardSession, token=dashboard_token)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    return app


def _settings(path):
    settings = _object(Path(path).read_bytes())
    if settings.get("catalog_file"):
        catalog = Path(settings["catalog_file"])
        if not catalog.is_absolute():
            settings["catalog_file"] = str(
                (Path(path).resolve().parent / catalog).resolve()
            )
    return settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "demo"):
        command = commands.add_parser(name)
        command.add_argument("--data-dir", type=Path, required=True)
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=8521)
        command.add_argument("--allowed-host", action="append", default=[])
        command.add_argument("--tick-interval", type=float, default=2)
        if name == "serve":
            command.add_argument("--settings", type=Path, required=True)
    trigger = commands.add_parser("trigger")
    trigger.add_argument("--url", default="http://127.0.0.1:8521")
    trigger.add_argument("--file", type=Path, required=True)
    binding = commands.add_parser(
        "bind-vonage", help="bind an exact Vonage UUID to an existing SLNG request"
    )
    binding.add_argument("--data-dir", type=Path, required=True)
    binding.add_argument("--settings", type=Path, required=True)
    binding.add_argument("--request-id", required=True)
    binding.add_argument(
        "--call-id", required=True, help="actual Vonage UUID for the outbound PSTN leg"
    )
    args = parser.parse_args(argv)
    fire_token = os.environ.get("FIRE_TRIGGER_TOKEN")
    try:
        if args.command == "bind-vonage":
            from .call_events import bind_vonage_call

            if (
                not (args.data_dir / "runtime.sqlite").is_file()
                or not (args.data_dir / "coordination.sqlite").is_file()
            ):
                raise ValueError("existing incident directory required")
            runtime = IncidentRuntime(args.data_dir, _settings(args.settings))
            saved = runtime._load()
            if not saved:
                raise ValueError("no active incident")
            coordinator = runtime._coordinator(saved)
            try:
                bind_vonage_call(
                    coordinator.voice,
                    vonage_call_id=args.call_id,
                    request_id=args.request_id,
                )
            finally:
                coordinator.close()
            print("Vonage association recorded; no call was placed.")
            return
        _token(fire_token)
        if args.command == "trigger":
            import requests

            response = requests.post(
                args.url.rstrip("/") + "/api/fire",
                data=args.file.read_bytes(),
                headers={
                    "Authorization": "Bearer " + fire_token,
                    "Content-Type": "application/json",
                },
                timeout=(5, 60),
                allow_redirects=False,
            )
            if response.status_code != 200:
                parser.exit(
                    1,
                    "Trigger rejected; inspect the service configuration and input.\n",
                )
            print(json.dumps(response.json(), indent=2))
            return
        if args.command == "demo":
            initial_trigger = demo_trigger()
            root = Path(__file__).resolve().parents[1] / "fixtures/end_to_end"
            contacts = [
                {"asset_id": aid, "contact_number": f"+1202555012{i}"}
                for i, aid in enumerate("ABC")
            ]
            settings = {
                "input_mode": "synthetic",
                "call_mode": "simulate_no_answer",
                "search_radius_m": 10000,
                "catalog_file": str(root / "catalog.json"),
                "operations": _object((root / "operations.json").read_bytes()),
                "contacts": contacts,
                "approvals": [
                    dict(c, snapshot_id="end-to-end-demo-0001") for c in contacts
                ],
            }
            for team in settings["operations"].get("teams", []):
                if team.get("current_location"):
                    team["current_location"]["observed_at"] = initial_trigger["as_of"]
        else:
            settings = _settings(args.settings)
        client = None
        if settings.get("call_mode") == "live" or os.environ.get("SLNG_EVENT_TOKEN"):
            from .slng_voice import SlngClient, SlngConfig

            client = SlngClient(SlngConfig.from_env())
        runtime = IncidentRuntime(args.data_dir, settings, client=client)
        if args.command == "demo" and runtime.state() is None:
            runtime.trigger(initial_trigger)
        vonage = None
        if os.environ.get("VONAGE_SIGNATURE_SECRET"):
            vonage = {
                "signature_secret": os.environ["VONAGE_SIGNATURE_SECRET"],
                "api_key": os.environ.get("VONAGE_API_KEY"),
                "application_id": os.environ.get("VONAGE_APPLICATION_ID"),
            }
        hosts = list(dict.fromkeys(LOCAL_HOSTS + args.allowed_host))
        dashboard_token = os.environ.get("DASHBOARD_TOKEN")
        if args.host not in ("127.0.0.1", "localhost", "::1") and not dashboard_token:
            raise ValueError("public binding requires dashboard authentication")
        application = create_app(
            runtime,
            fire_token=fire_token,
            result_token=os.environ.get("VOICE_RESULT_TOKEN"),
            slng_event_token=os.environ.get("SLNG_EVENT_TOKEN"),
            vonage=vonage,
            dashboard_token=dashboard_token,
            allowed_hosts=hosts,
            tick_interval=args.tick_interval,
        )
        import uvicorn

        uvicorn.run(
            application, host=args.host, port=args.port, workers=1, access_log=False
        )
    except (ValueError, OSError, KeyError, TypeError):
        parser.exit(
            1,
            "Invalid service configuration: check settings, paths and dedicated environment tokens.\n",
        )


if __name__ == "__main__":
    main()
