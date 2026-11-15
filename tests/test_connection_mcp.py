"""Connection MCP tools remain adapters to authorized HTTP routes."""
import asyncio
import json

import httpx
from kuberweaver.mcp_server import ServerConfig, create_server


def test_connection_metadata_and_explicit_discovery_use_http_only(tmp_path):
    token = tmp_path / 'machine-token'
    token.write_text('unit-private-token')
    token.chmod(0o600)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})

    server = create_server(ServerConfig('http://127.0.0.1:8088', token),
                           transport=httpx.MockTransport(respond))
    identity = 'a' * 32

    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        for name in ('list_connection_drivers', 'list_connections', 'get_connection', 'discover_connection'):
            assert name in tools, name
        assert not any('credential' in name or 'password' in name for name in tools)
        for name, params, method, path in [
            ('list_connection_drivers', {}, 'GET', '/api/connection-drivers'),
            ('list_connections', {}, 'GET', '/api/connections'),
            ('get_connection', {'identity': identity}, 'GET', f'/api/connections/{identity}'),
            ('discover_connection', {'identity': identity}, 'POST', f'/api/connections/{identity}/discover'),
        ]:
            await server.call_tool(name, params)
            assert requests[-1].method == method
            assert requests[-1].url.path == path
            assert requests[-1].headers['authorization'] == 'Bearer unit-private-token'
        assert tools['get_connection'].annotations.readOnlyHint is True
        assert tools['discover_connection'].annotations.destructiveHint is False
        assert tools['discover_connection'].inputSchema['additionalProperties'] is False

    asyncio.run(check())


def test_connection_configuration_tools_are_typed_and_never_accept_inline_passwords(tmp_path):
    import pytest
    from mcp.server.fastmcp.exceptions import ToolError
    token = tmp_path / 'machine-token'
    token.write_text('unit-private-token')
    token.chmod(0o600)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})

    server = create_server(ServerConfig('http://127.0.0.1:8088', token),
                           transport=httpx.MockTransport(respond))
    identity = 'b' * 32
    spec = {'label': 'Lab', 'driver': 'esxi', 'endpoint': 'https://192.0.2.10',
            'secret_ref': 'esxi-lab', 'secrets_provider': 'openbao-local',
            'tls_sha256': None, 'enabled': True}

    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert {'create_connection', 'update_connection'} <= set(tools)
        await server.call_tool('create_connection', {'connection': spec})
        assert requests[-1].method == 'POST'
        assert requests[-1].url.path == '/api/connections'
        assert json.loads(requests[-1].content) == spec
        await server.call_tool('update_connection', {'identity': identity, 'patch': {'revision': 1, 'label': 'Renamed'}})
        assert requests[-1].method == 'PATCH'
        assert requests[-1].url.path == f'/api/connections/{identity}'
        assert json.loads(requests[-1].content) == {'revision': 1, 'label': 'Renamed'}
        before = len(requests)
        for name, values in [
            ('create_connection', {'connection': {**spec, 'password': 'test-only-value'}}),
            ('update_connection', {'identity': identity, 'patch': {'label': 'No revision'}}),
            ('update_connection', {'identity': identity, 'patch': {'revision': 1, 'password': 'test-only-value'}}),
            ('get_connection', {'identity': '../agent-tokens'}),
        ]:
            with pytest.raises(ToolError):
                await server.call_tool(name, values)
        assert len(requests) == before

    asyncio.run(check())
