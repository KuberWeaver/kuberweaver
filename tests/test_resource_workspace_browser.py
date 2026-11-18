"""Exercise resource workspace publication with real HTTP, SQLite and Chromium."""
import shutil

import pytest

from test_mcp_server import ROOT, real_api


def test_workspace_composes_and_publishes_two_resources(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api', reason='Optional browser acceptance')
    with real_api(tmp_path, plugins_dir=ROOT / 'plugins') as (base_url, human), playwright.sync_playwright() as browser_api:
        executable = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
        browser = browser_api.chromium.launch(executable_path=executable, headless=True)
        try:
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.add_cookies([{'name': 'kw_session', 'value': human.cookies['kw_session'],
                                  'url': base_url, 'httpOnly': True, 'sameSite': 'Strict'}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base_url)
            page.get_by_role('heading', name='Blueprint workspace').wait_for()
            page.get_by_label('Blueprint name').fill('Web pair smoke test')
            page.get_by_role('button', name='Add resource', exact=True).click()
            page.get_by_role('button', name='Container', exact=True).click()
            page.get_by_label('Deployment adapter').select_option('nginx')
            page.get_by_role('button', name='Add resource', exact=True).click()
            page.get_by_role('button', name='Container', exact=True).click()
            page.get_by_label('Deployment adapter').select_option('apache')
            page.get_by_text('Import configuration values (JSON)', exact=True).click()
            page.get_by_label('Configuration JSON').fill('{"port": 18091}')
            page.get_by_role('button', name='Apply values').click()
            page.get_by_role('checkbox', name='NGINX', exact=True).check()
            page.get_by_role('button', name='Validate blueprint', exact=True).click()
            page.get_by_text('Policies allow this blueprint.', exact=True).wait_for()
            page.get_by_role('button', name='Publish to Service Catalog', exact=True).click()
            page.get_by_role('status').get_by_text('Published to Service Catalog: Web pair smoke test · v1.').wait_for()
            page.get_by_role('button', name='Open Service Catalog', exact=True).click()
            page.get_by_role('button', name='Request Web pair smoke test', exact=True).wait_for()
            blueprints = human.get('/api/blueprints').json()
            assert len(blueprints) == 1
            graph = blueprints[0]['graph']
            assert [node['config']['port'] for node in graph['nodes']] == [18090, 18091]
            assert [(edge['source'], edge['target']) for edge in graph['edges']] == [
                (graph['nodes'][0]['id'], graph['nodes'][1]['id'])]
            assert human.get('/api/jobs').json() == []
            page.get_by_role('button', name='Request Web pair smoke test', exact=True).click()
            page.get_by_role('status').get_by_text('pending approval', exact=False).wait_for()
            assert [job['status'] for job in human.get('/api/jobs').json()] == ['pending_approval']
            assert not errors, errors
            page.get_by_role('button', name='Canvas', exact=True).click()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.get_by_role('button', name='NGINX — Container', exact=True).click()
            assert page.get_by_label('Deployment adapter').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        finally:
            browser.close()
