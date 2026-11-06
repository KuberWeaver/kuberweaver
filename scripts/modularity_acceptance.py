"""Prove provider replacement through real portal + real worker, without core edits."""
from pathlib import Path
import copy,hashlib,json,time
import httpx
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8088'

def main():
    creds=dict(x.split(':',1) for x in (ROOT/'data/bootstrap-credentials.txt').read_text().splitlines() if ':' in x)
    core=list((ROOT/'backend/kuberweaver').glob('*.py'))
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in core}
    report:dict={'checks':[]}
    with httpx.Client(base_url=URL,timeout=20,trust_env=False) as client:
        r=client.post('/api/auth/login',json={'username':'admin','password':creds['admin']});r.raise_for_status();client.headers['X-CSRF-Token']=r.json()['csrf_token']
        assert client.get('/api/providers').json()['bindings']['diagnostics']=='rules'
        r=client.post('/api/extensions/lab-notes/enable',json={'trusted':True});r.raise_for_status()
        with sync_playwright() as p:
            b=p.chromium.launch(headless=True);page=b.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(URL);page.get_by_label('Identifiant').fill('admin');page.get_by_label('Mot de passe',exact=True).fill(creds['admin']);page.get_by_role('button',name='Se connecter').click()
            page.get_by_role('button',name='Intégrations',exact=False).first.click()
            page.get_by_label('Provider diagnostics',exact=True).select_option('lab-notes')
            with page.expect_response(lambda r:r.url.endswith('/api/providers') and r.request.method=='PATCH') as changed:
                page.get_by_role('button',name='Enregistrer les providers').click()
            assert changed.value.ok,changed.value.text()
            page.get_by_text('Liaisons enregistrées et vérifiées depuis le serveur.').wait_for()
            page.screenshot(path=str(ROOT/'data/evidence/portal-provider-selection.png'),full_page=True)
            assert not errors,errors;b.close()
        assert client.get('/api/providers').json()['bindings']['diagnostics']=='lab-notes'
        assert client.get('/api/diagnostics').status_code==200
        report['checks'].append('provider_selected_from_portal_and_real_api_route')
        previous=json.loads((ROOT/'data/evidence/browser-acceptance.json').read_text())
        graph=client.get('/api/jobs/'+previous['job_id']).json()['graph']
        graph=copy.deepcopy(graph);graph['nodes'][0]['config']['version']='1.1.0';graph['failure_mode']='none'
        r=client.post('/api/jobs',json={'graph':graph});r.raise_for_status();jid=r.json()['id']
        r=client.post('/api/jobs/'+jid+'/approve');r.raise_for_status()
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            job=client.get('/api/jobs/'+jid).json()
            if job['status'] in {'failed','rollback_failed'}:raise AssertionError(job)
            diagnostic=client.get('/api/diagnostics').json()
            matches=[f for f in diagnostic.get('findings',[]) if f.get('provenance',{}).get('job_id')==jid]
            if job['status']=='succeeded' and matches:break
            time.sleep(1)
        else:raise AssertionError('Worker completion did not reach selected extension')
        report['checks'].append('real_deployment_job_finished_hook_reaches_diagnostics_api')
        report['job_id']=jid;report['diagnostic']=diagnostic;report['provider_pin']=job['approved_execution_provider']
        assert job['can_rollback']
        r=client.post('/api/jobs/'+jid+'/rollback');r.raise_for_status()
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            job=client.get('/api/jobs/'+jid).json()
            if job['status'] in {'rolled_back','rollback_failed'}:break
            time.sleep(1)
        assert job['status']=='rolled_back',job
        # Wait for the matching completion observer, not merely terminal job persistence.
        for _ in range(20):
            diagnostic=client.get('/api/diagnostics').json()
            if any('rolled_back' in f.get('message','') for f in diagnostic.get('findings',[])):break
            time.sleep(.2)
        r=client.post('/api/extensions/lab-notes/disable');r.raise_for_status()
        assert client.get('/api/diagnostics').status_code==503
        assert client.get('/api/infrastructure').status_code==200
        assert client.get('/api/jobs').status_code==200
        report['checks'].append('disabled_selected_provider_unavailable_without_breaking_core')
        r=client.patch('/api/providers',json={'bindings':{'diagnostics':'rules','secrets':'openbao'}});r.raise_for_status()
        state=client.get('/api/providers').json();assert state['bindings']['diagnostics']=='rules' and state['bindings']['secrets']=='openbao'
        assert client.get('/api/diagnostics').status_code==200
        report['checks'].append('explicit_rebind_restores_route_and_selects_available_secret_source')
        assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in hashes.items())
        report['checks'].append('no_core_source_modifications_for_provider_lifecycle')
        report['final_bindings']=state['bindings']
    (ROOT/'data/evidence/modularity-live.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'ok':True,'checks':len(report['checks']),'job_id':report['job_id'],'final_bindings':report['final_bindings']}))

if __name__=='__main__':main()
