const {chromium,expect}=require('@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1024,height:768},hasTouch:true,isMobile:true}),errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push(r.method());});
  const base=process.env.DASHBOARD_BASE_URL||'http://127.0.0.1:18522';
  await page.goto(base+'/?demo=1#/incidents/gavarres/summary');
  const slider=page.getByLabel('Snapshot time');
  await expect(slider).toHaveAttribute('max','3');
  await expect(page.getByLabel('Time travel')).toContainText('Illustrative simulated spread');
  const shape=page.locator('.incident-map path[fill="#ff603e"][stroke="#ff603e"]');
  const width=()=>shape.evaluate(e=>e.getBBox().width);
  const finalWidth=await width();
  for(let i=0;i<3;i++)await page.getByRole('button',{name:'Previous',exact:true}).tap();
  await expect(slider).toHaveValue('0');
  await expect(page.getByLabel('Time travel')).toContainText('Stage 1 of 4');
  const widths=[await width()];
  await page.screenshot({path:'artifacts/fire-spread-stage-1.png'});
  for(let stage=1;stage<4;stage++){
   await page.getByRole('button',{name:'Next',exact:true}).tap();
   await expect(slider).toHaveValue(String(stage));
   widths.push(await width());
  }
  for(let i=1;i<widths.length;i++)assert.ok(widths[i]>widths[i-1]*1.2,`perimeter expands: ${widths}`);
  assert.ok(Math.abs(widths[3]-finalWidth)<2,'map scale remains stable while replaying one incident');
  await page.screenshot({path:'artifacts/fire-spread-stage-4.png'});
  await page.reload();
  await expect(slider).toHaveAttribute('max','3');
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  console.log('Fire simulation browser passed: four persisted moments, visible expanding perimeter at stable map zoom, stage labels and reload; no writes.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
