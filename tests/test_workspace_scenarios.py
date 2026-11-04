"""Workspace interaction and multi-user tests. Never connects to production Firebase."""
import base64
import copy
import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.action_chains import ActionChains

import test_upgrade as baseline

ARTIFACTS = Path(tempfile.mkdtemp(prefix="noah-workspace-"))
DATABASE = {}
LOCK = threading.Lock()
MOCK = r"""
window.audit.shared = () => {
 cloudUser={uid:'test-team'};setCloudUnlocked(true);
 const read=async ref=>{const r=await fetch('/api/'+ref.code);const p=await r.json();return {exists:()=>!!p,data:()=>p}};
 firebaseApi={db:{},doc:(db,col,code)=>({code}),collection:()=>({}),serverTimestamp:()=>0,
  getDoc:read,
  getDocs:async()=>{const docs=await (await fetch('/api-list')).json();return {forEach:fn=>Object.entries(docs).forEach(([id,value])=>fn({id,data:()=>value}))}},
  onSnapshot:(ref,cb,error)=>{let previous='',closed=false;const poll=async()=>{try{const s=await read(ref);const next=JSON.stringify(s.data());if(!closed&&next!==previous){previous=next;cb(s)}}catch(e){if(!closed)error(e)}};poll();const timer=setInterval(poll,70);return()=>{closed=true;clearInterval(timer)}},
  runTransaction:async(db,fn)=>{for(let attempt=0;attempt<4;attempt++){let revision=0,pending;const result=await fn({get:async ref=>{const s=await read(ref);revision=s.data()?.revision||0;return s},set:(ref,value)=>pending={ref,value}});if(!pending)return result;const response=await fetch('/api/'+pending.ref.code,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected:revision,value:pending.value})});if(!response.ok)throw Object.assign(new Error('test offline'),{code:'unavailable'});if(!(await response.json()).accepted)continue;return result}throw Object.assign(new Error('retry'),{code:'aborted'})}
 };
 updateCloudSyncButton();refreshWorkspaceShell();
};
window.audit.inspectDraft=()=>JSON.parse(JSON.stringify(editingItems));
window.audit.inspectRows=()=>JSON.parse(JSON.stringify(editingRows));
window.audit.localBase=()=>JSON.parse(JSON.stringify(localSharedBase));
window.audit.freeze=setFreezeColumns;
window.audit.offlineWrites=()=>{const original=firebaseApi.runTransaction;firebaseApi.runTransaction=async()=>{throw Object.assign(new Error('offline simulation'),{code:'unavailable'})};window.audit.restoreWrites=()=>{firebaseApi.runTransaction=original;window.dispatchEvent(new Event('online'))}};
"""


def html(preserve=False):
    result = baseline.source_html()
    result = result.replace("      if (document.readyState === 'loading')", MOCK + "\n      if (document.readyState === 'loading')")
    if preserve:
        start = result.index("<script>localStorage.clear();")
        end = result.index("</script>", start) + len("</script>")
        result = result[:start] + result[end:]
    return result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api-list' or path.startswith('/api/'):
            with LOCK:
                value = copy.deepcopy(DATABASE if path == '/api-list' else DATABASE.get(path[5:]))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html;charset=utf-8')
            self.end_headers()
            self.wfile.write(html(path == '/tab').encode())

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        code = self.path[5:]
        with LOCK:
            current = DATABASE.get(code, {})
            accepted = current.get('revision', 0) == payload['expected']
            if accepted:
                DATABASE[code] = payload['value']
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'accepted':accepted}).encode())

    def log_message(self, *_args):
        pass


def browser():
    options = Options()
    for arg in ['--headless=new', '--window-size=1680,1050', '--no-sandbox', '--no-proxy-server']:
        options.add_argument(arg)
    options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
    return webdriver.Chrome(options=options)


def run():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    a, b = browser(), browser()
    passed = []
    errors = []

    def js(script, driver=a, *args):
        return driver.execute_script(script, *args)

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        passed.append(name)

    def wait(script, driver=a, seconds=7):
        until = time.time() + seconds
        while time.time() < until:
            if js(script, driver):
                return True
            time.sleep(.08)
        return False

    def reset():
        js('audit.set(arguments[0]);window.confirm=()=>true;window.alert=m=>window.lastAlert=m', a, copy.deepcopy(baseline.FIXTURE))

    try:
        a.get(url)
        b.get(url)
        reset()
        js("document.getElementById('hideToolbarButton').click()")
        check('Tools opens without resizing the workspace', js("return !document.getElementById('toolsDrawer').hidden && document.querySelector('.app-topbar').getBoundingClientRect().height<=70"))
        for tab in ['project', 'templates', 'cloud', 'files', 'view']:
            js("document.querySelector('[data-tool-tab='+arguments[0]+']').click()", a, tab)
            check('Drawer tab ' + tab, js("return !document.querySelector('[data-tool-panel='+arguments[0]+']').hidden", a, tab))
        js("document.getElementById('closeDrawerButton').click();audit.openProjectModal()")
        js("document.getElementById('projectOwnerTitleInput').value='Producer';document.querySelector('[data-project-section=presentation]').click();document.getElementById('actionByLabelInput').value='Team';document.getElementById('applyProjectButton').click()")
        check('Project tabs retain all field edits', js("return audit.get().projectOwnerTitle==='Producer' && audit.get().actionByLabel==='Team'"))
        reset()
        js("audit.openItemsModal('r1');document.getElementById('itemRemarksField').value='My updated note';document.getElementById('itemRemarksField').dispatchEvent(new Event('input',{bubbles:true}));document.querySelectorAll('[data-select-item]')[1].click();document.querySelectorAll('[data-select-item]')[0].click()")
        check('Switching selected items retains the draft', js("return document.getElementById('itemRemarksField').value==='My updated note'"))
        js("document.getElementById('itemEditorSearch').value='Budget';document.getElementById('itemEditorSearch').dispatchEvent(new Event('input'));document.getElementById('itemEditorSearch').value='';document.getElementById('itemEditorSearch').dispatchEvent(new Event('input'))")
        check('Inspector search includes nested items', js("return document.getElementById('itemEditorCount').textContent.includes('3 of 3')"))
        js("document.querySelector('#itemDetails [data-action=addSubitem]').click()")
        field = a.find_elements(By.CSS_SELECTOR, '#itemDetails [data-field=subText]')[-1]
        field.send_keys('New child')
        field.send_keys('\ue007')
        check('Enter creates the next sub-item', js("return document.querySelectorAll('#itemDetails [data-field=subText]').length===4 && document.activeElement.dataset.field==='subText'"))
        js("document.getElementById('undoItemsButton').click()")
        check('Editor Undo reverses the new child', js("return document.querySelectorAll('#itemDetails [data-field=subText]').length===3"))
        js("window.confirm=()=>false;document.querySelector('#itemsEditorBody [data-action=deleteItem]').click()")
        check('Declining item delete retains it', js('return audit.inspectDraft().length===3'))
        js("window.confirm=()=>true;document.getElementById('applyItemsButton').click()")
        check('Save applies inspector changes', js("return audit.get().rows[0].checklist[0].remarks==='My updated note'"))
        reset()
        js("audit.openRowsModal();document.querySelector('[data-select-row]').click();document.getElementById('duplicateSelectedRowsButton').click()")
        check('Batch duplication creates independent item IDs', js("const rows=audit.inspectRows();return rows.length===3 && rows[0].id!==rows[1].id && rows[0].checklist[0].id!==rows[1].checklist[0].id"))
        js("document.getElementById('undoRowsButton').click();document.querySelector('[data-select-row]').click();window.confirm=()=>false;document.getElementById('deleteSelectedRowsButton').click()")
        check('Declining batch deletion retains rows', js("return document.querySelectorAll('#rowsEditorBody tr').length===2"))
        js("window.confirm=()=>true;document.getElementById('deleteSelectedRowsButton').click();document.getElementById('undoRowsButton').click();document.getElementById('applyRowsButton').click()")
        check('Rows Undo restores deleted rows', js('return audit.get().rows.length===2'))
        reset()
        a.save_screenshot(str(ARTIFACTS / 'workspace-desktop.png'))
        js("document.getElementById('hideToolbarButton').click()")
        a.save_screenshot(str(ARTIFACTS / 'tools-desktop.png'))
        js("document.getElementById('closeDrawerButton').click();audit.openProjectModal()")
        a.save_screenshot(str(ARTIFACTS / 'project-desktop.png'))
        js("document.querySelector('[data-project-section=presentation]').click()")
        a.save_screenshot(str(ARTIFACTS / 'presentation-desktop.png'))
        js("document.getElementById('cancelProjectButton').click();audit.openItemsModal('r1')")
        a.save_screenshot(str(ARTIFACTS / 'items-desktop.png'))
        js("document.getElementById('cancelItemsButton').click();audit.openRowsModal()")
        a.save_screenshot(str(ARTIFACTS / 'rows-desktop.png'))
        a.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {'width':390, 'height':844, 'deviceScaleFactor':1, 'mobile':True})
        time.sleep(.1)
        check('Mobile rows fit without horizontal page scroll', js('return document.documentElement.scrollWidth<=innerWidth+1'))
        a.save_screenshot(str(ARTIFACTS / 'rows-mobile.png'))
        js("document.getElementById('cancelRowsButton').click()")
        a.save_screenshot(str(ARTIFACTS / 'workspace-mobile.png'))
        check('Mobile freeze leaves room for other columns', js("return document.body.classList.contains('freeze-cols-1')"))
        a.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})
        time.sleep(.1)
        check('Desktop freeze preference survives mobile sizing', js("return document.body.classList.contains('freeze-cols-4')"))
        cells = a.find_elements(By.CSS_SELECTOR,'.timeline-edit-cell[data-row-id=r1]')
        ActionChains(a).move_to_element(cells[0]).click_and_hold().pause(.08).move_to_element(cells[1]).pause(.1).release().perform()
        check('Native timeline drag moves the scheduled dates', js("return audit.get().rows[0].start==='2026-06-08'"))
        js('audit.undoProject()')
        handle = a.find_element(By.CSS_SELECTOR,'.timeline-edit-cell[data-row-id=r1] .left-handle')
        target = a.find_elements(By.CSS_SELECTOR,'.timeline-edit-cell[data-row-id=r1]')[1]
        ActionChains(a).move_to_element(handle).click_and_hold().pause(.08).move_to_element(target).pause(.1).release().perform()
        check('Native timeline resize changes only the start boundary', js("return audit.get().rows[0].start==='2026-06-08' && audit.get().rows[0].end==='2026-08-31'"))
        dense = copy.deepcopy(baseline.FIXTURE)
        dense['rows'] = [dict(copy.deepcopy(dense['rows'][0]), id='dense'+str(i), category='Group'+str(i)) for i in range(12)]
        dense['rows'][0]['checklist'] = [{'id':'many'+str(i),'text':'Action '+str(i),'subitems':[{'id':'child'+str(j),'text':'Child '+str(j)} for j in range(20)] if i==0 else []} for i in range(20)]
        js('audit.set(arguments[0]);audit.freeze(0)', a, dense)
        js("const w=document.getElementById('programmeWrap');w.scrollTop=180;window.beforeFreezeTop=document.querySelector('.left-head').getBoundingClientRect().top;audit.freeze(6)")
        check('Horizontal freeze does not freeze vertical headers', js("return Math.abs(document.querySelector('.left-head').getBoundingClientRect().top-beforeFreezeTop)<1 && getComputedStyle(document.querySelector('.left-head')).top==='auto'"))
        js("document.getElementById('programmeWrap').scrollTop=0;document.querySelector('.row-expand-toggle').click();document.querySelector('.subcheck-toggle-button').click();window.openHeight=document.querySelector('.checklist-list').getBoundingClientRect().height;document.querySelector('.subcheck-toggle-button').click()")
        check('Auto-height follows independently collapsed sublists', js("return document.querySelector('.checklist-list').getBoundingClientRect().height<openHeight"))
        js("document.querySelector('.row-expand-toggle').click();const list=document.querySelector('.checklist-list');list.scrollTop=list.scrollHeight;window.savedInnerScroll=list.scrollTop;list.querySelectorAll('.checklist-toggle')[19].click()")
        time.sleep(.12)
        check('Checking a bottom item preserves its scrollbar', js("return Math.abs(document.querySelector('.checklist-list').scrollTop-savedInnerScroll)<2"))
        js("audit.openItemsModal('dense0');const pane=document.getElementById('itemDetails');pane.scrollTop=pane.scrollHeight;window.inspectorTop=pane.scrollTop;pane.querySelectorAll('[data-field=subDone]')[19].click()")
        check('Inspector sub-item ticks preserve scroll and keyboard focus', js("return Math.abs(document.getElementById('itemDetails').scrollTop-inspectorTop)<2 && document.activeElement.dataset.subIndex==='19'"))
        js("document.getElementById('cancelItemsButton').click()")
        js("audit.setWorkspaceColumnWidth('category',260);audit.render();audit.openOutputPreview()")
        time.sleep(.15)
        a.switch_to.frame(a.find_element(By.ID,'outputFrame'))
        check('PDF uses preset widths rather than dragged screen widths', js("return document.querySelector('.category-col').getBoundingClientRect().width!==260"))
        check('PDF preview checkboxes are read-only', js("const box=document.querySelector('.checklist-toggle');const before=box.checked;box.click();return box.checked===before"))
        a.switch_to.default_content()
        js("document.querySelector('[data-close-modal=outputModal]').click()")

        # Local coordination uses two tabs sharing storage, not a Firebase mock.
        reset()
        first = a.current_window_handle
        a.switch_to.new_window('tab')
        a.get(url + '/tab')
        second = a.current_window_handle
        js('window.confirm=()=>true;window.alert=m=>window.lastAlert=m;audit.openItemsModal("r1")')
        a.switch_to.window(first)
        js("audit.edit(d=>d.rows[1].action='Other tab row')")
        a.switch_to.window(second)
        time.sleep(.15)
        check('Other-tab update waits for the local editor', js("return audit.get().rows[1].action==='Recruit helpers'"))
        js("document.getElementById('itemRemarksField').value='Local inspector note';document.getElementById('itemRemarksField').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('applyItemsButton').click()")
        check('Independent local tab edits both survive', wait("return audit.get().rows[1].action==='Other tab row' && audit.get().rows[0].checklist[0].remarks==='Local inspector note'"))
        a.switch_to.window(first)
        check('Merged local version reaches the first tab', wait("return audit.get().rows[0].checklist[0].remarks==='Local inspector note'"))
        js('audit.openItemsModal("r1")')
        a.switch_to.window(second)
        js("audit.openItemsModal('r1');document.getElementById('itemRemarksField').value='Tab two conflict';document.getElementById('itemRemarksField').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('applyItemsButton').click()")
        a.switch_to.window(first)
        js("document.getElementById('itemRemarksField').value='Tab one conflict';document.getElementById('itemRemarksField').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('applyItemsButton').click()")
        check('Same field in local tabs requires a choice', wait("return document.getElementById('mergeModal').classList.contains('show') && document.getElementById('mergeRemoteHeading').textContent==='Other tab'"))
        js("document.querySelectorAll('[data-merge-path]').forEach(s=>s.value='remote');document.getElementById('mergeApplyButton').click()")
        check('Local conflict decision is saved without Cloud', wait("return audit.get().rows[0].checklist[0].remarks==='Tab two conflict' && audit.state().active===''"))
        a.switch_to.window(second)
        a.close()
        a.switch_to.window(first)

        # Two separate browser profiles simulate independent colleagues.
        reset()
        js('audit.set(arguments[0]);window.confirm=()=>true;window.alert=m=>window.lastAlert=m', b, copy.deepcopy(baseline.FIXTURE))
        normalized = js('return audit.content()')
        with LOCK:
            DATABASE['AUD-A'] = {'data':normalized, 'projectCode':'AUD-A', 'revision':1}
        js('audit.shared()')
        js('audit.shared()', b)
        check('Unlock alone leaves the local project unchanged', js("return audit.state().active==='' && audit.get().rows[0].action==='Venue planning'"))
        cloud_copy = dict(copy.deepcopy(normalized), projectCode='AUD-B', projectName='Downloaded cloud project')
        with LOCK:
            DATABASE['AUD-B'] = {'data':cloud_copy,'projectCode':'AUD-B','revision':1}
        js("document.getElementById('projectListButton').click();document.querySelector('[data-project-tab=cloud]').click()")
        check('Unlocked project list shows Cloud codes', wait("return document.getElementById('projectListBody').textContent.includes('AUD-B')"))
        js("document.querySelector('.project-list-load-button[data-code=\"AUD-B\"]').click()")
        check('Loading from Cloud does not enable Auto Sync', wait("return audit.get().projectCode==='AUD-B' && audit.state().active===''") )
        js("audit.edit(d=>d.projectName='Local downloaded draft')")
        time.sleep(1)
        with LOCK:
            unchanged = DATABASE['AUD-B']['data']['projectName']=='Downloaded cloud project'
        check('Downloaded project stays local until explicit Sync', unchanged)
        js('audit.set(arguments[0]);audit.shared()', a, copy.deepcopy(baseline.FIXTURE))
        js('audit.prepareCloudSync()')
        js('audit.prepareCloudSync()', b)
        check('Explicit Sync starts both colleagues', wait("return audit.state().active==='AUD-A'") and wait("return audit.state().active==='AUD-A'", b))
        js("audit.openItemsModal('r1')")
        js("audit.edit(d=>d.rows[1].action='Colleague B row')", b)
        check('Cloud update waits behind the inspector draft', wait('return audit.state().pending'))
        js("document.getElementById('itemRemarksField').value='Colleague A note';document.getElementById('itemRemarksField').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('applyItemsButton').click()")
        check('Two colleagues independent fields merge', wait("return audit.get().rows[1].action==='Colleague B row' && audit.get().rows[0].checklist[0].remarks==='Colleague A note'"))
        check('Merged changes are delivered to colleague B', wait("return audit.get().rows[0].checklist[0].remarks==='Colleague A note'", b))
        js('audit.undoProject()')
        check('Undo retains a colleagues independently merged changes', js("return audit.get().rows[1].action==='Colleague B row' && audit.get().rows[0].checklist[0].remarks!=='Colleague A note'"))
        js('audit.undoProject(true)')
        check('Redo retains the colleagues changes too', js("return audit.get().rows[1].action==='Colleague B row' && audit.get().rows[0].checklist[0].remarks==='Colleague A note'"))
        js('audit.openItemsModal("r1")')
        js("audit.edit(d=>d.rows[0].checklist[0].remarks='Cloud B note')", b)
        check('Concurrent update is buffered', wait('return audit.state().pending'))
        js("document.getElementById('itemRemarksField').value='Cloud A note';document.getElementById('itemRemarksField').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('applyItemsButton').click()")
        check('Two colleagues same field conflict is explicit', wait("return audit.state().conflict && document.getElementById('mergeModal').classList.contains('show')"))
        js("document.querySelectorAll('[data-merge-path]').forEach(s=>s.value='remote');document.getElementById('mergeApplyButton').click()")
        check('Cloud conflict choice keeps the chosen value', wait("return audit.get().rows[0].checklist[0].remarks==='Cloud B note' && !audit.state().conflict"))
        js("audit.offlineWrites();audit.edit(d=>d.rows[1].action='Edit while connection is down')")
        check('Failed upload leaves a local saved copy', wait("return document.getElementById('cloudStatus').textContent.includes('retry')") and js("return JSON.parse(localStorage.getItem('noah_master_programme_local_project_v1:AUD-A')).rows[1].action==='Edit while connection is down'"))
        js('audit.restoreWrites()')
        check('Connection recovery resumes sync', wait("return audit.get().rows[1].action==='Edit while connection is down'", b, seconds=10))
        js("audit.stopCloudSyncSilently();audit.edit(d=>d.rows[1].action='Local only after stop')")
        time.sleep(1.2)
        check('Stop Sync prevents later local edits uploading', js("return audit.get().rows[1].action!=='Local only after stop'", b))
        errors = [entry['message'] for d in [a,b] for entry in d.get_log('browser') if entry['level']=='SEVERE' and 'favicon' not in entry['message']]
        check('No browser exceptions in workspace scenarios', not errors)
        print(json.dumps({'passed':passed, 'artifacts':str(ARTIFACTS)}, ensure_ascii=False, indent=2))
    except Exception:
        a.save_screenshot(str(ARTIFACTS / 'failure.png'))
        print(json.dumps({'passed':passed, 'errors':errors+a.get_log('browser')+b.get_log('browser'), 'artifacts':str(ARTIFACTS)}, ensure_ascii=False, indent=2))
        raise
    finally:
        a.quit()
        b.quit()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    run()
