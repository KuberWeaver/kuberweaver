"""No live libvirt mutation: subprocess is an explicit fake boundary."""
import hashlib
import importlib
import json
import pytest


@pytest.fixture(autouse=True)
def isolated_ssh_config(tmp_path, monkeypatch):
    module = importlib.import_module('backend.kuberweaver.tofu_execution')
    known = tmp_path / 'known_hosts'
    known.write_text('host ssh-ed25519 test-key\n')
    config = tmp_path / 'ssh_config'
    config.write_text(f'Host infra-hypervisor\n  StrictHostKeyChecking yes\n  UserKnownHostsFile {known}\n')
    monkeypatch.setattr(module, 'SSH_CONFIG', config)


def graph():
    return {'name': 'vm-test', 'environment': 'dev', 'nodes': [
        {'id': 'vm1', 'plugin_id': 'libvirt-vm', 'config': {'cpu': 1, 'memory_mb': 128, 'disk_gb': 1}}
    ], 'edges': [], 'failure_mode': 'none'}


def test_reviewed_graph_rejects_arbitrary_connection_and_updates(tmp_path):
    module = importlib.import_module('backend.kuberweaver.tofu_execution')
    executor = module.TofuExecutor(tmp_path)
    job = {'id': 'job-1', 'graph': graph()}
    prepared = executor.review(job)
    assert prepared['name'].startswith('kw-')
    assert prepared['config']['cpu'] == 1
    job['graph']['nodes'][0]['config']['host'] = 'evil'
    with pytest.raises(ValueError):
        executor.review(job)
    job['graph'] = graph()
    job['graph']['environment'] = 'production'
    with pytest.raises(ValueError):
        executor.review(job)
    job['id'] = '../outside'
    with pytest.raises(ValueError):
        executor.review(job)


class FakeBoundary:
    """Only process boundary is fake; artifacts/locks/digests use real files."""
    def __init__(self):
        self.calls = []
        self.state = {'values': {'root_module': {'resources': []}}}
        self.drift = False
        self.ssh_extra = ''

    def __call__(self, argv, **kwargs):
        from pathlib import Path
        from types import SimpleNamespace
        self.calls.append(argv)
        cwd = Path(kwargs.get('cwd', '.'))
        if (cwd / 'terraform.tfvars.json').is_file():
            self.name = json.loads((cwd / 'terraform.tfvars.json').read_text())['name']
        out, code = '', 0
        if argv[0] == 'ssh':
            if '-G' in argv:
                module = importlib.import_module('backend.kuberweaver.tofu_execution')
                known = module.SSH_CONFIG.parent / 'known_hosts'
                out = (f'stricthostkeychecking true\nuserknownhostsfile {known}\n'
                       'globalknownhostsfile none\nupdatehostkeys false\n'
                       'verifyhostkeydns false\ncontrolmaster false\n')
                out += getattr(self, 'ssh_extra', '')
                return SimpleNamespace(returncode=0, stdout=out, stderr='')
            command = argv[-1]
            if 'list --all --name' in command:
                out = 'nested-smoke\n'
            elif 'vol-list' in command:
                out = ' Name Path\n----------------\n base /base\n'
            elif 'dumpxml' in command or 'vol-dumpxml' in command:
                if 'vol-dumpxml' in command:
                    out = f'<volume><name>{self.name}.qcow2</name><key>/owned/disk</key></volume>'
                else:
                    out = f'<domain><name>{self.name}</name><uuid>owned-uuid</uuid></domain>'
        elif 'init' in argv:
            (cwd / '.terraform.lock.hcl').write_text('verified fake provider lock')
        elif 'plan' in argv:
            target = next(x.split('=', 1)[1] for x in argv if x.startswith('-out='))
            (cwd / target).write_bytes(b'exact saved binary plan')
            code = 2 if '-refresh-only' not in argv or self.drift else 0
        elif 'show' in argv:
            if argv[-1].endswith('.plan'):
                action = 'delete' if argv[-1] == 'destroy.plan' else ('no-op' if argv[-1] == 'refresh.plan' else 'create')
                config = json.loads((cwd / 'terraform.tfvars.json').read_text())
                out = json.dumps({'resource_changes': [
                    {'address': addr, 'mode': 'managed', 'change': {'actions': [action], 'after': {'name': config['name'] + ('.qcow2' if 'volume' in addr else '')}}}
                    for addr in ('libvirt_domain.vm', 'libvirt_volume.disk')]})
            else:
                out = json.dumps(self.state)
        elif 'apply' in argv:
            config = json.loads((cwd / 'terraform.tfvars.json').read_text())
            resources = [] if argv[-1] == 'destroy.plan' else [
                {'address': 'libvirt_domain.vm', 'values': {'name': config['name'], 'uuid': 'owned-uuid'}},
                {'address': 'libvirt_volume.disk', 'values': {'name': config['name'] + '.qcow2', 'id': '/owned/disk'}}]
            self.state = {'values': {'root_module': {'resources': resources}}}
            (cwd / 'terraform.tfstate').write_text(json.dumps(self.state))
        return SimpleNamespace(returncode=code, stdout=out, stderr='SECRET must never escape')


def test_failed_apply_never_automatically_destroys(tmp_path):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    original = ex.runner
    def fail_apply(argv, **kwargs):
        if 'apply' in argv:
            from types import SimpleNamespace
            boundary.calls.append(argv)
            return SimpleNamespace(returncode=1, stdout='SECRET', stderr='SECRET')
        return original(argv, **kwargs)
    ex.runner = fail_apply
    assert ex.execute(j, lambda *a: None)['status'] == 'failed'
    assert ex.rollback(j, lambda *a: None)['status'] == 'rollback_failed'
    assert not (ex.state_dir / j['id'] / 'owned.json').exists()
    assert not any('-destroy' in a for a in boundary.calls)


def test_unexpected_plan_actions_rejected(tmp_path):
    ex, _ = executor(tmp_path)
    for action in (['update'], ['delete', 'create'], ['no-op']):
        with pytest.raises(ValueError):
            ex._plan_actions({'resource_changes': [{'address': addr, 'change': {'actions': action}}
                             for addr in ('libvirt_domain.vm', 'libvirt_volume.disk')]}, 'create')


def test_external_xml_change_blocks_removal(tmp_path):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    assert ex.execute(j, lambda *a: None)['status'] == 'succeeded'
    boundary.name = 'not-the-owned-vm'
    original = ex._ssh
    ex._ssh = lambda command: original(command).replace('owned-uuid', 'external-uuid')
    with pytest.raises(ValueError):
        ex.prepare_removal(j)
    assert not any('-destroy' in a for a in boundary.calls)


def test_planned_catalog_and_disabled_extension_discovery(tmp_path):
    from backend.kuberweaver.catalog import Catalog
    from backend.kuberweaver.extensions import ExtensionRegistry
    module = importlib.import_module('backend.kuberweaver.tofu_execution')
    assert Catalog(module.ROOT / 'plugins').discover()['libvirt-vm']['capability'] == 'planned'
    registry = ExtensionRegistry()
    registry.load_directory(module.ROOT / 'examples/extensions', enabled=set())
    assert not any(x['name'] == 'opentofu' for x in registry.list())


def executor(tmp_path):
    module = importlib.import_module('backend.kuberweaver.tofu_execution')
    binary = tmp_path / 'tofu'
    binary.write_bytes(b'trusted executable')
    boundary = FakeBoundary()
    return module.TofuExecutor(tmp_path / 'state', binary=binary, runner=boundary), boundary


def job():
    g = graph()
    module = importlib.import_module('backend.kuberweaver.tofu_execution')
    return {'id': 'job-1', 'graph': g, 'approved_by': 'admin', 'approved_at': 'reviewed',
            'approved_graph_hash': module.digest(g),
            'approved_execution_provider': {'name': 'opentofu'}}


def test_saved_plan_requires_explicit_digest_approval_and_no_replanning(tmp_path):
    ex, boundary = executor(tmp_path)
    j = job()
    evidence = ex.prepare_plan(j)
    assert evidence['actions'] == {'libvirt_domain.vm': ['create'], 'libvirt_volume.disk': ['create']}
    assert not any('apply' in call for call in boundary.calls)
    assert ex.execute(j, lambda *args: None)['status'] == 'failed'
    j['approved_tofu_plan'] = evidence['plan_digest']
    before = len([a for a in boundary.calls if 'plan' in a])
    assert ex.execute(j, lambda *args: None)['status'] == 'succeeded'
    assert len([a for a in boundary.calls if 'plan' in a]) == before
    assert next(a for a in boundary.calls if 'apply' in a)[-1] == 'create.plan'
    folder = ex.state_dir / j['id']
    assert (folder / 'create.plan').stat().st_mode & 0o777 == 0o600
    assert folder.stat().st_mode & 0o777 == 0o700


def test_separate_approved_remove_only_owned_without_drift(tmp_path):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    assert ex.execute(j, lambda *a: None)['status'] == 'succeeded'
    assert ex.rollback(j, lambda *a: None)['status'] == 'rollback_failed'
    removal = ex.prepare_removal(j)
    j['approved_tofu_removal'] = removal['plan_digest']
    boundary.drift = True
    assert ex.rollback(j, lambda *a: None)['status'] == 'rollback_failed'
    assert not any('apply' in a and a[-1] == 'destroy.plan' for a in boundary.calls)
    boundary.drift = False
    assert ex.rollback(j, lambda *a: None)['status'] == 'rolled_back'
    assert boundary.state['values']['root_module']['resources'] == []


@pytest.mark.parametrize('artifact', ['create.plan', 'main.tf', 'terraform.tfvars.json', '.terraform.lock.hcl'])
def test_tampered_approved_artifacts_never_apply(tmp_path, artifact):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    (ex.state_dir / j['id'] / artifact).write_text('changed')
    result = ex.execute(j, lambda *a: None)
    assert result['status'] == 'failed'
    assert 'SECRET' not in json.dumps(result)
    assert not any('apply' in a for a in boundary.calls)


def test_preexisting_state_never_applied_or_removed(tmp_path):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    (ex.state_dir / j['id'] / 'terraform.tfstate').write_text('{}')
    assert ex.execute(j, lambda *a: None)['status'] == 'failed'
    assert ex.rollback(j, lambda *a: None)['status'] == 'rollback_failed'
    assert not any('apply' in a for a in boundary.calls)


def test_preflight_is_read_only_and_registry_helper_is_safe(tmp_path):
    ex, boundary = executor(tmp_path)
    report = ex.preflight(remote=True)
    assert report['provider'] == 'opentofu'
    assert report['capability'] == 'planned'
    assert report['mutations_performed'] is False
    assert ex.read()['binary_present'] is True
    assert not any('apply' in a or 'plan' in a for a in boundary.calls)


def test_state_lock_excludes_second_instance(tmp_path):
    ex, _ = executor(tmp_path)
    with ex._lock():
        assert ex.execute(job(), lambda *a: None)['status'] == 'failed'


@pytest.mark.parametrize('view,source', [
    ('live', '<source file="/owned/./disk"/>'),
    ('persistent', '<source file="/owned/&#100;isk"/>'),
    ('live', '<source pool="infra-lab" volume="{name}.qcow2"/>'),
])
def test_foreign_disk_attachment_blocks_removal(tmp_path, view, source):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    assert ex.execute(j, lambda *a: None)['status'] == 'succeeded'
    original = ex._ssh
    def inspect(command):
        if 'list --name' in command:
            return 'nested-smoke\n'
        if 'dumpxml' in command and 'nested-smoke' in command:
            selected = 'persistent' if '--inactive' in command else 'live'
            disk = source.format(name=boundary.name) if selected == view else ''
            return '<domain><devices><disk>' + disk + '</disk></devices></domain>'
        return original(command)
    ex._ssh = inspect
    with pytest.raises(ValueError, match='referenced'):
        ex.prepare_removal(j)
    assert not any('-destroy' in a for a in boundary.calls)


def test_unreadable_foreign_live_xml_blocks_removal(tmp_path):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    assert ex.execute(j, lambda *a: None)['status'] == 'succeeded'
    original = ex._ssh
    def inspect(command):
        if 'list --name' in command:
            return 'nested-smoke\n'
        if 'dumpxml' in command and 'nested-smoke' in command and '--inactive' not in command:
            raise RuntimeError('cannot read live XML')
        return original(command)
    ex._ssh = inspect
    with pytest.raises(RuntimeError):
        ex.prepare_removal(j)
    assert not any('-destroy' in a for a in boundary.calls)


@pytest.mark.parametrize('artifact,content', [
    ('cli.tfrc', 'provider_installation { dev_overrides { "dmacvicar/libvirt" = "/evil" } }'),
    ('override.tf', 'terraform {}'),
    ('extra.auto.tfvars.json', '{}'),
    ('terraform.rc', 'unexpected executable config'),
])
def test_unapproved_cli_configuration_blocks_every_command(tmp_path, artifact, content):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    folder = ex.state_dir / j['id']
    (folder / artifact).write_text(content)
    before = len(boundary.calls)
    with pytest.raises(ValueError):
        ex._command(folder, 'show', '-json')
    assert len(boundary.calls) == before
    assert ex.execute(j, lambda *a: None)['status'] == 'failed'
    assert not any('apply' in a for a in boundary.calls)


def test_changed_known_hosts_blocks_approved_apply(tmp_path):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    (tmp_path / 'known_hosts').write_text('host ssh-ed25519 substituted-key\n')
    assert ex.execute(j, lambda *a: None)['status'] == 'failed'
    assert not any('apply' in a for a in boundary.calls)


@pytest.mark.parametrize('option', [
    'proxycommand ssh -S /existing/socket jump -W %h:%p\n',
    'controlpath /existing/socket\n',
    'proxyjump jump\n',
    'knownhostscommand /external/trust\n',
    'stricthostkeychecking false\n',
    'userknownhostsfile /dev/null\n',
    'verifyhostkeydns true\n',
])
def test_uninspectable_host_trust_blocks_planning(tmp_path, option):
    ex, boundary = executor(tmp_path)
    boundary.ssh_extra = option
    with pytest.raises(ValueError):
        ex.prepare_plan(job())
    assert not any('init' in a or 'plan' in a for a in boundary.calls)


def test_effective_host_trust_change_blocks_approved_apply(tmp_path):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    boundary.ssh_extra = 'hostname different-host\n'
    assert ex.execute(j, lambda *a: None)['status'] == 'failed'
    assert not any('apply' in a for a in boundary.calls)


@pytest.mark.parametrize('directive', ['Include=/external/config', 'Match exec "true"'])
def test_dynamic_ssh_config_rejected_before_ssh_g(tmp_path, directive):
    ex, boundary = executor(tmp_path)
    module = importlib.import_module('backend.kuberweaver.tofu_execution')
    module.SSH_CONFIG.write_text(directive + '\n')
    with pytest.raises(ValueError):
        ex.prepare_plan(job())
    assert boundary.calls == []


@pytest.mark.parametrize('operation', ['create', 'remove'])
@pytest.mark.parametrize('interruption', ['timeout', 'keyboard'])
def test_partial_apply_interruption_survives_restart_without_retry(tmp_path, operation, interruption):
    import subprocess
    module = importlib.import_module('backend.kuberweaver.tofu_execution')
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    if operation == 'remove':
        assert ex.execute(j, lambda *a: None)['status'] == 'succeeded'
        j['approved_tofu_removal'] = ex.prepare_removal(j)['plan_digest']
    original = ex.runner
    def interrupt(argv, **kwargs):
        result = original(argv, **kwargs)
        if 'apply' in argv:
            if interruption == 'keyboard':
                raise KeyboardInterrupt()
            raise subprocess.TimeoutExpired(argv, 180)
        return result
    ex.runner = interrupt
    action = ex.execute if operation == 'create' else ex.rollback
    if interruption == 'keyboard':
        with pytest.raises(KeyboardInterrupt):
            action(j, lambda *a: None)
    else:
        assert action(j, lambda *a: None)['requires_manual_review'] is True
    folder = ex.state_dir / j['id']
    marker = 'attempt.json' if operation == 'create' else 'remove-attempt.json'
    assert (folder / marker).exists()
    if operation == 'create':
        assert not (folder / 'owned.json').exists()
    attempts = len([a for a in boundary.calls if 'apply' in a])
    restarted = module.TofuExecutor(ex.state_dir, binary=ex.binary, runner=boundary)
    retry = restarted.execute if operation == 'create' else restarted.rollback
    assert retry(j, lambda *a: None)['requires_manual_review'] is True
    assert len([a for a in boundary.calls if 'apply' in a]) == attempts


@pytest.mark.parametrize('artifact', ['cli.tfrc', 'known_hosts'])
def test_postapproval_trust_drift_blocks_removal(tmp_path, artifact):
    ex, boundary = executor(tmp_path)
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    assert ex.execute(j, lambda *a: None)['status'] == 'succeeded'
    j['approved_tofu_removal'] = ex.prepare_removal(j)['plan_digest']
    path = tmp_path / 'known_hosts' if artifact == 'known_hosts' else ex.state_dir / j['id'] / artifact
    path.write_text('changed')
    assert ex.rollback(j, lambda *a: None)['status'] == 'rollback_failed'
    assert not any('apply' in a and a[-1] == 'destroy.plan' for a in boundary.calls)


@pytest.mark.parametrize('artifact', ['binary', 'provider'])
def test_executable_distribution_pins_preserved(tmp_path, artifact):
    ex, boundary = executor(tmp_path)
    original = ex.runner
    plugin = ex.state_dir / 'job-1' / '.terraform/providers/registry/provider'
    def install(argv, **kwargs):
        result = original(argv, **kwargs)
        if 'init' in argv:
            plugin.parent.mkdir(parents=True)
            plugin.write_bytes(b'reviewed provider')
        return result
    ex.runner = install
    j = job()
    j['approved_tofu_plan'] = ex.prepare_plan(j)['plan_digest']
    (ex.binary if artifact == 'binary' else plugin).write_bytes(b'substituted executable')
    assert ex.execute(j, lambda *a: None)['status'] == 'failed'
    assert not any('apply' in a for a in boundary.calls)

