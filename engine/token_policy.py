"""Backend-only switches; saved settings and UI need no migration."""


def enabled(config, feature):
    policy = config.get('token_optimization', True)
    return policy is not False and (not isinstance(policy, dict) or policy.get(feature, True) is not False)
