"""Authenticated acceptance and independent guest readback, no secret output."""
from pathlib import Path
import copy,json,subprocess,time
import httpx
ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8088'
TERMINAL={'succeeded','failed','rolled_back','rollback_failed','rejected'}

def main():
    creds=dict(x.split(':',1) for x in (ROOT/'data/bootstrap-credentials.txt').read_text().splitlines() if ':' in x)
    report: dict = {'checks':[]}
    def login(user):
        c=httpx.Client(base_url=URL,timeout=20,trust_env=False)
        r=c.post('/api/auth/login',json={'username':user,'password':creds[user]});r.raise_for_status()
        c.headers['X-CSRF-Token']=r.json()['csrf_token']
        return c
    admin,operator,viewer=[login(x) for x in ['admin','operator','viewer']]
    baseline=json.loads((ROOT/'data/evidence/browser-acceptance.json').read_text())
    original=admin.get('/api/jobs/'+baseline['job_id']).json()
    graph=original['graph']
    assert viewer.post('/api/jobs',json={'graph':graph}).status_code==403
    assert operator.patch('/api/settings',json={'pricing':{'cpu_monthly':1}}).status_code==403
    no_csrf=admin.headers.pop('X-CSRF-Token')
    assert admin.post('/api/jobs',json={'graph':graph}).status_code==403
    admin.headers['X-CSRF-Token']=no_csrf
    report['checks']+=['viewer_write_denied','operator_policy_write_denied','csrf_enforced']
    for name,edit in [('public-ssh',{'public_cidr':'0.0.0.0/0','port':22}),('unknown-host',{'targets':['production']})]:
        bad=copy.deepcopy(graph);bad['nodes'][0]['config'].update(edit)
        response=admin.post('/api/plans/validate',json=bad)
        assert response.status_code==422 or (response.is_success and not response.json()['allowed']),response.text
        report['checks'].append(name+'_blocked')
    bad=copy.deepcopy(graph);bad['nodes'][0]['plugin_id']='pfsense'
    response=admin.post('/api/plans/validate',json=bad)
    assert response.status_code==422 or not response.json()['allowed']
    report['checks'].append('planned_target_blocked')
    inventory=admin.get('/api/infrastructure').json()
    assert not inventory['errors'] and len(inventory['nodes'])==12 and len(inventory['links'])==20
    report['inventory']={'nodes':len(inventory['nodes']),'links':len(inventory['links'])}
    extensions=admin.get('/api/extensions').json()
    assert not any(x['name']=='lab-notes' for x in extensions['providers'])
    denied=admin.post('/api/extensions/lab-notes/enable',json={'trusted':False})
    assert denied.status_code==422
    enabled=admin.post('/api/extensions/lab-notes/enable',json={'trusted':True});enabled.raise_for_status()
    assert any(x['name']=='lab-notes' for x in admin.get('/api/extensions').json()['providers'])
    report['checks']+=['installed_plugin_disabled','explicit_trust_required','extension_enabled_without_core_edit']
    def wait(jid):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            r=admin.get('/api/jobs/'+jid);r.raise_for_status();job=r.json()
            if job['status'] in TERMINAL:return job
            time.sleep(1)
        raise AssertionError('Deployment timed out')
    def submit(g):
        r=admin.post('/api/jobs',json={'graph':g});r.raise_for_status();job=r.json()
        assert job['status']=='pending_approval'
        assert operator.post('/api/jobs/'+job['id']+'/approve').status_code==403
        r=admin.post('/api/jobs/'+job['id']+'/approve');r.raise_for_status()
        return wait(job['id'])
    def guest_versions():
        data={}
        for host,ip in [('app','192.168.122.40'),('branch-client','192.168.122.60')]:
            r=subprocess.run(['ssh','-F','/home/ubu/Desktop/infra-platform-lab/ssh_config','infra-'+host,'curl','--max-time','5','-fsS',f'http://{ip}:18091/version.json'],capture_output=True,text=True,check=True,timeout=20)
            data[host]=json.loads(r.stdout)
        return data
    update=copy.deepcopy(graph);update['nodes'][0]['config']['version']='2.0.0';update['failure_mode']='rolling'
    failed=submit(update)
    assert failed['status']=='failed',failed
    assert failed['result']['rollback']['status']=='rolled_back',failed
    restored=guest_versions();assert all(x['version']=='1.0.0' for x in restored.values())
    report['automatic_rollback']={'job_id':failed['id'],'status':failed['status'],'result':failed['result'],'independent_readback':restored}
    report['checks'].append('rolling_failure_restores_canary_and_second_target')
    update['failure_mode']='none'
    success=submit(update);assert success['status']=='succeeded',success
    assert all(x['version']=='2.0.0' for x in guest_versions().values())
    response=admin.post('/api/jobs/'+success['id']+'/rollback');response.raise_for_status()
    rolled=wait(success['id']);assert rolled['status']=='rolled_back',rolled
    restored=guest_versions();assert all(x['version']=='1.0.0' for x in restored.values())
    report['manual_rollback']={'job_id':rolled['id'],'status':rolled['status'],'result':rolled['result'],'independent_readback':restored}
    report['checks']+=['successful_v2_two_guests','manual_rollback_verified_on_two_guests']
    disabled=admin.post('/api/extensions/lab-notes/disable');disabled.raise_for_status()
    assert not any(x['name']=='lab-notes' for x in admin.get('/api/extensions').json()['providers'])
    assert admin.get('/healthz').status_code==200
    report['checks'].append('extension_disabled_without_core_failure')
    commit=original['git_commit']
    actual=subprocess.run(['git','-C',str(ROOT/'data/gitops'),'rev-parse',commit+'^{commit}'],capture_output=True,text=True,check=True).stdout.strip()
    assert actual==commit
    report['git_commit']=actual
    report['checks'].append('git_commit_independently_verified')
    assert admin.get('/api/audit').json()
    report['job_count']=len(admin.get('/api/jobs').json())
    report['checks'].append('audit_and_jobs_read_back')
    for c in [admin,operator,viewer]:c.close()
    (ROOT/'data/evidence/api-live-acceptance.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'ok':True,'checks':len(report['checks']),'job_count':report['job_count'],'automatic_rollback_job':failed['id'],'manual_rollback_job':rolled['id']}))

if __name__=='__main__':main()
