/* Real compiled Angular UI + Python worker + HTTP Ollama double.
 * NODE_PATH=<playwright node_modules> FRM_BROWSER=<chromium> node tests/unified_assistant_browser.cjs
 * Optional: FRM_TEST_PYTHONPATH=<requirements-test target>, FRM_BROWSER_SCREENSHOT=<internal QA path>.
 */
'use strict';
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const delay = ms => new Promise(r=>setTimeout(r,ms));
async function port() {const server=net.createServer();await new Promise(r=>server.listen(0,'127.0.0.1',r));const result=server.address().port;await new Promise(r=>server.close(r));return result;}
async function choose(page,label,value,index=0) {
  await page.getByRole('combobox',{name:label,exact:true}).nth(index).click();
  await page.getByRole('option',{name:value,exact:true}).click();
}
async function until(fn,description) {
  for(let i=0;i<200;i++){if(await fn())return;await delay(100);}throw new Error(description);
}
async function main() {
  const folder=await fs.mkdtemp(path.join(os.tmpdir(),'frm-unified-'));
  const apiPort=await port(), aiPort=await port(), origin='http://localhost:'+apiPort, ai='http://127.0.0.1:'+aiPort;
  const server=spawn(process.env.FRM_PYTHON || 'python3',[path.join(root,'tests/unified_browser_server.py'),String(apiPort),String(aiPort),folder],{
    cwd:root,env:{...process.env,PYTHONPATH:[root,path.join(root,'tests'),process.env.FRM_TEST_PYTHONPATH || ''].join(path.delimiter)}});
  let browser,page,frontend;let serverLog='';server.stderr.on('data',v=>{serverLog+=v;});
  const errors=[], directAi=[], consoleWarnings=[], consoleErrors=[];let currentJob='',runId='';
  try {
    await until(async()=>{try{return(await fetch(origin+'/api/health')).ok;}catch{return false;}},'API did not start: '+serverLog);
    const chromiumModule=process.env.FRM_CHROMIUM_MODULE?require(process.env.FRM_CHROMIUM_MODULE):null;
    const packaged=chromiumModule?.default??chromiumModule;
    const executablePath=process.env.FRM_BROWSER||(packaged?await packaged.executablePath():undefined);
    browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{}),
      args:[...(packaged?.args??[]),'--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-software-rasterizer']});
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    await context.addInitScript(() => localStorage.setItem('frm-options-v6', JSON.stringify({ai_mode:'off',max_ai_calls:0})));
    page=await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',entry=>{if(entry.type()==='warning')consoleWarnings.push(entry.text());if(entry.type()==='error')consoleErrors.push(entry.text());});
    page.on('request',r=>{if(r.url().startsWith(ai))directAi.push(r.url());});
    // A real large module can expose hundreds of bindings even with the panel closed.
    await page.route('**/api/jobs/*/assistant', async route => {
      if (route.request().method() !== 'GET') { await route.continue(); return; }
      const response = await route.fetch(); const index = await response.json();
      if (index.input_bindings?.rows?.length) {
        const row = index.input_bindings.rows[0];
        index.input_bindings.rows.push(...Array.from({length:600}, (_, i) => ({...row,id:'large-'+i,source:'GLOBAL.LARGE_'+i})));
      }
      await route.fulfill({response,json:index});
    });
    await page.goto(origin);
    const dialog=page.getByRole('dialog');await dialog.waitFor();
    await dialog.locator('input').fill(origin+'/api');await dialog.getByRole('button',{name:'Kapcsolódás',exact:true}).click();
    await dialog.waitFor({state:'hidden'});
    await page.getByLabel('Közös Ollama URL').fill(ai+'/api/chat');
    await page.getByLabel('Közös Ollama modell').fill('browser-qwen:30b');
    await page.getByRole('button',{name:'AI-kapcsolat mentése',exact:true}).click();
    await page.getByText('Az Ollama elérhető, a modell megtalálható. A közös AI-kapcsolat elmentve.',{exact:true}).waitFor();
    assert.equal(await page.getByLabel('Közös Ollama URL').inputValue(),ai);
    assert.equal(await page.getByRole('combobox',{name:'Generálás AI módja',exact:true}).count(),0);
    assert.match(await page.getByText('Java-átírás végpontja:',{exact:false}).innerText(),new RegExp(ai+'/api/chat'));
    await page.locator('input[type=file]').first().setInputFiles(path.join(folder,'lookup.xml'));
    await page.locator('input[formcontrolname=module]').fill('lookup');
    await page.locator('input[formcontrolname=AWU_AZON]').fill('1234');
    await page.getByRole('button',{name:'Generálás indítása',exact:true}).click();
    await until(async()=>{const data=await(await fetch(origin+'/api/jobs')).json();return data.jobs.some(j=>j.status==='completed');},'Generation failed: '+serverLog);
    await page.getByRole('dialog').getByRole('button',{name:'Java asszisztens',exact:true}).click();
    await page.getByRole('combobox',{name:'Adatbázistábla',exact:true}).waitFor();
    assert.match(page.url(),/view=assistant/);
    const job=(await(await fetch(origin+'/api/jobs')).json()).jobs.find(j=>j.status==='completed');
    assert(job);currentJob=job.id;
    assert.equal(job.options.ai_mode,'assist');
    assert(job.options.max_ai_calls>=1,'Old saved zero budget must not disable generation AI');
    // Headerless eight-column file import includes the user's exact failing row.
    assert.equal(await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).count(),0);
    await page.getByLabel('Táblaszerkezet CSV-fájl',{exact:true}).setInputFiles({name:'account.csv',mimeType:'text/csv',
      buffer:Buffer.from('\ufeffID,NUMBER,22,20,0,N,0,\r\nNAME,VARCHAR2,100,,,Y,100,B\r\nOSZLOP_NEVE,VARCHAR2,40,,,N,40,B\r\n')});
    await until(async()=>await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).count()===3,'CSV did not populate the grid');
    assert.equal(await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).first().inputValue(),'ID');
    assert.equal(await page.getByRole('spinbutton',{name:'Precision',exact:true}).first().inputValue(),'20');
    assert.equal(await page.getByRole('spinbutton',{name:'Oszlophossz',exact:true}).first().inputValue(),'');
    assert.equal(await page.getByRole('checkbox',{name:'Elsődleges kulcs',exact:true}).first().isChecked(),false);
    assert.equal(await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).nth(2).inputValue(),'OSZLOP_NEVE');
    assert.equal(await page.getByRole('spinbutton',{name:'Oszlophossz',exact:true}).nth(2).inputValue(),'40');
    assert.equal((await(await fetch(origin+'/api/jobs/'+job.id+'/assistant/schema')).json()).pending.length,1);
    const layout=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
      overflowing:[...document.querySelectorAll('app-migrator-assistant,app-migrator-assistant div,app-migrator-assistant p-panel')]
        .map(e=>({tag:e.tagName,cls:e.className,width:e.clientWidth,scroll:e.scrollWidth,right:e.getBoundingClientRect().right,overflow:getComputedStyle(e).overflowX}))
        .filter(e=>e.right>innerWidth).slice(0,18)}));
    assert(layout.scroll<=layout.width+1,'Wide column grids must scroll inside the panel, not widen the page: '+JSON.stringify(layout));
    // Discard restores the original empty draft; malformed paste changes nothing.
    await page.getByRole('button',{name:'Táblavázlat elvetése',exact:true}).click();
    await until(async()=>await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).count()===0,'Discard did not restore the saved table');
    await page.getByText('CSV-szöveg beillesztése és exportminta',{exact:true}).click();
    await page.getByLabel('Táblaszerkezet CSV-szöveg',{exact:true}).fill('ID,NUMBER,22,20,0,N\nID,NUMBER,22,20,0,N');
    await page.getByRole('button',{name:'CSV beolvasása',exact:true}).click();
    await page.getByTestId('assistant-run-feedback').getByText(/hibás vagy ismétlődő oszlopnév/).waitFor();
    assert.equal(await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).count(),0);
    // The exact failing row also works pasted without a header.
    await page.getByLabel('Táblaszerkezet CSV-szöveg',{exact:true}).fill('OSZLOP_NEVE,VARCHAR2,40,,,N,40,B');
    await page.getByRole('button',{name:'CSV beolvasása',exact:true}).click();
    await until(async()=>await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).count()===1,'Exact user CSV paste did not populate the grid');
    assert.equal(await page.getByRole('spinbutton',{name:'Oszlophossz',exact:true}).first().inputValue(),'40');
    await page.getByRole('button',{name:'Táblavázlat elvetése',exact:true}).click();
    await until(async()=>await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).count()===0,'Discard did not restore the empty table');
    // Large CSVs render one page. Edits address stable keys on later pages.
    await page.getByLabel('Táblaszerkezet CSV-szöveg',{exact:true}).fill(
      Array.from({length:200},(_,i)=>'COLUMN_'+String(i).padStart(3,'0')+',VARCHAR2,40,,,Y,40,B').join('\n'));
    await page.getByRole('button',{name:'CSV beolvasása',exact:true}).click();
    await until(async()=>await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).count()===10,'Large CSV must paginate');
    await page.getByRole('button',{name:'Last Page',exact:true}).click();
    await until(async()=>await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).first().inputValue()==='COLUMN_190','Last page did not open');
    await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).first().fill('CHANGED_LAST_PAGE');
    await page.getByRole('button',{name:'First Page',exact:true}).click();
    await until(async()=>await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).first().inputValue()==='COLUMN_000','First page did not open');
    await page.getByRole('button',{name:'Last Page',exact:true}).click();
    await until(async()=>await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).first().inputValue()==='CHANGED_LAST_PAGE','Last-page edit did not persist');
    await page.getByRole('button',{name:'Táblavázlat elvetése',exact:true}).click();
    await until(async()=>await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).count()===0,'Discard did not reset paging');
    // CHAR semantics must use character length rather than byte length on save.
    await page.getByLabel('Táblaszerkezet CSV-szöveg',{exact:true}).fill('ID,NUMBER,22,20,0,N,0,\nNAME,VARCHAR2,400,,,Y,100,C');
    await page.getByRole('button',{name:'CSV beolvasása',exact:true}).click();
    await until(async()=>await page.getByRole('textbox',{name:'Oszlopnév',exact:true}).count()===2,'CSV paste did not populate the grid');
    assert.equal(await page.getByRole('spinbutton',{name:'Oszlophossz',exact:true}).nth(1).inputValue(),'100');
    await page.getByRole('checkbox',{name:'Elsődleges kulcs',exact:true}).first().check();
    await page.getByRole('button',{name:'Táblaszerkezet mentése',exact:true}).click();
    await until(async()=>{const index=await(await fetch(origin+'/api/jobs/'+job.id+'/assistant')).json();return index.schema.pending.length===0;},'Table schema was not stored');
    const savedSchema=await(await fetch(origin+'/api/jobs/'+job.id+'/assistant/schema/download')).json();
    assert.equal(savedSchema.tables['APP.ACCOUNT'].columns.NAME.char_length,100);
    assert.equal(savedSchema.tables['APP.ACCOUNT'].columns.NAME.length_semantics,'CHAR');
    // The basic flow needs no frontend-binding panel or data-access selection.
    assert.equal(await page.getByRole('combobox',{name:'Adatelérés',exact:true}).count(),0);
    assert.equal(await page.getByRole('combobox',{name:'Bemenet forrása',exact:true}).count(),0,'Closed panel must not create its controls');
    await page.getByText('Haladó: képernyőértékek bekötése',{exact:true}).click();
    await until(async()=>await page.getByRole('combobox',{name:'Bemenet forrása',exact:true}).count()===10,'Large binding inventory must paginate');
    await page.getByText('Haladó: képernyőértékek bekötése',{exact:true}).click();
    await until(async()=>await page.getByRole('combobox',{name:'Bemenet forrása',exact:true}).count()===0,'Closed binding controls must be destroyed');
    const originalController=await(await fetch(origin+'/api/jobs/'+job.id+'/file?path=backend/DPS/LookupControllerImpl.java')).json();
    assert.equal(await page.getByRole('button',{name:'Trigger javásítása',exact:true}).isEnabled(),true);
    assert.equal(await page.getByLabel('Asszisztens feladata',{exact:true}).count(),0);
    await page.getByText('Nem kell promptot írnod.',{exact:false}).waitFor();
    // A rejected POST gives the real validation detail at the clicked button.
    const feedback=page.getByTestId('assistant-run-feedback');
    const assistantUrl=origin+'/api/jobs/'+job.id+'/assistant';
    const invalidStart=async route=>{
      if(route.request().method()==='POST') await route.fulfill({status:422,contentType:'application/json',
        body:JSON.stringify({detail:[{loc:['body','num_ctx'],msg:'Input should be greater than or equal to 4096'}]})});
      else await route.continue();
    };
    await page.route(assistantUrl,invalidStart);
    await page.getByRole('button',{name:'Trigger javásítása',exact:true}).click();
    await feedback.getByText(/num_ctx: Input should be greater than or equal to 4096/).waitFor();
    assert(await feedback.evaluate(e=>{const rect=e.getBoundingClientRect();return rect.top<innerHeight&&rect.bottom>0;}),'Failure must appear in the current viewport by the trigger button');
    assert.equal(await page.getByRole('button',{name:'Trigger javásítása',exact:true}).isEnabled(),true);
    assert.equal(await page.getByRole('combobox',{name:'Asszisztens modul',exact:true}).isEnabled(),true);
    await page.unroute(assistantUrl,invalidStart);
    // A background preflight failure also stays visible and survives reload.
    await page.getByText('Memória és futtatás',{exact:true}).click();
    await page.getByLabel('Kontextus (token)',{exact:true}).fill('4096');
    await page.getByRole('button',{name:'Trigger javásítása',exact:true}).click();
    await feedback.getByText(/A teljes kontextus.*num_ctx/).waitFor();
    assert.equal(await page.getByRole('button',{name:'Trigger javásítása',exact:true}).isEnabled(),true);
    await page.reload();
    await feedback.getByText(/Legutóbbi AI-futás: A teljes kontextus/).waitFor();
    await page.getByText('Memória és futtatás',{exact:true}).click();
    await page.getByLabel('Kontextus (token)',{exact:true}).fill('32768');
    await page.getByRole('button',{name:'Trigger javásítása',exact:true}).click();
    // Refresh while inference runs: the server returns active_run for recovery.
    await until(async()=>{const active=(await(await fetch(origin+'/api/jobs/'+job.id+'/assistant')).json()).active_run;if(active)runId=active.id;return Boolean(active);},'No active run');
    await until(async()=>await page.getByRole('combobox',{name:'Asszisztens modul',exact:true}).isDisabled(),'The reactive module control must be disabled while the model works');
    await page.reload();
    await page.getByText('A tesztválasz további projektmintát kér.',{exact:true}).waitFor({timeout:30000});
    await page.getByText('Kiegészítő forrás (ha az AI kéri)',{exact:true}).click();
    await page.getByLabel('Projektminta',{exact:true}).fill('A projektminta a második böngészőteszt-kérésben már rendelkezésre áll.');
    await page.getByRole('button',{name:'Trigger javásítása',exact:true}).click();
    await page.getByText('Rögzített böngészőteszt-javaslat; nem üzleti működésigazolás.',{exact:true}).waitFor({timeout:30000});
    await page.getByRole('combobox',{name:'Javaslat fájlja',exact:true}).waitFor();
    assert.match(await page.locator('app-migrator-assistant pre[tabindex="0"]').innerText(),/Böngészőteszt/);
    const apply=page.getByRole('button',{name:'Beillesztés a fájlokba',exact:true});
    assert.equal(await apply.isEnabled(),true);
    await apply.click();
    await page.getByText('A Java-javaslat átvéve, a letölthető ZIP-ek frissültek. Fordítsd és teszteld a céges projektben.',{exact:true}).waitFor();
    const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Friss modul ZIP',exact:true}).click();
    assert.equal((await downloaded).suggestedFilename(),'module.zip');
    const allRequests=await(await fetch(ai+'/requests')).json();
    assert.equal(job.summary.ai.mode,'assist','Generation keeps AI enabled even when this fixture needs no preliminary advice');
    const sent=allRequests.filter(request=>request.messages);
    assert.equal(sent.length,2);assert(sent.every(r=>r.model==='browser-qwen:30b'));
    // 4.34: the selected method in a Java block and two lines per used table; a free Markdown answer.
    const prompt=sent[1].messages[1].content;
    assert.match(sent[1].messages[0].content,/Oracle Forms/);
    assert.equal(sent[1].format,undefined,'No JSON-schema answer: the model writes Java code blocks');
    assert.match(prompt,/```java\npublic ActionResult onCtrlRun\(/);
    assert.match(prompt,/SELECT a.NAME/);
    assert.match(prompt,/- lookupAppAccountRepository \(LookupAppAccountRepository\) for entity LookupAppAccountEntity, table APP.ACCOUNT/);
    assert.match(prompt,/BigDecimal id \(ID, primary key\)/);
    assert.match(prompt,/A projektminta a második böngészőteszt-kérésben már rendelkezésre áll\./);
    assert(!prompt.includes('@Entity'),'The entity is described in a line, not sent as Java source');
    assert(!prompt.includes('Additional requirements'),'No empty optional sections');
    // The AI log shows what was sent and what came back, also without rebuilding: /ai-log.
    await page.getByText('AI-kommunikáció: mit küldtünk a modellnek, és mit válaszolt',{exact:true}).click();
    const exchangeView=page.getByTestId('assistant-exchange');
    await exchangeView.waitFor();
    assert.match(await exchangeView.innerText(),/findAccount/);
    await page.getByRole('button',{name:'Küldendő prompt előnézete',exact:true}).click();
    await page.getByTestId('assistant-prompt-preview').waitFor();
    assert.match(await page.getByTestId('assistant-prompt-preview').innerText(),/onCtrlRun/);
    const logPage=await fetch(origin+'/ai-log?job='+job.id);
    assert.equal(logPage.status,200);
    assert.match(await logPage.text(),/AI-napló/);
    const finalController=await(await fetch(origin+'/api/jobs/'+job.id+'/file?path=backend/DPS/LookupControllerImpl.java')).json();
    assert.equal(finalController.text,originalController.text);
    const finalRepository=await(await fetch(origin+'/api/jobs/'+job.id+'/file?path=backend/DPS/LookupAppAccountRepository.java')).json();
    assert.match(finalRepository.text,/entityManager.find/);
    assert(!finalRepository.text.includes('SELECT '));
    const finalService=await(await fetch(origin+'/api/jobs/'+job.id+'/file?path=backend/DPS/LookupServiceImpl.java')).json();
    assert.match(finalService.text,/lookupAppAccountRepository.findAccount/);
    const finalDto=await(await fetch(origin+'/api/jobs/'+job.id+'/file?path=backend/CL/LookupAccountDto.java')).json();
    assert.match(finalDto.text,/class LookupAccountDto/);
    assert.equal(directAi.length,0,'The browser must only call the migrator; Ollama is called server-side');
    assert.deepEqual(errors,[],'Browser runtime errors');
    assert.deepEqual(consoleErrors.filter(message=>/RangeError|Maximum call stack|NG\d{4}/.test(message)),[],'No Angular runtime errors, including with large inventories');
    assert(!consoleWarnings.some(message=>message.includes('disabled attribute with a reactive form directive')),'Reactive control must use FormControl.disable(), including in development builds');
    assert(consoleErrors.some(message=>message.includes('[Migrátor] num_ctx:')),'POST validation detail must be in the console');
    assert(consoleErrors.some(message=>message.includes('[Migrátor] A teljes kontextus')),'Background failure detail must be in the console');
    if(process.env.FRM_BROWSER_SCREENSHOT)await page.screenshot({path:process.env.FRM_BROWSER_SCREENSHOT,fullPage:true});
    // Legacy bookmarks lead to the same Angular app and keep the selected module.
    const old=await fetch(origin+'/assistant?job='+job.id,{redirect:'manual'});
    assert.equal(old.status,307);assert.equal(old.headers.get('location'),'/?view=assistant&job='+job.id);
    // A separate frontend origin uses the configured API for all assistant reads/writes.
    const webRoot=path.join(root,'web-dist/browser');
    frontend=http.createServer(async(req,res)=>{
      try {
        const pathname=new URL(req.url,'http://localhost').pathname;
        const file=path.resolve(webRoot,'.'+(pathname==='/'?'/index.html':pathname));
        if(!file.startsWith(webRoot+path.sep)){res.writeHead(403);res.end();return;}
        const content=await fs.readFile(file);
        res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(content);
      }catch{res.writeHead(404);res.end();}
    });
    await new Promise((resolve,reject)=>{frontend.once('error',reject);frontend.listen(4200,'127.0.0.1',resolve);});
    const other=await browser.newContext({viewport:{width:1440,height:1000}}),remote=await other.newPage(),wrongApi=[];
    remote.on('pageerror',e=>errors.push(e.message));remote.on('request',r=>{if(r.url().startsWith('http://localhost:4200/api/'))wrongApi.push(r.url());});
    await remote.goto('http://localhost:4200/?view=assistant&job='+job.id);
    const settings=remote.getByRole('dialog');await settings.waitFor();await settings.locator('input').fill(origin+'/api');
    await settings.getByRole('button',{name:'Kapcsolódás',exact:true}).click();await settings.waitFor({state:'hidden'});
    await remote.getByRole('textbox',{name:'Oszlop megjegyzése',exact:true}).first().fill('Ellenőrizve a külön Angular originről.');
    await remote.getByRole('button',{name:'Táblaszerkezet mentése',exact:true}).click();
    await until(async()=>{const schema=await(await fetch(origin+'/api/jobs/'+job.id+'/assistant/schema')).json();return schema.tables[0].definition.columns.ID.comment==='Ellenőrizve a külön Angular originről.';},'Separate-origin schema write failed');
    assert.equal(new URL(remote.url()).origin,'http://localhost:4200');assert.deepEqual(wrongApi,[]);assert.deepEqual(errors,[]);
    console.log('Unified Angular browser: upload → generation → CSV file/paste/reject/discard → schema save → automatic JPA prompt → needs_context → reload/resume → formatted proposal → insert service/entity/repository/DTO → ZIP; separate frontend origin OK');
  } catch(error) {console.error('Server log:',serverLog,'Browser errors:',errors,'Console errors:',consoleErrors);if(currentJob){console.error('INDEX',JSON.stringify(await(await fetch(origin+'/api/jobs/'+currentJob+'/assistant')).json()).slice(0,2000));if(runId)console.error('RUN',await(await fetch(origin+'/api/jobs/'+currentJob+'/assistant/runs/'+runId)).json());}console.error('AI CALLS',(await(await fetch(ai+'/requests')).json()).length);if(page){console.error((await page.locator('body').innerText()).slice(-6000));if(process.env.FRM_BROWSER_SCREENSHOT)await page.screenshot({path:process.env.FRM_BROWSER_SCREENSHOT+'.failed.png',fullPage:true});}throw error;}
  finally{await browser?.close();if(frontend)await new Promise(r=>frontend.close(r));server.kill('SIGTERM');await fs.rm(folder,{recursive:true,force:true});}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
