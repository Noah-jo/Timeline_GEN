"""Viewport and modal layout smoke checks using only isolated fixture data."""
import copy
import json
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

import test_upgrade as baseline


def run():
    server = ThreadingHTTPServer(('127.0.0.1', 0), baseline.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    options = Options()
    for arg in ['--headless=new', '--window-size=1920,1080', '--no-sandbox', '--no-proxy-server']:
        options.add_argument(arg)
    options.set_capability('goog:loggingPrefs', {'browser':'ALL'})
    driver = webdriver.Chrome(options=options)
    artifacts = Path(tempfile.mkdtemp(prefix='noah-layout-'))
    passed = []
    fixture = copy.deepcopy(baseline.FIXTURE)
    fixture['rows'][0]['checklist'][0]['text'] = 'Venue sourcing / \u5834\u5730\u641c\u96c6'

    def js(script, *args):
        return driver.execute_script(script, *args)

    try:
        driver.get('http://127.0.0.1:' + str(server.server_port))
        js('window.confirm=()=>true;window.alert=m=>window.lastAlert=m')
        for width,height in [(320,800),(390,844),(820,1180),(1366,768),(1920,1080)]:
            driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride',{'width':width,'height':height,'deviceScaleFactor':1,'mobile':width<680})
            views = {
                'workspace':'',
                'project':'audit.openProjectModal()',
                'presentation':"audit.openProjectModal();document.querySelector('[data-project-section=presentation]').click()",
                'items':"audit.openItemsModal('r1')",
                'rows':'audit.openRowsModal()',
                'row-details':"audit.openQuickRowModal('r1')",
                'new-project':"document.getElementById('addProjectButton').click()",
                'library':"audit.openItemsModal('r1');document.getElementById('openLibraryButton').click()",
                'pdf':'audit.openOutputPreview()',
                'projects':"document.getElementById('projectListButton').click()",
                'json':"document.getElementById('editDataButton').click()",
            }
            for name,opening in views.items():
                js('audit.set(arguments[0])',fixture)
                if opening:
                    js(opening)
                time.sleep(.07)
                metrics = js("const m=[...document.querySelectorAll('.modal.show')].at(-1)?.querySelector('.modal-card');return {overflow:document.documentElement.scrollWidth-innerWidth,box:m?{left:m.getBoundingClientRect().left,right:m.getBoundingClientRect().right,top:m.getBoundingClientRect().top,bottom:m.getBoundingClientRect().bottom}:null}")
                box = metrics['box']
                if metrics['overflow']>1 or (box and (box['left']<0 or box['right']>width+1 or box['top']<0 or box['bottom']>height+1)):
                    raise AssertionError((width,name,metrics))
                driver.save_screenshot(str(artifacts / (str(width)+'-'+name+'.png')))
                passed.append(str(width)+' '+name)
        errors = [entry for entry in driver.get_log('browser') if entry['level']=='SEVERE' and 'favicon' not in entry['message']]
        assert not errors,errors
        print(json.dumps({'layout_checks':len(passed),'passed':passed,'artifacts':str(artifacts)},indent=2))
    except Exception:
        driver.save_screenshot(str(artifacts / 'failure.png'))
        print(json.dumps({'passed':passed,'errors':driver.get_log('browser'),'artifacts':str(artifacts)},indent=2))
        raise
    finally:
        driver.quit()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    run()
