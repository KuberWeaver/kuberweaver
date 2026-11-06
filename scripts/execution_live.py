"""Run explicit live acceptance against ONLY infra-app/infra-branch-client.

Writes real Ansible + independent curl evidence. Run from repository .venv.
"""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from kuberweaver.execution import Executor, TARGETS, SSH_CONFIG


def emit(phase, level, message):
    print(f'[{phase}:{level}] {message}', flush=True)


def curl_both(port):
    records = []
    for target, details in TARGETS.items():
        # curl originates on each guest, bound service is on its management address.
        command = ['ssh', '-F', str(SSH_CONFIG), details['alias'],
                   f"curl --noproxy '*' --fail --silent --show-error http://{details['ip']}:{port}/version.json"]
        output = subprocess.run(command, capture_output=True, text=True, timeout=20)
        if output.returncode:
            raise RuntimeError(f'Independent curl failed on {target}: {output.stderr}')
        records.append({'target': target, 'command': command, 'body': output.stdout, 'payload': json.loads(output.stdout)})
    return records


def no_service(node_id, port):
    records = []
    for target, details in TARGETS.items():
        command = ['ssh', '-F', str(SSH_CONFIG), details['alias'],
                   f"sudo -n docker container inspect kw-{node_id} >/dev/null 2>&1; c=$?; sudo -n test -e /opt/kuberweaver/services/{node_id}; p=$?; printf '%s %s\\n' \"$c\" \"$p\""]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0 and result.stdout.strip() == '1 1', (target, result.stdout, result.stderr)
        records.append({'target': target, 'container_absent': True, 'config_absent': True})
    return records


def graph(node='web-live', plugin='nginx', port=18090, version='1.0.0', failure='none'):
    return {'name': 'live-acceptance', 'environment': 'dev', 'nodes': [
        {'id': node, 'plugin_id': plugin, 'label': plugin, 'position': {'x': 80, 'y': 80},
         'config': {'targets': ['app', 'branch-client'], 'port': port, 'version': version,
                    'cpu': 1, 'memory_mb': 256, 'disk_gb': 2, 'public_cidr': '127.0.0.1/32'}}
    ], 'edges': [], 'strategy': {'canary': 1, 'batch_size': 1}, 'failure_mode': failure}


def main():
    evidence_path = ROOT / 'data/evidence/execution-live.json'
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    executor = Executor(ROOT / 'data/execution')
    run = str(time.time_ns())
    evidence = {'source': 'real Ansible + real SSH curl, not test runner', 'steps': [], 'run': run}

    def save():
        evidence_path.write_text(json.dumps(evidence, indent=2))

    def deploy(label, config, expected='succeeded'):
        job = {'id': label + '-' + run, 'graph': config}
        result = executor.execute(job, emit)
        evidence['steps'].append({'name': label, 'job': job, 'result': result})
        save()
        assert result['status'] == expected, result
        return job, result

    _, failed = deploy('absence-canary', graph(node='absence-check', port=18109, failure='canary'), 'failed')
    assert failed['rollback']['status'] == 'rolled_back'
    evidence['steps'][-1]['independent_absence'] = no_service('absence-check', 18109)
    baseline, _ = deploy('baseline-v1', graph())
    evidence['steps'][-1]['independent_curl'] = curl_both(18090)
    _, failed = deploy('rolling-v2-failure', graph(version='2.0.0', failure='rolling'), 'failed')
    assert failed['rollback']['status'] == 'rolled_back'
    evidence['steps'][-1]['independent_curl'] = curl_both(18090)
    assert all(x['payload']['version'] == '1.0.0' for x in evidence['steps'][-1]['independent_curl'])
    update, _ = deploy('update-v2', graph(version='2.0.0'))
    evidence['steps'][-1]['independent_curl'] = curl_both(18090)
    assert all(x['payload']['version'] == '2.0.0' for x in evidence['steps'][-1]['independent_curl'])
    result = executor.rollback(update, emit)
    evidence['steps'].append({'name': 'manual-rollback-v1', 'job_id': update['id'], 'result': result,
                              'independent_curl': curl_both(18090)})
    save()
    assert result['status'] == 'rolled_back', result
    assert all(x['payload']['version'] == '1.0.0' for x in evidence['steps'][-1]['independent_curl'])
    apache, _ = deploy('apache-live', graph(node='apache-check', plugin='apache', port=18091))
    evidence['steps'][-1]['independent_curl'] = curl_both(18091)
    result = executor.rollback(apache, emit)
    evidence['steps'].append({'name': 'apache-restore-absence', 'result': result,
                              'independent_absence': no_service('apache-check', 18091)})
    assert result['status'] == 'rolled_back', result
    evidence['baseline_job_id'] = baseline['id']
    evidence['final_baseline'] = curl_both(18090)
    old_workload = subprocess.run(['ssh', '-F', str(SSH_CONFIG), 'infra-app',
        "curl --noproxy '*' --fail --silent --output /dev/null --write-out '%{http_code}' http://192.168.122.40:8080"],
        capture_output=True, text=True, timeout=20)
    evidence['existing_8080'] = {'http_status': old_workload.stdout, 'returncode': old_workload.returncode}
    assert old_workload.returncode == 0 and old_workload.stdout == '200'
    evidence['status'] = 'passed'
    save()
    print(json.dumps({'status': 'passed', 'evidence': str(evidence_path), 'baseline_job_id': baseline['id']}))


if __name__ == '__main__':
    main()
