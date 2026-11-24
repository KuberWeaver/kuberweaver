"""MCP security and real stdio → HTTP integration tests (no live state)."""
import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    return importlib.import_module('backend.mcp_server')


def test_only_safe_administrator_origins_and_private_token_files(tmp_path):
    assert importlib.util.find_spec('backend.mcp_server') is not None, 'MCP adapter missing'
    m = module()
    token = tmp_path / 'token'
    token.write_text('test-private-bearer\n')
    token.chmod(0o600)
    for origin in ['http://127.0.0.1:8000', 'http://[::1]:8000']:
        assert m.ServerConfig(origin, token).base_url == origin
    for origin in ['http://localhost:8000', 'http://example.org', 'https://example.org',
                   'http://127.0.0.1/api', 'http://user:pass@127.0.0.1',
                   'http://127.0.0.1?x=1', 'ftp://127.0.0.1', 'http://127.0.0.1/#x']:
        with pytest.raises(ValueError):
            m.ServerConfig(origin, token)
    assert m.ServerConfig('https://api.example.org', token, allow_remote_https=True)
    token.chmod(0o644)
    with pytest.raises(ValueError, match='private'):
        m.ServerConfig('http://127.0.0.1', token)


@pytest.mark.parametrize('kind', ['symlink', 'oversized', 'multiline', 'directory'])
def test_invalid_token_file_never_discloses_contents(tmp_path, kind):
    m = module()
    token = tmp_path / 'token'
    token.write_text('private-marker')
    token.chmod(0o600)
    if kind == 'symlink':
        link = tmp_path / 'link'
        link.symlink_to(token)
        token = link
    elif kind == 'oversized':
        token.write_text('private-marker' * 500)
    elif kind == 'multiline':
        token.write_text('private-marker\nsecond-token')
    else:
        token = tmp_path
    with pytest.raises(ValueError) as failure:
        m.ServerConfig('http://127.0.0.1', token)
    assert 'private-marker' not in str(failure.value)


def test_typed_discovery_and_read_returns_structured_result(tmp_path):
    import httpx
    m = module()
    assert hasattr(m, 'create_server'), 'Domain tools missing'
    token = tmp_path / 'token'
    token.write_text('private-value')
    token.chmod(0o600)
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[{'id': 'sample-service'}])
    server = m.create_server(m.ServerConfig('http://127.0.0.1:8000', token),
                             transport=httpx.MockTransport(respond))
    async def check():
        tools = await server.list_tools()
        tool = next(t for t in tools if t.name == 'list_plugins')
        assert tool.inputSchema['additionalProperties'] is False
        assert tool.annotations.readOnlyHint is True
        assert tool.outputSchema
        result = await server.call_tool('list_plugins', {})
        assert result[1]['data'] == [{'id': 'sample-service'}]
    asyncio.run(check())
    assert requests[0].headers['authorization'] == 'Bearer private-value'
    assert requests[0].headers['x-request-id']
    assert requests[0].url.path == '/api/plugins'


READ_ROUTES = {
    'list_plugins': '/api/plugins', 'get_overview': '/api/overview',
    'get_topology': '/api/infrastructure', 'get_settings': '/api/settings',
    'get_finops': '/api/finops', 'get_diagnostics_cache': '/api/diagnostics',
    'list_audit': '/api/audit', 'list_integrations': '/api/integrations',
    'list_providers': '/api/providers', 'list_extensions': '/api/extensions',
    'get_operations': '/api/operations', 'list_jobs': '/api/jobs',
    'list_blueprints': '/api/blueprints',
    'get_notification_deliveries': '/api/operations/notifications',
    'list_connection_drivers': '/api/connection-drivers',
    'list_connections': '/api/connections',
}


def test_complete_domain_surface_exact_paths_types_and_annotations(tmp_path):
    import httpx
    from test_core_graph import graph
    token = tmp_path / 'token'
    token.write_text('private-value')
    token.chmod(0o600)
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    server = module().create_server(module().ServerConfig('http://127.0.0.1:8000', token),
                                     transport=httpx.MockTransport(respond))
    identity = 'a' * 32
    connection = {'label': 'Lab', 'driver': 'esxi', 'endpoint': 'https://192.0.2.10',
                  'secret_ref': 'esxi-lab', 'secrets_provider': 'openbao-local',
                  'tls_sha256': None, 'enabled': True}
    def normalized(body):
        # The tool serializes the validated Graph, so defaults it declares are part
        # of the request body; compare against the same canonical form.
        from backend.models import Graph
        return Graph.model_validate(body).model_dump()

    mutations = {
        'validate_plan': ({'graph': graph()}, 'POST', '/api/plans/validate', normalized(graph())),
        'save_blueprint': ({'name': 'Saved', 'graph': graph()}, 'POST', '/api/blueprints', {'name': 'Saved', 'graph': normalized(graph())}),
        'submit_job': ({'graph': graph()}, 'POST', '/api/jobs', {'graph': normalized(graph())}),
        'approve_job': ({'identity': identity}, 'POST', f'/api/jobs/{identity}/approve', None),
        'reject_job': ({'identity': identity}, 'POST', f'/api/jobs/{identity}/reject', None),
        'rollback_job': ({'identity': identity}, 'POST', f'/api/jobs/{identity}/rollback', None),
        'update_settings': ({'settings': {'policies': {'max_nodes': 7}}}, 'PATCH', '/api/settings', {'policies': {'max_nodes': 7}}),
        'update_provider_bindings': ({'bindings': {'inventory': 'builtin'}}, 'PATCH', '/api/providers', {'bindings': {'inventory': 'builtin'}}),
        'enable_extension': ({'identity': 'trusted-extension', 'trusted': True}, 'POST', '/api/extensions/trusted-extension/enable', {'trusted': True}),
        'disable_extension': ({'identity': 'trusted-extension'}, 'POST', '/api/extensions/trusted-extension/disable', None),
        'run_diagnostics': ({}, 'POST', '/api/diagnostics/run', None),
        'retry_notifications': ({}, 'POST', '/api/operations/notifications/retry', None),
        'issue_chatops_action': ({'job_id': identity, 'decision': 'reject'}, 'POST', '/api/chatops/actions', {'job_id': identity, 'decision': 'reject'}),
        'discover_connection': ({'identity': identity}, 'POST', f'/api/connections/{identity}/discover', None),
        'create_connection': ({'connection': connection}, 'POST', '/api/connections', connection),
        'update_connection': ({'identity': identity, 'patch': {'revision': 1, 'enabled': False}}, 'PATCH', f'/api/connections/{identity}', {'revision': 1, 'enabled': False}),
    }
    async def check():
        tools = {t.name: t for t in await server.list_tools()}
        assert set(tools) == set(READ_ROUTES) | set(mutations) | {'get_job', 'get_blueprint', 'get_connection'}
        for name, path in READ_ROUTES.items():
            await server.call_tool(name, {})
            assert requests[-1].url.path == path
            assert tools[name].annotations.readOnlyHint
        for name, path in [('get_job', '/api/jobs/'), ('get_blueprint', '/api/blueprints/'), ('get_connection', '/api/connections/')]:
            await server.call_tool(name, {'identity': identity})
            assert requests[-1].url.path == path + identity
        for name, (args, method, path, body) in mutations.items():
            await server.call_tool(name, args)
            request = requests[-1]
            assert request.method == method and request.url.path == path
            import json
            assert (json.loads(request.content) if request.content else None) == body
            assert not tools[name].annotations.readOnlyHint
            assert tools[name].annotations.destructiveHint == (name not in {'validate_plan', 'discover_connection'})
        assert all(t.inputSchema['additionalProperties'] is False for t in tools.values())
    asyncio.run(check())


def test_real_stdio_discovery_and_unauthenticated_http_denial(tmp_path):
    """Full protocol, real KuberWeaver Uvicorn subprocess, never TestClient transport."""
    assert (ROOT / 'scripts/mcp_server.py').exists(), 'stdio entry point missing'
    with real_api(tmp_path) as (base_url, human):
        token = tmp_path / 'denied-token'
        token.write_text('intentionally-invalid')
        token.chmod(0o600)
        async def check():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / 'scripts/mcp_server.py'), '--base-url', base_url, '--token-file', str(token)])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    names = {tool.name for tool in listing.tools}
                    assert len(names) == len(listing.tools)
                    assert {'list_plugins', 'list_connections', 'discover_connection'} <= names
                    denied = await session.call_tool('list_plugins', {})
                    assert denied.isError
                    assert 'HTTP 401' in denied.content[0].text
        asyncio.run(check())

from contextlib import contextmanager


def test_transport_total_deadline_not_only_socket_idle_timeout(tmp_path, monkeypatch):
    import httpx
    from mcp.server.fastmcp.exceptions import ToolError
    m = module()
    monkeypatch.setattr(m, 'REQUEST_DEADLINE', .01, raising=False)
    token = tmp_path / 'token'
    token.write_text('private-value')
    token.chmod(0o600)
    async def respond(request):
        await asyncio.sleep(.05)
        return httpx.Response(200, json={'slow': True})
    server = m.create_server(m.ServerConfig('http://127.0.0.1', token), transport=httpx.MockTransport(respond))
    async def check():
        with pytest.raises(ToolError, match='deadline'):
            await server.call_tool('list_plugins', {})
    asyncio.run(check())


@pytest.mark.parametrize('name,args', [
    ('list_plugins', {'url': 'http://private'}),
    ('get_job', {'identity': '../agent-tokens'}),
    ('save_blueprint', {'name': 'Missing graph'}),
    ('enable_extension', {'identity': 'test', 'trusted': False}),
    ('enable_extension', {'identity': 'test', 'trusted': 1}),
    ('update_settings', {'settings': {'unknown': {'secret': 'never-echo-input'}}}),
    ('update_provider_bindings', {'bindings': {'unregistered-kind': 'test'}}),
    ('issue_chatops_action', {'job_id': 'a' * 32, 'decision': 'execute'}),
])
def test_closed_strict_inputs_rejected_before_http(tmp_path, name, args):
    import httpx
    from mcp.server.fastmcp.exceptions import ToolError
    token = tmp_path / 'token'
    token.write_text('private-value')
    token.chmod(0o600)
    def respond(request):
        pytest.fail('Invalid parameters reached the API')
    server = module().create_server(module().ServerConfig('http://127.0.0.1', token), transport=httpx.MockTransport(respond))
    async def check():
        with pytest.raises(ToolError, match='Invalid domain tool parameters') as failure:
            await server.call_tool(name, args)
        assert 'never-echo-input' not in str(failure.value)
    asyncio.run(check())


@pytest.mark.parametrize('kind', ['redirect', 'forbidden', 'oversize', 'invalid-json', 'network'])
def test_transport_no_redirects_proxy_or_secret_error_echo(tmp_path, monkeypatch, kind):
    import httpx
    from mcp.server.fastmcp.exceptions import ToolError
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    token = tmp_path / 'token'
    token.write_text('private-value')
    token.chmod(0o600)
    requests = []
    def respond(request):
        requests.append(request)
        if kind == 'redirect':
            return httpx.Response(302, headers={'location': 'http://untrusted/secret-marker'}, text='secret-marker')
        if kind == 'forbidden':
            return httpx.Response(403, json={'secret': 'secret-marker'}, headers={'X-Secret': 'secret-marker'})
        if kind == 'oversize':
            return httpx.Response(200, content=b'x' * 1_048_577)
        if kind == 'network':
            raise httpx.ConnectError('private-value secret-marker', request=request)
        return httpx.Response(200, text='secret-marker')
    server = module().create_server(module().ServerConfig('https://127.0.0.1', token), transport=httpx.MockTransport(respond))
    async def check():
        with pytest.raises(ToolError) as failure:
            await server.call_tool('list_plugins', {})
        assert 'secret-marker' not in str(failure.value)
        assert 'private-value' not in str(failure.value)
    asyncio.run(check())
    assert len(requests) == 1


def test_real_stdio_bearer_read_mutation_and_scoped_approval_denial(tmp_path):
    """A human-issued admin token is still scope-limited; no mock auth or HTTP."""
    from test_core_graph import graph
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    with real_api(tmp_path) as (base_url, human):
        response = human.post('/api/agent-tokens', json={
            'label': 'isolated MCP test', 'role': 'admin',
            'scopes': ['read', 'plan', 'submit'], 'expires_in_seconds': 300})
        assert response.status_code == 201, response.text
        issued = response.json()
        token = tmp_path / 'scoped-token'
        token.write_text(issued['token'])
        token.chmod(0o600)
        async def check():
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / 'scripts/mcp_server.py'), '--base-url', base_url],
                env={'KUBERWEAVER_MCP_TOKEN_FILE': str(token),
                     'HTTP_PROXY': 'http://127.0.0.1:1', 'HTTPS_PROXY': 'http://127.0.0.1:1',
                     'ALL_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': '',
                     'SSL_CERT_FILE': '/nonexistent-do-not-use-environment-certificate'})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    names = {tool.name for tool in tools}
                    assert len(names) == len(tools)
                    assert {'list_plugins', 'list_connections', 'discover_connection'} <= names
                    plugins = await session.call_tool('list_plugins', {})
                    assert not plugins.isError
                    assert plugins.structuredContent['data'][0]['id'] == 'sample-service'
                    candidate = graph()
                    candidate['environment'] = 'production'
                    validation = await session.call_tool('validate_plan', {'graph': candidate})
                    assert not validation.isError, validation
                    assert validation.structuredContent['data']['allowed']
                    saved = await session.call_tool('save_blueprint', {'name': 'MCP protocol saved', 'graph': candidate})
                    assert not saved.isError, saved
                    identity = saved.structuredContent['data']['id']
                    assert human.get('/api/blueprints/' + identity).json()['name'] == 'MCP protocol saved'
                    submitted = await session.call_tool('submit_job', {'graph': candidate})
                    assert not submitted.isError, submitted
                    job = submitted.structuredContent['data']
                    assert job['status'] == 'pending_approval'
                    denied = await session.call_tool('approve_job', {'identity': job['id']})
                    assert denied.isError
                    assert 'Permission denied' in denied.content[0].text
                    assert 'HTTP 403' in denied.content[0].text
                    assert issued['token'] not in str(denied)
                    assert human.get('/api/jobs/' + job['id']).json()['status'] == 'pending_approval'
                    unsafe = await session.call_tool('get_job', {'identity': '../agent-tokens'})
                    assert unsafe.isError and 'Invalid domain tool parameters' in unsafe.content[0].text
                    # Revoke while the same MCP process is alive: every call reauthenticates.
                    assert human.delete('/api/agent-tokens/' + issued['metadata']['id']).status_code == 200
                    revoked = await session.call_tool('list_plugins', {})
                    assert revoked.isError and 'HTTP 401' in revoked.content[0].text
        asyncio.run(check())


@contextmanager
def real_api(tmp_path, *, plugins_dir=None):
    import os
    import socket
    import subprocess
    import time
    import httpx
    from test_core_graph import manifest
    from test_core_auth import bootstrap
    plugins = plugins_dir if plugins_dir is not None else tmp_path / 'plugins'
    if plugins_dir is None:
        manifest(plugins)
    state = tmp_path / 'state'
    state.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # The product binds no execution provider on a fresh install, so the launched
    # server registers the same review fixture the in-process suites use.
    launcher = tmp_path / 'serve.py'
    launcher.write_text(
        'from backend.app import create_app\n'
        'import uvicorn\n'
        'from backend.providers import provider_provenance\n'
        '\n'
        'class FixtureExecution:\n'
        "    def execute(self, job, emit):\n"
        "        return {'status': 'succeeded'}\n"
        "    def rollback(self, job, emit):\n"
        "        return {'status': 'rolled_back'}\n"
        '\n'
        f'app = create_app(state_dir={str(state)!r}, plugins_dir={str(plugins)!r})\n'
        "app.state.registry.register('execution', 'recording', FixtureExecution(), {'status': 'configured'})\n"
        "app.state.provider_loaded_sources[('execution', 'recording')] = provider_provenance(app, 'execution', 'recording')\n"
        "app.state.provider_bindings['execution'] = 'recording'\n"
        f'uvicorn.run(app, host="127.0.0.1", port={port}, log_level="error")\n')
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    log = (tmp_path / 'uvicorn.log').open('w+')
    process = subprocess.Popen([sys.executable, str(launcher)], env=env, stdout=log, stderr=log)
    base_url = f'http://127.0.0.1:{port}'
    try:
        with httpx.Client(base_url=base_url, trust_env=False, timeout=2) as human:
            for _ in range(100):
                try:
                    if human.get('/healthz').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if process.poll() is not None:
                    log.seek(0)
                    pytest.fail(log.read())
                time.sleep(.05)
            else:
                pytest.fail('isolated Uvicorn failed to start')
            credentials = bootstrap(state)
            response = human.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
            assert response.status_code == 200
            human.headers['X-CSRF-Token'] = response.json()['csrf_token']
            yield base_url, human
    finally:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)
        log.close()
