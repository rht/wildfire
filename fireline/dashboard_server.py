"""Read-only localhost API for a CoordinationStore or explicitly labelled demo."""
import argparse
import asyncio
import json
import sqlite3
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from .dashboard_public import public_state

DESIGN = Path(__file__).resolve().parents[1] / 'design'
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
           'Referrer-Policy': 'no-referrer'}


def _revision_states(store, cursor):
    """Accept full envelopes or coordination-update-1 records with a full refresh."""
    updates = store.updates(max(0, cursor))
    current = store.state()
    if current is None:
        raise ValueError('state unavailable')
    if any(item.get('schema_version') == 'coordination-update-1' and
           not isinstance(item.get('refresh'), dict) for item in updates):
        return [current] if current['revision'] != cursor else []
    states = [item.get('refresh', item) for item in updates]
    states = sorted((s for s in states if s['revision'] > cursor), key=lambda s: s['revision'])
    if cursor > current['revision'] or (not states and current['revision'] > cursor):
        states = [current]
    return states


class CoordinationDatabase:
    """Read-only adapter to CoordinationStore's committed revision table.

    Each read uses its own short connection; no migrations, writes, or refreshes.
    A missing/uninitialized database produces the same unavailable state as the API.
    """
    def __init__(self, path):
        self.uri = Path(path).resolve().as_uri() + '?mode=ro'

    def _read(self, query, parameters=()):
        conn = sqlite3.connect(self.uri, uri=True, timeout=1)
        try:
            return conn.execute(query, parameters).fetchall()
        finally:
            conn.close()

    def state(self):
        rows = self._read('SELECT payload FROM coordination_revisions ORDER BY revision DESC LIMIT 1')
        return json.loads(rows[0][0]) if rows else None

    def updates(self, after_revision=0):
        return [json.loads(row[0]) for row in self._read(
            'SELECT event FROM coordination_revisions WHERE revision>? ORDER BY revision',
            (after_revision,))]


def create_app(store, *, poll_interval=0.5):
    """Inject a read-only store exposing state() and updates(after_revision)."""
    async def state(request):
        try:
            return JSONResponse(public_state(store.state()), headers=HEADERS)
        except Exception:
            return JSONResponse({'error': 'state_unavailable'}, status_code=503, headers=HEADERS)

    async def updates(ws):
        origin = ws.headers.get('origin')
        if origin and urlsplit(origin).netloc != ws.headers.get('host'):
            await ws.close(code=1008)
            return
        try:
            cursor = int(ws.query_params.get('after_revision', '-1'))
            if cursor < -1:
                raise ValueError()
        except ValueError:
            await ws.close(code=1008)
            return
        await ws.accept()
        heartbeat_at = monotonic()
        try:
            while True:
                try:
                    for item in _revision_states(store, cursor):
                        state = public_state(item)
                        if state['revision'] == cursor:
                            continue
                        await ws.send_json(state)
                        cursor = state['revision']
                    if monotonic() - heartbeat_at >= 10:
                        await ws.send_json({'type': 'heartbeat', 'revision': cursor})
                        heartbeat_at = monotonic()
                except Exception:
                    await ws.send_json({'type': 'error', 'code': 'state_unavailable'})
                    await ws.close(code=1011)
                    return
                try:
                    message = await asyncio.wait_for(ws.receive(), timeout=poll_interval)
                    if message['type'] == 'websocket.disconnect':
                        return
                except TimeoutError:
                    pass
        except WebSocketDisconnect:
            return

    async def page(request):
        return FileResponse(DESIGN / 'ui-mockup.html', headers=HEADERS)

    async def script(request):
        name = request.path_params['name']
        if name not in ('dashboard-client.js', 'dashboard-view.js'):
            return JSONResponse({'error': 'not_found'}, status_code=404)
        return FileResponse(DESIGN / name, media_type='text/javascript', headers=HEADERS)

    app = Starlette(routes=[Route('/', page), Route('/api/state', state),
                            WebSocketRoute('/api/updates', updates), Route('/{name}', script)])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--demo', action='store_true', help='explicit offline illustration; no calls')
    source.add_argument('--database', type=Path, help='existing CoordinationStore database; read-only')
    parser.add_argument('--port', type=int, default=8521)
    args = parser.parse_args()
    from .dashboard_demo import DemoStore
    import uvicorn
    store = DemoStore() if args.demo else CoordinationDatabase(args.database)
    uvicorn.run(create_app(store), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
