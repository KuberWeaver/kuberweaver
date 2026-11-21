from click.testing import CliRunner
from pathlib import Path

from kw.main import cli
from kuberweaver.catalog import Catalog
from kuberweaver.policy import Admission


def test_cloudflare_is_not_a_standalone_cli_command():
    runner = CliRunner()
    assert runner.invoke(cli, ['--help']).exit_code == 0
    result = runner.invoke(cli, ['cloudflare', '--help'])
    assert result.exit_code == 2
    assert "No such command 'cloudflare'" in result.output


def test_catalog_admits_cloudflare_as_ansible_service():
    class DefaultStore:
        def get(self, *_):
            return None

    catalog = Catalog(Path(__file__).resolve().parents[1] / 'plugins')
    brick = catalog.discover()['cloudflare-tunnel']
    assert (brick['kind'], brick['engine']) == ('workload', 'ansible')
    graph = {
        'name': 'Cloudflare service', 'environment': 'dev', 'failure_mode': 'none',
        'strategy': {'canary': 1, 'batch_size': 1}, 'edges': [],
        'nodes': [{'id': 'tunnel', 'plugin_id': 'cloudflare-tunnel', 'config': {
            'target_uuid': '6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b',
            'ssh_key_id': 'b' * 32, 'tunnel_token_ref': 'cloudflare/example',
        }}],
    }
    result = Admission(catalog, DefaultStore()).validate(graph)
    assert result['allowed'], result['violations']