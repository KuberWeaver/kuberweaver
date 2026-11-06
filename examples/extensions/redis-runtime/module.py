"""Explicitly trusted adapter, installed disabled; no work at import time."""
from kuberweaver.redis_execution import RedisExecutor


class RedisRuntime(RedisExecutor):
    """Concrete adapter pins this installed package in host provider provenance.

    RedisExecutor also checks its entire inherited implementation and Redis
    blueprint immediately before each command, without replacing the worker's
    runner wrapper. No lifecycle/HTTP provider is impersonated here.
    """


def register(ctx):
    # Shared host journal/ownership is intentional, not an accidental namespace
    # escape: the serialized worker's rollback eligibility reads <data>/jobs.
    # This is privileged, trusted code, NOT a sandboxed extension.
    state_root = ctx.plugin_data_dir().parents[1]
    ctx.register('execution', 'redis', RedisRuntime(state_root), {
        'label': 'Redis isolated-lab runtime', 'isolated_lab_only': True,
        'protocol': 'redis-container-v1', 'requires_blueprints': ['redis'],
        'persistent_snapshots': True, 'remote_health_verified': False,
    })
