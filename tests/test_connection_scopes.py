"""Agent scopes cover connection metadata, not credential ingestion."""
import pytest

from backend.agent_auth import permits, required_scope

IDENTITY = 'a' * 32


@pytest.mark.parametrize('method,path,scope', [
    ('GET', '/api/connection-drivers', 'read:infrastructure'),
    ('GET', '/api/connections', 'read:infrastructure'),
    ('GET', f'/api/connections/{IDENTITY}', 'read:infrastructure'),
    ('POST', '/api/connections', 'configure'),
    ('PATCH', f'/api/connections/{IDENTITY}', 'configure'),
    ('POST', f'/api/connections/{IDENTITY}/test', 'read:infrastructure'),
    ('POST', f'/api/connections/{IDENTITY}/discover', 'read:infrastructure'),
])
def test_explicit_connection_scopes(method, path, scope):
    assert required_scope(method, path) == scope
    assert not permits(['read:finops'], scope)


@pytest.mark.parametrize('method,path', [
    ('PUT', f'/api/connections/{IDENTITY}/credentials'),
    ('DELETE', f'/api/connections/{IDENTITY}'),
    ('POST', f'/api/connections/{IDENTITY}/execute'),
    ('GET', '/api/connections/not-an-identity'),
    ('POST', '/api/connections/../test'),
])
def test_unknown_operations_and_secret_ingestion_are_not_granted_to_agents(method, path):
    assert required_scope(method, path) is None
    assert not permits(['read', 'configure', 'approve'], required_scope(method, path))
