"""Agent Router-only client identity and admission-error handling."""
from urllib.parse import urlsplit
import requests


class ClientAdmissionError(requests.exceptions.RequestException):
    pass


def is_agent_router_url(url):
    parsed = urlsplit(url)
    return parsed.scheme == 'https' and parsed.hostname == 'agentrouter.org'


def request_headers(endpoint, url, headers, stream, config):
    if endpoint != 'Agent Router' or not is_agent_router_url(url):
        return headers
    merged = dict(headers)
    defaults = {'User-Agent': 'GalamAI', 'originator': 'galamaicli',
                'X-Title': 'Galam.AI', 'HTTP-Referer': 'https://github.com/Johnbenser/galamaicli',
                'Accept': 'text/event-stream' if stream else 'application/json'}
    for name, value in defaults.items():
        if not any(key.lower() == name.lower() for key in merged):
            merged[name] = value
    # Keep provider-approved identity overrides backend-only. Authentication,
    # host and transport headers cannot be replaced through this option.
    allowed = {'user-agent', 'originator', 'x-title', 'http-referer', 'version', 'x-client-version'}
    configured = config.get('agent_router_headers') or {}
    if not isinstance(configured, dict):
        raise ValueError('agent_router_headers must be a mapping of approved client headers.')
    for name, value in configured.items():
        if not isinstance(name, str) or name.lower() not in allowed:
            raise ValueError('Unsupported Agent Router identity header.')
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError('Agent Router identity headers must be nonempty printable ASCII.')
        for key in list(merged):
            if key.lower() == name.lower():
                del merged[key]
        merged[name] = value
    return merged


def client_rejection(endpoint, url, data):
    if endpoint != 'Agent Router' or not is_agent_router_url(url):
        return ''
    if isinstance(data, dict):
        error = data.get('error') or {}
        kind = data.get('type', '')
        if isinstance(error, dict):
            kind = str(kind) + ' ' + str(error.get('type', ''))
            detail = str(error.get('message') or data.get('message', ''))
        else:
            detail = str(error)
    else:
        kind, detail = '', str(data)
    if 'unauthorized_client_error' not in (kind + ' ' + detail).lower() and 'unauthorized client detected' not in detail.lower():
        return ''
    # Do not equate client admission with token validity or retry every model.
    return ('Agent Router rejected this client identity (unauthorized_client_error). '
            'Request approved Galam/custom-client access and header requirements from Agent Router support. '
            'API key validity is unverified. Model fallback stopped because this is a client-access rejection.')
