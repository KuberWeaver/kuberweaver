"""Real HTTP, stdio MCP and browser flow with a named fixture driver.

The fixture replaces only the remote hypervisor/secret-source boundary. It is
not evidence of authenticated ESXi discovery or VM provisioning.
"""
import asyncio
import json
import shutil
import sys

import httpx
import pytest

from test_mcp_server import ROOT, real_api


FIXTURE_EXTENSION = '''
from contextlib import contextmanager
from kuberweaver.secrets import SecretValue

class FixtureSecrets:
    @contextmanager
    def resolve(self, reference):
        if reference != 'fixture-ref':
            raise ValueError('Unknown fixture reference')
        value = SecretValue(b'{"username":"fixture-user","password":"fixture-password-only"}')
        try:
            yield value
        finally:
            value.close()
    def describe(self):
        return {'approved_names': ['fixture-ref'], 'can_store': False}

class FixtureHypervisor:
    def __init__(self, path):
        self.path = path
    def discover(self, connection, credentials):
        assert b'fixture-password-only' in credentials
        with self.path.open('a') as stream:
            stream.write('discovery\\n')
        return {'server': {'name': 'Protocol fixture, not ESXi', 'version': 'fixture-v1',
            'api_type': 'Fixture', 'write_api_supported': None},
            'datastores': [{'id': 'fixture-ds', 'name': 'Fixture storage',
                'capacity_bytes': 1073741824, 'free_bytes': 536870912, 'accessible': True}],
            'networks': [{'id': 'fixture-network', 'name': 'Fixture network'}],
            'resource_pools': [{'id': 'fixture-pool', 'name': 'Fixture resource pool'}],
            'virtual_machines': []}

def register(ctx):
    ctx.register('hypervisor', 'fixture-hypervisor',
        FixtureHypervisor(ctx.plugin_data_dir() / 'calls.txt'), {'label': 'Protocol fixture'})
    ctx.register('secrets', 'fixture-vault', FixtureSecrets(), {'reference_only': True})
'''


def test_real_connection_http_mcp_and_browser(tmp_path):
    pytest.importorskip('playwright.sync_api', reason='Optional acceptance: install requirements-acceptance.txt')
    with real_api(tmp_path) as (base_url, human):
        package = tmp_path / 'state/extensions/protocol-fixture'
        package.mkdir()
        (package / 'plugin.yaml').write_text(json.dumps({'id': 'protocol-fixture',
            'version': '1.0.0', 'entrypoint': 'module.py',
            'capabilities': ['providers:hypervisor', 'providers:secrets', 'data:read-write']}))
        (package / 'module.py').write_text(FIXTURE_EXTENSION)
        assert human.post('/api/extensions/protocol-fixture/enable', json={'trusted': True}).status_code == 200
        registry = human.get('/api/connection-drivers').json()
        assert any(row['id'] == 'fixture-hypervisor' and row['available'] for row in registry['drivers'])
        definition = {'label': 'Real protocol fixture', 'driver': 'fixture-hypervisor',
            'endpoint': 'https://192.0.2.10', 'secret_ref': 'fixture-ref',
            'secrets_provider': 'fixture-vault', 'tls_sha256': None, 'enabled': True}
        response = human.post('/api/connections', json=definition)
        assert response.status_code == 201, response.text
        connection = response.json()
        identity = connection['id']
        assert human.get('/api/connections/' + identity).json() == connection
        calls = tmp_path / 'state/plugin-data/protocol-fixture/calls.txt'
        assert not calls.exists(), 'Metadata reads must not discover remote inventory'
        observation = human.post('/api/connections/' + identity + '/discover').json()
        assert observation['status'] == 'reachable', observation
        assert human.get('/api/connections/' + identity).json()['observation'] == observation
        assert calls.read_text().splitlines() == ['discovery']
        for route in ('/api/connections', '/api/connection-drivers', '/api/audit'):
            assert 'fixture-password-only' not in human.get(route).text

        issued = human.post('/api/agent-tokens', json={'label': 'V2 protocol acceptance',
            'role': 'operator', 'scopes': ['read:infrastructure'], 'expires_in_seconds': 300}).json()
        token_path = tmp_path / 'v2-agent.token'
        token_path.write_text(issued['token'])
        token_path.chmod(0o600)

        async def machine_flow():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / 'scripts/mcp_server.py'), '--base-url', base_url,
                      '--token-file', str(token_path)])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.call_tool('list_connections', {})
                    assert not listing.isError
                    assert listing.structuredContent['data'][0]['id'] == identity
                    observed = await session.call_tool('discover_connection', {'identity': identity})
                    assert not observed.isError
                    assert observed.structuredContent['data']['status'] == 'reachable'
                    denied = await session.call_tool('create_connection', {'connection': definition})
                    assert denied.isError and 'HTTP 403' in denied.content[0].text
                    assert human.delete('/api/agent-tokens/' + issued['metadata']['id']).status_code == 200
                    revoked = await session.call_tool('get_connection', {'identity': identity})
                    assert revoked.isError and 'HTTP 401' in revoked.content[0].text
        asyncio.run(machine_flow())

        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            executable = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            try:
                context.add_cookies([{'name': 'kw_session', 'value': human.cookies['kw_session'],
                    'url': base_url, 'httpOnly': True, 'sameSite': 'Strict'}])
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base_url)
                page.get_by_role('button', name='Connections', exact=True).click()
                page.get_by_role('heading', name='Hypervisor connections').wait_for()
                page.get_by_text('Protocol fixture, not ESXi', exact=True).wait_for()
                page.get_by_role('button', name='Discover inventory', exact=True).click()
                page.get_by_role('status').filter(has_text='Observation saved').wait_for()
                assert page.get_by_role('table', name='Datastores').get_by_text('Fixture storage').count() == 1
                assert 'fixture-password-only' not in page.locator('body').inner_text()
                assert not errors, errors
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.get_by_role('heading', name='Hypervisor connections').is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            finally:
                context.close()
                browser.close()
        assert calls.read_text().splitlines() == ['discovery', 'discovery', 'discovery']
        persisted = httpx.get(base_url + '/healthz', trust_env=False)
        assert persisted.status_code == 200
