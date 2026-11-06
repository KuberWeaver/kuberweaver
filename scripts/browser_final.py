"""Post-review browser verification, approval cost, role controls and mobile pages."""
from pathlib import Path
import copy,json
import httpx
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:8088'

def main():
    creds=dict(x.split(':',1) for x in (ROOT/'data/bootstrap-credentials.txt').read_text().splitlines() if ':' in x)
    report:dict={'page_errors':[],'checks':[],'mobile':{}}
    with httpx.Client(base_url=URL,trust_env=False) as c:
        login=c.post('/api/auth/login',json={'username':'admin','password':creds['admin']});login.raise_for_status();c.headers['X-CSRF-Token']=login.json()['csrf_token']
        old=json.loads((ROOT/'data/evidence/browser-acceptance.json').read_text())
        graph=copy.deepcopy(c.get('/api/jobs/'+old['job_id']).json()['graph']);graph['nodes'][0]['config']['version']='1.2.0'
        req=c.post('/api/jobs',json={'graph':graph});req.raise_for_status();jid=req.json()['id']
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
            def sign_in(name):
                page.goto(URL);page.get_by_label('Identifiant').fill(name);page.get_by_label('Mot de passe',exact=True).fill(creds[name]);page.get_by_role('button',name='Se connecter').click();page.get_by_label('Nom du blueprint').wait_for()
            sign_in('admin')
            page.screenshot(path=str(ROOT/'data/evidence/portal-desktop-final.png'),full_page=True)
            page.get_by_role('button',name='Déploiements',exact=True).click();page.locator('.job-row').first.click()
            page.get_by_role('button',name='Approuver',exact=True).wait_for()
            cost=page.get_by_role('region',name='Estimation interne du déploiement')
            cost.wait_for();assert 'facturation mesurée' in cost.inner_text()
            cost_box=cost.bounding_box();approve_box=page.get_by_role('button',name='Approuver',exact=True).bounding_box()
            assert cost_box is not None and approve_box is not None
            assert cost_box['y']<approve_box['y']
            report['checks'].append('server_cost_breakdown_visible_before_approval')
            page.screenshot(path=str(ROOT/'data/evidence/portal-approval-cost.png'),full_page=True)
            with page.expect_response(lambda r:r.url.endswith('/reject')) as rejected:
                page.get_by_role('button',name='Refuser',exact=True).click()
            assert rejected.value.ok,rejected.value.text()
            page.get_by_role('button',name='Approuver',exact=True).wait_for(state='hidden')
            assert c.get('/api/jobs/'+jid).json()['status']=='rejected'
            report['checks'].append('browser_rejection_read_back')
            page.set_viewport_size({'width':390,'height':844})
            for section in ['Canvas','Catalogue','Déploiements','Infrastructure','Gouvernance','FinOps','Diagnostic','Audit','Intégrations']:
                page.get_by_role('button',name='Ouvrir le menu').click();page.get_by_role('button',name=section,exact=section!='Intégrations').first.click();page.wait_for_timeout(300)
                dims=page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})');report['mobile'][section]=dims
                assert dims['scroll']<=dims['width'],(section,dims)
            page.screenshot(path=str(ROOT/'data/evidence/portal-mobile-final.png'),full_page=True)
            report['checks'].append('all_nine_mobile_sections_no_page_overflow')
            page.set_viewport_size({'width':1440,'height':1000})
            page.get_by_role('button',name='Se déconnecter').click();sign_in('viewer')
            assert page.get_by_role('button',name='Soumettre à approbation').is_disabled()
            page.get_by_role('button',name='Intégrations',exact=False).first.click();page.get_by_label('Provider diagnostics',exact=True).wait_for();assert page.get_by_label('Provider diagnostics',exact=True).is_disabled()
            report['checks'].append('viewer_canvas_and_provider_controls_read_only')
            browser.close()
    assert not report['page_errors'],report['page_errors']
    (ROOT/'data/evidence/browser-final.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'ok':True,'checks':len(report['checks']),'mobile_sections':len(report['mobile']),'page_errors':report['page_errors']}))

if __name__=='__main__':main()
