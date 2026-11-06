"""Real browser acceptance against the local portal and real GNS3 executor."""
from pathlib import Path
import json,time
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8088'

def main():
    credentials=dict(line.split(':',1) for line in (ROOT/'data/bootstrap-credentials.txt').read_text().splitlines() if ':' in line)
    report: dict = {'checks':[],'page_errors':[],'failed_requests':[]}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
        page.on('requestfailed',lambda r:report['failed_requests'].append({'url':r.url,'failure':r.failure}))
        page.goto(URL)
        page.get_by_label('Identifiant').fill('admin')
        page.get_by_label('Mot de passe',exact=True).fill(credentials['admin'])
        page.get_by_role('button',name='Se connecter').click()
        page.get_by_label('Nom du blueprint').wait_for()
        assert page.locator('.react-flow__node').count()==0
        report['checks'].append('initial_canvas_empty')
        page.get_by_role('button',name='Ajouter NGINX').click()
        page.get_by_label('Nom du blueprint').fill('portal-acceptance')
        page.get_by_label('Nom de la ressource').fill('Portail NGINX')
        page.get_by_label('port',exact=True).fill('18091')
        with page.expect_response(lambda r:'/api/plans/validate' in r.url) as validation:
            page.get_by_role('button',name='Valider le blueprint').click()
        vr=validation.value.json()
        assert validation.value.ok and vr['allowed'], vr
        report['validation']={'allowed':vr['allowed'],'cost':vr['cost']}
        report['checks'].append('schema_form_and_policy_validation')
        with page.expect_response(lambda r:r.url.endswith('/api/blueprints') and r.request.method=='POST') as saved:
            page.get_by_role('button',name='Enregistrer',exact=True).click()
        assert saved.value.ok, saved.value.text()
        report['blueprint_id']=saved.value.json()['id']
        report['checks'].append('blueprint_saved_and_read_back')
        with page.expect_response(lambda r:r.url.endswith('/api/jobs') and r.request.method=='POST') as submitted:
            page.get_by_role('button',name='Soumettre à approbation').click()
        assert submitted.value.ok, submitted.value.text()
        job=submitted.value.json(); jid=job['id']
        assert job['status']=='pending_approval'
        report['job_id']=jid
        page.get_by_role('button',name='Approuver',exact=True).wait_for(timeout=10000)
        with page.expect_response(lambda r:r.url.endswith('/approve')) as approved:
            page.get_by_role('button',name='Approuver',exact=True).click()
        assert approved.value.ok, approved.value.text()
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            job=page.request.get(URL+'/api/jobs/'+jid).json()
            if job['status'] in ['succeeded','failed','rolled_back','rollback_failed']:
                break
            page.wait_for_timeout(2000)
        assert job['status']=='succeeded',job
        report['checks'].append('browser_approval_to_real_two_guest_deployment')
        report['job']={k:job.get(k) for k in ['id','status','git_commit','result']}
        page.wait_for_timeout(2500)
        page.screenshot(path=str(ROOT/'data/evidence/portal-deployment.png'),full_page=True)
        for section in ['Catalogue','Infrastructure','Gouvernance','FinOps','Diagnostic','Audit','Intégrations']:
            page.get_by_role('button',name=section,exact=section!='Intégrations').first.click()
            page.wait_for_timeout(500)
            assert page.locator('main').count()
            report['checks'].append('page_'+section)
            if section=='Infrastructure':
                page.screenshot(path=str(ROOT/'data/evidence/portal-infrastructure.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_timeout(300)
        overflow=page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
        assert overflow['scroll']<=overflow['width'],overflow
        page.screenshot(path=str(ROOT/'data/evidence/portal-mobile.png'),full_page=True)
        report['checks'].append('mobile_no_page_overflow')
        browser.close()
    assert not report['page_errors'],report['page_errors']
    (ROOT/'data/evidence/browser-acceptance.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'ok':True,'job_id':jid,'checks':len(report['checks']),'page_errors':report['page_errors']}))

if __name__=='__main__':main()
