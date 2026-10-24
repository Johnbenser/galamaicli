"""Request-local recovery for providers that buffer actions until fully generated."""
from contextlib import contextmanager
from contextvars import ContextVar

_CAPACITY = ContextVar('galam_output_capacity', default=None)


@contextmanager
def output_scope():
    token = _CAPACITY.set({})
    try:
        yield
    finally:
        _CAPACITY.reset(token)


def output_override(endpoint, model):
    return (_CAPACITY.get() or {}).get((endpoint, model))


def grow_output(endpoint, model, config, payload):
    """Grow only after a length finish, within the same connection's free context.

    No global settings change. Explicit opt-out and local manual tuning survive.
    Prepared-request validation and user task spending allowances still apply.
    """
    capacities = _CAPACITY.get()
    if capacities is None or config.get('adaptive_action_output', True) is False:
        return False
    if endpoint == 'Local Endpoint' and (config.get('local_tuning') or {}).get('mode') == 'manual':
        return False
    from engine.context_manager import estimate_tokens, model_limits
    from storage.endpoints import ENDPOINT_REGISTRY
    window, current, margin = model_limits(endpoint, model, config)
    provider = ENDPOINT_REGISTRY.get(endpoint, {}).get('models', {}).get(model, {}).get('max_output_tokens')
    ceiling = max(0, window - margin - estimate_tokens(payload) - 128)
    if provider:
        ceiling = min(ceiling, int(provider))
    wanted = min(current * 2, ceiling)
    if wanted <= current:
        return False
    capacities[(endpoint, model)] = wanted
    return True


def retain_output(endpoint, model, tokens):
    """Restore an internal recovery reservation for this human task only."""
    capacities = _CAPACITY.get()
    if capacities is not None and isinstance(tokens, int) and tokens > 0:
        capacities[(endpoint, model)] = tokens
