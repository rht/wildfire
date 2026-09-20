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
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from .dashboard_approvals import ApprovalStore, PlanChanged, envelope, plans_for
from .dashboard_plan_review import InvalidOrder, preview_order
from .dashboard_public import public_state

FRONTEND_DIST = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
           'Referrer-Policy': 'no-referrer'}
_BUILD_REQUIRED = ("Dashboard frontend is not built.\nRun:\n"
                   "  npm --prefix frontend ci\n"
                   "  npm --prefix frontend run build\n")


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


def create_app(store, *, poll_interval=0.5, approvals=None, analyst='@mirrdj',
               allow_demo_approvals=False):
    """Inject a read-only store exposing state() and updates(after_revision)."""
    if not isinstance(analyst, str) or not analyst.strip():
        raise ValueError('analyst must be a non-empty string')

    def approval_error(code, status_code):
        return JSONResponse({'error': code}, status_code=status_code, headers=HEADERS)

    def approval_identity(values, *, query=False, preview=False):
        required = {'source', 'incident_id', 'revision', 'snapshot_id'}
        if not query:
            required |= {'team_id', 'plan_version'}
            if preview:
                required.add('action_ids')
            elif 'action_ids' in values or 'review_version' in values:
                required |= {'action_ids', 'review_version'}
        if set(values) != required:
            raise ValueError('unexpected approval fields')
        source = values.get('source')
        incident_id = values.get('incident_id')
        snapshot_id = values.get('snapshot_id')
        revision = values.get('revision')
        if query:
            if not isinstance(revision, str) or not revision.isascii() or not revision.isdecimal():
                raise ValueError('revision must be an integer')
            revision = int(revision)
        if (source not in ('connected', 'design_demo')
                or any(not isinstance(value, str) or not value.strip()
                       for value in (incident_id, snapshot_id))
                or type(revision) is not int or revision < 0):
            raise ValueError('invalid approval identity')
        if not query and any(not isinstance(values.get(field), str)
                             or not values[field].strip()
                             for field in ('team_id', 'plan_version')):
            raise ValueError('invalid approval plan')
        if 'review_version' in values and (not isinstance(values['review_version'], str)
                                           or not values['review_version'].strip()):
            raise ValueError('invalid review version')
        return source, incident_id, revision, snapshot_id

    def approval_state(source, incident_id):
        if source == 'connected':
            try:
                return public_state(store.state())
            except Exception as error:
                raise OSError('connected approval state unavailable') from error
        if not allow_demo_approvals:
            raise PermissionError('design demo approvals are disabled')
        try:
            records = json.loads((FRONTEND_DIST / 'assets' / 'design-demo.json').read_text(
                encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise OSError('design demo approval state unavailable') from error
        if not isinstance(records, list):
            raise OSError('invalid design demo export')
        return next((record for record in records
                     if isinstance(record, dict)
                     and (record.get('incident_id') or record.get('id')
                          or record.get('scenario_id')) == incident_id), None)

    def current_approval(source, incident_id, revision, snapshot_id):
        current = approval_state(source, incident_id)
        return plans_for(current, source=source, incident_id=incident_id,
                         revision=revision, snapshot_id=snapshot_id)

    async def crew_approvals(request):
        if approvals is None:
            return approval_error('approval_unavailable', 503)
        if request.method == 'GET':
            pairs = list(request.query_params.multi_items())
            values = dict(pairs)
            if len(pairs) != len(values):
                return approval_error('invalid_request', 422)
            try:
                source, incident_id, revision, snapshot_id = approval_identity(
                    values, query=True)
                plans = current_approval(source, incident_id, revision, snapshot_id)
                result = envelope(approvals, plans, source=source, incident_id=incident_id,
                                  revision=revision, snapshot_id=snapshot_id,
                                  analyst=analyst)
            except PermissionError:
                return approval_error('source_not_allowed', 403)
            except PlanChanged:
                return approval_error('plan_changed', 409)
            except (KeyError, TypeError, ValueError):
                return approval_error('invalid_request', 422)
            except (OSError, sqlite3.Error):
                return approval_error('approval_unavailable', 503)
            return JSONResponse(result, headers=HEADERS)

        if request.headers.get('origin') != f'{request.url.scheme}://{request.headers.get("host")}':
            return approval_error('origin_not_allowed', 403)
        content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        if content_type != 'application/json':
            return approval_error('json_required', 415)
        try:
            values = await request.json()
            if not isinstance(values, dict):
                raise ValueError('approval body must be an object')
            is_preview = request.url.path == '/api/crew-plan-preview'
            source, incident_id, revision, snapshot_id = approval_identity(values, preview=is_preview)
            current = approval_state(source, incident_id)
            plans = plans_for(current, source=source, incident_id=incident_id,
                              revision=revision, snapshot_id=snapshot_id)
            plan = next((item for item in plans if item['team_id'] == values['team_id']), None)
            if plan is None or not plan['can_confirm']:
                return approval_error('plan_not_confirmable', 422)
            if plan['plan_version'] != values['plan_version']:
                return approval_error('plan_changed', 409)
            preview = None
            if is_preview or 'review_version' in values:
                previous = approvals.approval(source=source, incident_id=incident_id,
                                              team_id=plan['team_id'], plan_version=plan['plan_version'])
                if (not is_preview and previous
                        and previous.get('review_version') == values['review_version']
                        and previous.get('reviewed_plan', {}).get('action_ids') == values['action_ids']
                        and previous['reviewed_plan'].get('revision') == revision):
                    return JSONResponse(envelope(approvals, plans, source=source,
                        incident_id=incident_id, revision=revision, snapshot_id=snapshot_id,
                        analyst=analyst), headers=HEADERS)
                preview = preview_order(current, source=source, incident_id=incident_id,
                                        revision=revision, snapshot_id=snapshot_id,
                                        team_id=plan['team_id'], plan_version=plan['plan_version'],
                                        action_ids=values['action_ids'],
                                        prior_approval_id=previous['approval_id'] if previous else None)
                if is_preview:
                    return JSONResponse(preview, headers=HEADERS)
                if preview['review_version'] != values['review_version']:
                    return approval_error('plan_changed', 409)
                if not preview['can_confirm']:
                    return JSONResponse({'error': 'plan_not_confirmable',
                                         'blockers': preview['blockers']},
                                        status_code=422, headers=HEADERS)
            approvals.confirm(source=source, incident_id=incident_id,
                              snapshot_id=snapshot_id, team_id=plan['team_id'],
                              plan_version=plan['plan_version'], analyst=analyst,
                              review_version=preview['review_version'] if preview else None,
                              reviewed_plan=preview['reviewed_plan'] if preview else None)
            result = envelope(approvals, plans, source=source, incident_id=incident_id,
                              revision=revision, snapshot_id=snapshot_id, analyst=analyst)
        except PermissionError:
            return approval_error('source_not_allowed', 403)
        except PlanChanged:
            return approval_error('plan_changed', 409)
        except InvalidOrder as error:
            return JSONResponse({'error': 'invalid_order',
                                 'blockers': [{'code': 'invalid_order', 'reason': str(error)}]},
                                status_code=422, headers=HEADERS)
        except (json.JSONDecodeError, UnicodeError, KeyError, TypeError, ValueError):
            return approval_error('invalid_request', 422)
        except (OSError, sqlite3.Error):
            return approval_error('approval_unavailable', 503)
        return JSONResponse(result, headers=HEADERS)

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
        index = FRONTEND_DIST / 'index.html'
        if not index.is_file():
            return PlainTextResponse(_BUILD_REQUIRED, status_code=503, headers=HEADERS)
        return FileResponse(index, media_type='text/html', headers=HEADERS)

    async def asset(request):
        asset_root = (FRONTEND_DIST / 'assets').resolve()
        candidate = (asset_root / request.path_params['path']).resolve()
        try:
            candidate.relative_to(asset_root)
        except ValueError:
            return JSONResponse({'error': 'not_found'}, status_code=404)
        if not candidate.is_file():
            return JSONResponse({'error': 'not_found'}, status_code=404)
        return FileResponse(candidate, headers=HEADERS)

    app = Starlette(routes=[Route('/', page), Route('/api/state', state),
                            Route('/api/crew-approvals', crew_approvals,
                                  methods=['GET', 'POST']),
                            Route('/api/crew-plan-preview', crew_approvals, methods=['POST']),
                            WebSocketRoute('/api/updates', updates),
                            Route('/assets/{path:path}', asset)])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--demo', action='store_true', help='explicit offline illustration; no calls')
    source.add_argument('--database', type=Path, help='existing CoordinationStore database; read-only')
    parser.add_argument('--port', type=int, default=8521)
    parser.add_argument('--approvals-database', type=Path,
                        default=Path('data/dashboard-approvals.sqlite3'))
    parser.add_argument('--analyst', default='@mirrdj')
    args = parser.parse_args()
    if args.database and args.database.resolve() == args.approvals_database.resolve():
        parser.error('--approvals-database must differ from the coordination --database')
    from .dashboard_demo import DemoStore
    import uvicorn
    store = DemoStore() if args.demo else CoordinationDatabase(args.database)
    approvals = ApprovalStore(args.approvals_database)
    uvicorn.run(create_app(store, approvals=approvals, analyst=args.analyst,
                           allow_demo_approvals=args.demo),
                host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
