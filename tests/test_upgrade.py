"""Regression tests use an isolated browser and an in-memory Firebase double."""
import copy
import base64
import json
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.action_chains import ActionChains

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "index.html" if (ROOT / "index.html").exists() else ROOT / "MGT_improved.html"
ARTIFACTS = Path(tempfile.mkdtemp(prefix="noah-upgrade-"))
FIXTURE = {
    "projectName": "Event planning review", "projectCode": "AUD-A",
    "projectOwner": "Jo", "lastUpdate": "Last update: 01 JUN 2026 10:00",
    "startDate": "2026-06-01", "endDate": "2026-08-31",
    "holidayAutoUpdate": False, "audienceMode": "internal",
    "rows": [
        {"id": "r1", "category": "Event", "item": "Venue",
         "action": "Venue planning", "actionBy": "Production",
         "start": "2026-06-01", "end": "2026-08-31",
         "checklist": [
             {"id": "i1", "text": "Venue sourcing", "remarks": "First line\nSecond line",
              "subitems": [{"id": "s1", "text": "Location", "done": False},
                           {"id": "s2", "text": "Budget", "done": True}]},
             {"id": "i2", "text": "Rate card", "remarks": "Rate notes"},
             {"id": "i3", "text": "Floor plan", "remarks": ""}]},
        {"id": "r2", "category": "People", "item": "Helpers",
         "action": "Recruit helpers", "actionBy": "HR",
         "start": "2026-06-02", "end": "2026-07-03", "checklist": []}
    ]
}
HOOKS = """
window.audit = {
 get:()=>JSON.parse(JSON.stringify(data)), content:()=>contentData(data),
 set:d=>{stopCloudSyncSilently();cloudUser=null;firebaseApi=null;
   document.querySelectorAll('.modal.show').forEach(m=>m.classList.remove('show'));
   data=normalizeData(d);lastSavedContent='';lastSavedSnapshot=null;
   undoEntries=[];redoEntries=[];rowFilters={search:'',category:'',person:'',status:'all'};render()},
 edit:fn=>{fn(data);touchLastUpdate();saveData()},
 render,saveData,contentData,cloudDataJson,mergeVersions,visibleRows,
 openQuickRowModal,applyQuickRowEditor,openItemsModal,openRowsModal,
 openProjectModal,closeProjectModal,removeLogo,importProjectFromFile,
 importCsv,applyCsvImport,exportCsv,openOutputPreview,refreshOutputPreview,
 createProject,stopCloudSyncSilently,saveCloudProjectNow,subscribeCloudProject,prepareCloudSync,restoreCloudProjectFromLocal,
 setWorkspaceColumnWidth,undoProject,loadProjectFromList,deleteLocalProjectOnly,
 refreshProjectList,listLocalProjects,recordRecovery,processRemotePayload,deleteProjectFromList,
 state:()=>({active:activeCloudProjectCode,revision:cloudRevision,
   pending:!!pendingRemotePayload,conflict:cloudConflictPending,
   undo:undoEntries.length,redo:redoEntries.length}),
 capture:()=>{downloadBlob=(content,name,type)=>{window.auditDownload={content,name,type}}},
 cloud:(code='AUD-A',delay=false)=>{
   cloudUser={uid:'audit'};sessionStorage.setItem(CLOUD_UNLOCK_KEY,'1');
   activeCloudProjectCode=code;cloudRevision=1;cloudBaseJson=cloudDataJson(data,code);
   window.auditRemote=contentData(data);window.auditWrites=[];window.auditDeleted=false;
   firebaseApi={db:{},doc:(db,col,c)=>({code:c}),serverTimestamp:()=>0,
    onSnapshot:(ref,cb)=>{window.auditSnapshot=cb;return ()=>{}},
    getDoc:async ref=>({exists:()=>true,data:()=>({data:window.auditRemote,revision:window.auditRev||1,deleted:window.auditDeleted})}),
    runTransaction:async(db,fn)=>{const result=await fn({
     get:async()=>({exists:()=>true,data:()=>({data:window.auditRemote,revision:window.auditRev||1,deleted:window.auditDeleted})}),
     set:(ref,value)=>{window.auditWrites.push({ref,value});window.auditRemote=value.data;window.auditRev=value.revision;if(value.deleted===false)window.auditDeleted=false;}
    });if(delay)await new Promise(resolve=>window.auditRelease=resolve);return result;}
   };
 },
 remote:d=>window.auditSnapshot({exists:()=>true,data:()=>({data:d,revision:2,updatedByClientId:'other-tab'})})
};
"""


def source_html():
    html = SOURCE.read_text(encoding="utf-8")
    html = re.sub(r'<script type="module">[\s\S]*?</script>', "", html)
    html = html.replace("      if (document.readyState === 'loading')",
                        HOOKS + "\n      if (document.readyState === 'loading')")
    seed = "<script>localStorage.clear();localStorage.setItem('noah_master_programme_display_v5'," + json.dumps(json.dumps(FIXTURE)) + ");</script>"
    return html.replace("<body>", seed + "<body>")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html;charset=utf-8")
        self.end_headers()
        self.wfile.write(source_html().encode("utf-8"))

    def log_message(self, *_args):
        pass


def run():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1680,1050")
    options.add_argument("--no-sandbox")
    options.add_argument("--no-proxy-server")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    driver = webdriver.Chrome(options=options)
    results = []

    def js(script, *args):
        return driver.execute_script(script, *args)

    def check(name, value):
        if not value:
            raise AssertionError(name)
        results.append(name)

    def reset(fixture=None):
        js("audit.set(arguments[0]);window.confirm=()=>true;window.alert=m=>{window.lastAlert=m};window.auditRev=1",
           fixture or copy.deepcopy(FIXTURE))
        time.sleep(0.08)

    try:
        driver.get("http://127.0.0.1:" + str(server.server_port))
        time.sleep(0.4)
        check("startup", js("return !!window.audit && document.querySelectorAll('.programme tbody tr').length===2"))
        check("no duplicate DOM IDs", js("const ids=[...document.querySelectorAll('[id]')].map(x=>x.id);return ids.length===new Set(ids).size"))
        original = js("return audit.get().rows[0].checklist")
        js("audit.openQuickRowModal('r1');audit.applyQuickRowEditor()")
        check("Quick Edit preserves nested checklist and multiline remarks", original == js("return audit.get().rows[0].checklist"))
        reset()
        js("audit.cloud();audit.subscribeCloudProject('AUD-A');document.querySelector('.checklist-toggle').click();const remote=JSON.parse(JSON.stringify(auditRemote));remote.rows[1].action='Remote changed';audit.remote(remote)")
        check("simultaneous independent changes merge", js("return audit.get().rows[0].checklist[0].done && audit.get().rows[1].action==='Remote changed' && !audit.state().conflict"))
        reset()
        js("audit.cloud();audit.subscribeCloudProject('AUD-A');audit.edit(d=>d.rows[0].action='Mine');const remote=JSON.parse(JSON.stringify(auditRemote));remote.rows[0].action='Theirs';audit.remote(remote)")
        check("same field conflict retains local draft", js("return audit.get().rows[0].action==='Mine' && audit.state().conflict && document.getElementById('mergeModal').classList.contains('show')"))
        js("""const s=document.querySelector('[data-merge-path="/rows/r1/action"]');s.value='remote';document.getElementById('mergeApplyButton').click()""")
        check("field conflict choices apply", js("return audit.get().rows[0].action==='Theirs' && !audit.state().conflict"))
        reset()
        js("audit.cloud();audit.subscribeCloudProject('AUD-A');audit.openItemsModal('r1');const remote=JSON.parse(JSON.stringify(auditRemote));remote.rows[1].action='Teammate waiting';audit.remote(remote)")
        check("teammate update waits during editor draft", js("return audit.state().pending && audit.get().rows[1].action==='Recruit helpers'"))
        js("document.getElementById('cancelItemsButton').click()")
        time.sleep(0.1)
        check("waiting update applies after editor closes", js("return audit.get().rows[1].action==='Teammate waiting'"))
        reset()
        js("audit.cloud('AUD-A',true);audit.saveCloudProjectNow('AUD-A')")
        time.sleep(0.08)
        js("audit.stopCloudSyncSilently();auditRelease()")
        time.sleep(0.08)
        check("Stop Sync survives in-flight completion", js("return audit.state().active===''"))
        reset()
        js("audit.cloud();window.auditDeleted=true;audit.restoreCloudProjectFromLocal('AUD-A')")
        time.sleep(0.1)
        check("restored deleted cloud project can sync again", js("return audit.state().active==='AUD-A' && auditWrites[0].value.deleted===false"))
        reset()
        js("audit.cloud();const imported=audit.get();imported.projectCode='AUD-B';imported.projectName='Imported B';audit.importProjectFromFile(new File([JSON.stringify(imported)],'b.json'))")
        time.sleep(0.15)
        check("JSON import stops old sync target", js("return audit.get().projectCode==='AUD-B' && audit.state().active===''"))
        js("audit.saveCloudProjectNow('AUD-A')")
        check("cannot write imported B into A", js("return auditWrites.length===0"))
        reset()
        js("document.getElementById('newProjectCodeInput').value='AUD-A';document.getElementById('newProjectNameInput').value='Overwrite';document.getElementById('newProjectOwnerInput').value='Jo';audit.createProject('blank')")
        check("duplicate local project code does not overwrite", js("return audit.get().projectName!=='Overwrite' && audit.get().rows.length===2"))
        reset()
        check("local project list works while locked", js("return audit.listLocalProjects().some(p=>p.code==='AUD-A')"))
        js("const other={...audit.get(),projectCode:'AUD-OTHER'};localStorage.setItem('noah_master_programme_local_project_v1:AUD-OTHER',JSON.stringify(other));audit.deleteProjectFromList('AUD-OTHER')")
        check("deleting another local project does not reset current project", js("return audit.get().projectCode==='AUD-A' && !localStorage.getItem('noah_master_programme_local_project_v1:AUD-OTHER')"))
        js("audit.openItemsModal('r1');document.getElementById('itemsModal').click()")
        check("backdrop retains editor", js("return document.getElementById('itemsModal').classList.contains('show')"))
        reset()
        js("const d=audit.get();d.logoDataUrl='data:image/svg+xml;base64,PHN2Zy8+';audit.set(d);audit.openProjectModal();audit.removeLogo();document.getElementById('cancelProjectButton').click()")
        check("Cancel discards logo draft", js("return !!audit.get().logoDataUrl"))
        reset()
        before = js("return audit.get().lastUpdate")
        Select(driver.find_element(By.ID, "audienceInput")).select_by_value("actions")
        check("view settings do not change Last Update", before == js("return audit.get().lastUpdate"))
        js("document.querySelector('.checklist-toggle').click()")
        check("checkbox refreshes Last Update header", js("return document.getElementById('lastUpdateText').textContent===audit.get().lastUpdate"))
        js("audit.undoProject()")
        check("Undo restores checkbox", js("return !audit.get().rows[0].checklist[0].done"))
        js("audit.undoProject(true)")
        check("Redo reapplies checkbox", js("return audit.get().rows[0].checklist[0].done"))
        reset()
        js("audit.importCsv(new File(['Wrong,Headers\\nfoo,bar'],'wrong.csv'))")
        time.sleep(0.12)
        check("invalid CSV does not replace rows", js("return audit.get().rows[0].action==='Venue planning' && !!window.lastAlert"))
        js("audit.capture();audit.exportCsv();audit.importCsv(new File([auditDownload.content],'roundtrip.csv'))")
        time.sleep(0.12)
        check("CSV validated preview before mutation", js("return document.getElementById('csvImportModal').classList.contains('show') && audit.get().rows.length===2"))
        js("audit.applyCsvImport(true)")
        check("CSV round trip preserves full checklist", original == js("return audit.get().rows[0].checklist"))
        reset()
        js("audit.openItemsModal('r1');const s=document.querySelector('#itemsEditorBody .move-position');s.value='2';s.dispatchEvent(new Event('change'));document.getElementById('applyItemsButton').click()")
        check("item position menu reorders without drag", js("return audit.get().rows[0].checklist[2].text==='Venue sourcing'"))
        reset()
        js("audit.openItemsModal('r1')")
        handle = driver.find_elements(By.CSS_SELECTOR, "#itemsEditorBody .drag-order-handle")[0]
        target = driver.find_elements(By.CSS_SELECTOR, "#itemsEditorBody tr")[2]
        ActionChains(driver).move_to_element(handle).click_and_hold().pause(0.15).move_to_element(target).pause(0.15).release().perform()
        check("native mouse drag reorders items", js("return document.querySelector('#itemsEditorBody textarea[data-field=text]').value!=='Venue sourcing'"))
        reset()
        js("audit.openRowsModal()")
        handle = driver.find_elements(By.CSS_SELECTOR, "#rowsEditorBody .drag-order-handle")[0]
        target = driver.find_elements(By.CSS_SELECTOR, "#rowsEditorBody tr")[1]
        ActionChains(driver).move_to_element(handle).click_and_hold().pause(0.1).move_to_element_with_offset(target,0,15).pause(0.1).release().perform()
        js("document.getElementById('applyRowsButton').click()")
        check("native mouse drag reorders rows", js("return audit.get().rows[0].id==='r2'"))
        reset()
        js("audit.openItemsModal('r1');document.getElementById('openLibraryButton').click();document.getElementById('libraryNameInput').value='Venue common';document.getElementById('librarySaveButton').click()")
        check("library saved with checklist preview", js("return document.getElementById('libraryPreview').textContent.includes('Venue sourcing')"))
        js("document.getElementById('libraryReplaceButton').click();document.getElementById('applyItemsButton').click()")
        check("library resets all completion states", js("return audit.get().rows[0].checklist.every(i=>!i.done && i.subitems.every(s=>!s.done))"))
        reset()
        js("const d=audit.get();d.rows[0].checklist[0].remarks='Long detailed remark about venue preparation and equipment. '.repeat(8);audit.set(d);audit.setWorkspaceColumnWidth('remarks',160);window.dispatchEvent(new Event('resize'))")
        time.sleep(0.2)
        check("remarks align after width changes", js("const a=[...document.querySelectorAll('.checklist-entry')],b=[...document.querySelectorAll('.remarks-entry')];return a.every((x,i)=>Math.abs(x.getBoundingClientRect().top-b[i].getBoundingClientRect().top)<1)"))
        reset()
        driver.find_element(By.ID, "rowSearchInput").send_keys("Recruit")
        time.sleep(0.25)
        check("search filters row content", js("return document.querySelectorAll('.programme tbody tr').length===1 && document.querySelector('.action-col[data-row-id]').textContent==='Recruit helpers'"))
        reset()
        js("audit.deleteLocalProjectOnly('AUD-A')")
        check("deleted local project enters recycle bin", js("return JSON.parse(localStorage.getItem('noah_project_trash_v1')).some(e=>e.data.projectCode==='AUD-A')"))
        js("document.querySelector('[data-project-tab=trash]').click()")
        time.sleep(0.08)
        js("const row=[...document.querySelectorAll('#projectListBody tr')].find(r=>r.textContent.includes('AUD-A /'));row.querySelector('.project-list-load-button').click()")
        check("Recycle Bin restores project", js("return audit.get().projectCode==='AUD-A' && audit.get().rows.length===2"))
        print_fixture = copy.deepcopy(FIXTURE)
        print_fixture["audienceMode"] = "actions"
        print_fixture["rows"][0]["checklist"] = [
            {"id": "print" + str(i), "text": "CHECKITEM_%02d" % i, "remarks": "REMARK_%02d" % i}
            for i in range(1, 31)
        ]
        reset(print_fixture)
        js("audit.openOutputPreview()")
        time.sleep(0.3)
        driver.switch_to.frame(driver.find_element(By.ID, "outputFrame"))
        check("PDF preview contains all 30 items", len(driver.find_elements(By.CSS_SELECTOR, ".checklist-entry")) == 30)
        check("PDF preview has no inner scrollbar", js("return getComputedStyle(document.querySelector('.checklist-list')).maxHeight==='none'"))
        driver.switch_to.default_content()
        driver.save_screenshot(str(ARTIFACTS / "preview-desktop.png"))
        preview_html = driver.find_element(By.ID, "outputFrame").get_attribute("srcdoc")
        parent = driver.current_window_handle
        driver.switch_to.new_window("tab")
        driver.get("data:text/html;base64," + base64.b64encode(preview_html.encode()).decode())
        time.sleep(0.15)
        pdf = driver.execute_cdp_cmd("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True})
        (ARTIFACTS / "actions.pdf").write_bytes(base64.b64decode(pdf["data"]))
        driver.close()
        driver.switch_to.window(parent)
        long_fixture = copy.deepcopy(print_fixture)
        long_fixture["rows"][0]["checklist"] = [
            {"id":"long"+str(i),"text":"LONGITEM_%03d"%i,"remarks":"Long remarks "+str(i)}
            for i in range(1,121)
        ]
        reset(long_fixture)
        js("audit.openOutputPreview()")
        time.sleep(0.2)
        long_html = driver.find_element(By.ID, "outputFrame").get_attribute("srcdoc")
        driver.switch_to.new_window("tab")
        driver.get("data:text/html;base64," + base64.b64encode(long_html.encode()).decode())
        pdf = driver.execute_cdp_cmd("Page.printToPDF", {"printBackground":True,"preferCSSPageSize":True})
        (ARTIFACTS / "actions-long.pdf").write_bytes(base64.b64decode(pdf["data"]))
        driver.close()
        driver.switch_to.window(parent)
        js("document.querySelector('[data-close-modal=outputModal]').click();audit.openItemsModal('r1')")
        driver.save_screenshot(str(ARTIFACTS / "editor-desktop.png"))
        driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width":390,"height":844,"deviceScaleFactor":1,"mobile":True})
        time.sleep(0.1)
        check("mobile page has no outer horizontal overflow", js("return document.documentElement.scrollWidth<=innerWidth+1"))
        driver.save_screenshot(str(ARTIFACTS / "editor-mobile.png"))
        errors = [entry["message"] for entry in driver.get_log("browser") if entry["level"] == "SEVERE" and "favicon" not in entry["message"]]
        check("no browser errors", not errors)
        print(json.dumps({"passed": results, "artifacts": str(ARTIFACTS)}, ensure_ascii=False, indent=2))
    except Exception:
        driver.save_screenshot(str(ARTIFACTS / "failure.png"))
        print(json.dumps({"passed": results, "browser_errors": driver.get_log("browser"), "failure_artifacts": str(ARTIFACTS)}, ensure_ascii=False, indent=2))
        raise
    finally:
        driver.quit()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
