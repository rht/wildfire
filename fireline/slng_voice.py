"""SLNG managed agents REST adapter, per official agents.oas.yaml (2026-09-19).

No network at import/construction. No automatic retries, redirects or response logging.
Browser sessions, agent creation and outbound dispatch are separate explicit operations.
"""
from dataclasses import dataclass, field
import math
import os
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4
import requests
from .env import load_env
from .voice_interview import interview_prompt
from .route_guidance import road_warning_brief
from .voice_models import ANSWER_FIELDS, text

BASE_URL = 'https://api.agents.slng.ai'


def uuid(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid SLNG identifier') from None


@dataclass(frozen=True)
class SlngConfig:
    api_key: str | None = field(default=None, repr=False)
    agent_id: str | None = None
    outbound_connection_id: str | None = None
    timeout: tuple[float, float] = (5, 20)

    def __post_init__(self):
        if self.api_key is not None and (not isinstance(self.api_key, str)
                or not self.api_key.strip() or any(c.isspace() for c in self.api_key)):
            raise ValueError('invalid SLNG_API_KEY')
        for value in (self.agent_id, self.outbound_connection_id):
            if value is not None:
                uuid(value)
        if len(self.timeout) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) or n <= 0 for n in self.timeout):
            raise ValueError('invalid HTTP timeout')

    @classmethod
    def from_env(cls):
        load_env()
        return cls(api_key=os.getenv('SLNG_API_KEY') or None,
                   agent_id=os.getenv('SLNG_AGENT_ID') or None,
                   outbound_connection_id=os.getenv('SLNG_OUTBOUND_CONNECTION_ID') or None)


class ProviderError(RuntimeError):
    """Sanitized provider error. outcome_unknown requires manual reconciliation."""


class SlngClient:
    def __init__(self, config, *, transport=None):
        self.config = config
        self.transport = transport if transport is not None else requests.Session()

    def configuration_status(self):
        missing = [key for key, value in (('SLNG_API_KEY', self.config.api_key),
                   ('SLNG_AGENT_ID', self.config.agent_id)) if not value]
        return {'status': 'not_configured' if missing else 'configured_unverified', 'missing': missing,
                'outbound_configured': bool(self.config.outbound_connection_id)}

    def _path(self, suffix):
        if not self.config.agent_id:
            raise ValueError('SLNG_AGENT_ID is required')
        return f'/v1/agents/{uuid(self.config.agent_id)}/{suffix}'.rstrip('/')

    def _http(self, method, path, payload=None):
        if not self.config.api_key:
            raise ValueError('SLNG_API_KEY is not configured')
        try:
            response = self.transport.request(method, BASE_URL + path,
                headers={'Authorization': f'Bearer {self.config.api_key}', 'Content-Type': 'application/json'},
                json=payload, timeout=self.config.timeout, allow_redirects=False)
        except requests.RequestException:
            raise ProviderError('outcome_unknown' if method == 'POST' else 'provider_unavailable') from None
        if not 200 <= response.status_code < 300:
            code = 'outcome_unknown' if method == 'POST' and response.status_code >= 500 else 'provider_rejected'
            raise ProviderError(f'{code}: HTTP {response.status_code}')
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except (ValueError, requests.RequestException):
            raise ProviderError('outcome_unknown' if method == 'POST' else 'invalid_provider_response') from None

    def create_agent(self, configuration):
        return self._http('POST', '/v1/agents', configuration)

    def create_web_session(self, request):
        body = self._http('POST', self._path('web-sessions'), {'arguments': call_arguments(request)})
        try:
            uuid(body['call_id'])
            for key in ('room_name', 'livekit_url', 'livekit_token'):
                text(body[key], key, 16384)
            duration = body['max_session_seconds']
            if isinstance(duration, bool) or not isinstance(duration, int) or duration < 60:
                raise ValueError()
        except (KeyError, ValueError):
            raise ProviderError('outcome_unknown: invalid session response') from None
        return body  # token is private; never include in logs or public CLI output

    def dispatch(self, request, *, approved_target=None):
        if request.input_mode != 'live' or approved_target != request.contact_number:
            raise ValueError('explicit live request and approved target required')
        if not self.config.outbound_connection_id:
            raise ValueError('SLNG outbound connection is required on the configured agent')
        arguments = call_arguments(request)
        configuration = self._http('GET', self._path(''))
        if (configuration.get('id') != self.config.agent_id or
                configuration.get('sip_outbound_trunk_id') != self.config.outbound_connection_id):
            raise ValueError('agent outbound connection does not match configured connection')
        validate_agent_templates(configuration, arguments)
        body = self._http('POST', self._path('calls'),
                          {'phone_number': request.contact_number, 'arguments': arguments})
        try:
            uuid(body['call_id'])
        except (KeyError, ValueError):
            raise ProviderError('outcome_unknown: invalid dispatch response') from None
        return body

    def get_call(self, call_id):
        body = self._http('GET', self._path(f'calls/{uuid(call_id)}'))
        if body.get('id') != call_id or body.get('agent_id') != self.config.agent_id:
            raise ValueError('provider association mismatch')
        return body


def call_arguments(request):
    arguments = {'request_id': request.request_id, 'asset_id': request.asset_id,
            'snapshot_id': request.snapshot_id, 'incident_brief': request.incident_brief,
            'scenario_notice': 'SIMULATION' if request.input_mode != 'live' else 'Analyst-authorized contact',
            'language': request.language, 'road_warning_brief': road_warning_brief(request.road_warnings)}
    # Provider limits apply to the rendered road text as well as the incident brief.
    # Never truncate restrictions or drop identity bindings to fit the payload.
    if (len(arguments) > 32 or any(len(k) > 64 or not isinstance(v, str) or len(v) > 1024
                                  for k, v in arguments.items())
            or sum(len(v) for v in arguments.values()) > 8192):
        raise ValueError('SLNG argument limits exceeded; review the call briefing')
    return arguments


def validate_agent_templates(configuration, arguments):
    """Require an advertised binding for every argument before starting a call."""
    variables = configuration.get('template_variables')
    if not isinstance(variables, dict) or not arguments.keys() <= variables.keys():
        raise ValueError('incompatible SLNG agent templates; deploy the dynamic package')
    for name, metadata in variables.items():
        if not isinstance(metadata, dict) or not isinstance(metadata.get('required'), bool):
            raise ValueError('invalid SLNG agent template metadata')
        if metadata['required'] and name not in arguments:
            raise ValueError('required SLNG agent template argument is missing')


def agent_configuration(request, *, name, region, models, tool_refs=None, outbound_connection_id=None):
    for key in ('stt', 'llm', 'tts', 'tts_voice'):
        text(models.get(key), key)
    text(name, 'name', 255)
    text(region, 'region', 64)
    prompt = (interview_prompt(request, template=True) +
              '\nInternal call binding: request_id={{request_id}}; snapshot_id={{snapshot_id}}. '
              'Never speak these internal identifiers or change the call binding.')
    config = dict(name=name, system_prompt=prompt, greeting='I am an AI readiness assistant. {{scenario_notice}}. May I confirm your location?',
                  language=request.language, region=region, models=dict(models),
                  tool_mode='shared', tool_refs=list(tool_refs or []), mcp_refs=[],
                  template_defaults={'scenario_notice': 'SIMULATION', 'road_warning_brief': road_warning_brief([])})
    if outbound_connection_id:
        config['sip_outbound_trunk_id'] = uuid(outbound_connection_id)
    return config


def result_tool_configuration(url, secret_name):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError('result endpoint must be a literal HTTPS URL')
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*', secret_name):
        raise ValueError('invalid Vault secret name')
    properties = {key: {'type': 'string'} for key in
                  ('request_id', 'asset_id', 'snapshot_id', 'provider_call_id')}
    properties.update({key: {'type': ['boolean', 'null']} for key in ANSWER_FIELDS + ('road_warning_acknowledged',)})
    properties.update(contradictory={'type': 'boolean'}, bad_audio={'type': 'boolean'},
                      evidence={'type': 'object', 'properties': {
                          key: {'type': 'string'} for key in ANSWER_FIELDS + ('help_needs', 'preparation_remaining', 'instruction_received', 'road_warning_acknowledged')},
                          'additionalProperties': False})
    return dict(name='submit_interview', description='Record evidenced household answers. Submit immediately when a person is requested. Unknown answers are null. Never invent evidence or confidence.',
        tool_type='api_request', config=dict(type='api_request', url=url, http_method='POST',
        auth={'type': 'bearer', 'secret_name': secret_name},
        parameters={'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False},
        strict=True, webhook_format='raw', timeout_seconds=10, wait_for_response=True,
        response={'show_to_llm': True, 'instructions': 'Recorded for human review only; this never confirms evacuation or a connected transfer.'}))


def result_tool_attachment(tool_id, version):
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError('published tool version required')
    return dict(attachment_id=str(uuid4()), tool_id=uuid(tool_id), version=version, invocation='model',
                argument_overrides={**{key: '{{' + key + '}}' for key in ('request_id', 'asset_id', 'snapshot_id')},
                                    'provider_call_id': '{{@call_id}}'})
