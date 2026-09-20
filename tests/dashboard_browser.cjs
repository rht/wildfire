/* Optional real-browser smoke test against the explicitly started local demo. */
const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const options={headless:true};
 if(process.env.DASHBOARD_BROWSER_EXECUTABLE) options.executablePath=process.env.DASHBOARD_BROWSER_EXECUTABLE;
 const browser=await chromium.launch(options);
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push(r.method()+' '+r.url());});
  await page.goto(process.env.DASHBOARD_BASE_URL||'http://127.0.0.1:8521/');
  await page.locator('#rankedList .row').first().waitFor();
  await page.locator('#rankedList .row').first().click();
  assert.match(await page.locator('#detailPanel').innerText(),/Message acknowledged/);
  assert.match(await page.locator('#modeBadge').innerText(),/OFFLINE DEMO/);
  assert.equal(await page.locator('#notifyPhoneBtn').isDisabled(),true);
  assert.equal(await page.locator('#sideCol .card').evaluateAll(cards=>cards.every(card=>card.scrollHeight<=card.clientHeight+1)),true);
  await page.screenshot({path:'data/dashboard-verification/dashboard.png',fullPage:true});
  await page.locator('#openDbBtn').click();
  assert.equal(await page.locator('#dbTableBody tr').count(),6);
  await page.locator('#backToMapBtn').click();await page.locator('#changeLogBtn').click();
  assert.equal(await page.locator('#changeLog').isVisible(),true);
  await page.locator('#nextUpdateBtn').click();
  await page.waitForFunction(()=>document.getElementById('statusBadge').textContent.startsWith('connected'));
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  console.log('Browser smoke passed: real REST/WebSocket, selection, database, log, reconnect; no writes or page errors.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
