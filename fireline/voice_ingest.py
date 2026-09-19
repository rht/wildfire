"""Bearer-authenticated API Request tool receiver. No public server starts on import.

Mount make_app behind HTTPS; never expose the development server directly. Lifecycle,
confidence and transfer success cannot be supplied through this endpoint.
"""
import hmac
import json
from .voice_store import digest
from .voice_models import CallResult, ANSWER_FIELDS

RESULT_FIELDS = frozenset(('request_id', 'asset_id', 'snapshot_id', 'provider_call_id',
                          'evidence', 'contradictory', 'bad_audio', 'road_warning_acknowledged') + ANSWER_FIELDS)
MAX_BODY = 32768


def ingest(store, body, *, authorization, token):
    if not isinstance(token, str) or len(token) < 32 or any(c.isspace() for c in token):
        raise ValueError('a dedicated result token of at least 32 characters is required')
    if not isinstance(authorization, str) or not hmac.compare_digest(authorization.encode(), ('Bearer ' + token).encode()):
        raise PermissionError('unauthorized')
    if len(body) > MAX_BODY:
        raise ValueError('result body too large')
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) - RESULT_FIELDS:
            raise ValueError()
        # Authenticated tool output is model extraction, not independent confidence review.
        result = CallResult(**payload, observed_at=store.clock().isoformat(),
                            evidence_time_basis='receipt_only',
                            status='in_progress', source='SLNG API Request tool',
                            confidence=None, confidence_basis=None)
    except (ValueError, TypeError):
        raise ValueError('invalid interview result') from None
    outcome = store.record_result(result, delivery_id='delivery:' + digest(payload))
    return {'accepted': True, 'outcome': outcome, 'human_followup_required': True}


def make_app(store_factory, token):
    """WSGI app. Factory opens a per-request connection; body/auth are never logged."""
    def application(environ, start_response):
        status, response = '200 OK', {}
        store = None
        try:
            if environ.get('PATH_INFO') != '/voice/results':
                status, response = '404 Not Found', {'error': 'not_found'}
            elif environ.get('REQUEST_METHOD') != 'POST':
                status, response = '405 Method Not Allowed', {'error': 'method_not_allowed'}
            else:
                length = int(environ.get('CONTENT_LENGTH') or 0)
                if not 0 < length <= MAX_BODY:
                    raise ValueError()
                store = store_factory()
                response = ingest(store, environ['wsgi.input'].read(length),
                                  authorization=environ.get('HTTP_AUTHORIZATION'), token=token)
        except PermissionError:
            status, response = '401 Unauthorized', {'error': 'unauthorized'}
        except (ValueError, KeyError, TypeError):
            status, response = '400 Bad Request', {'error': 'invalid_result'}
        finally:
            if store is not None:
                store.close()
        data = json.dumps(response).encode()
        start_response(status, [('Content-Type', 'application/json'), ('Content-Length', str(len(data)))])
        return [data]
    return application
