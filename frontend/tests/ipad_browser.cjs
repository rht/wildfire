const { chromium, expect } = require('@playwright/test');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const base=process.env.DASHBOARD_BASE_URL || 'http://127.0.0.1:18522';
 try {
  for (const viewport of [{width:768,height:1024},{width:1024,height:768},{width:820,height:1180},{width:1366,height:1024}]) {
   const page=await browser.newPage({viewport,isMobile:true,hasTouch:true,deviceScaleFactor:2}), errors=[], writes=[];
   page.on('pageerror',e=>errors.push(e.message));
   page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push(r.method());});
   await page.goto(base+'/?demo=1#/overview');
   await expect(page.getByTestId('metric-people')).toContainText('Assistance logs');
   await expect(page.getByTestId('metric-people')).toContainText('confirmed need');
   await expect(page.locator('body')).toHaveCSS('background-color','rgb(255, 255, 255)');
   await expect(page.locator('.header-brand')).toBeVisible();
   const cards=page.locator('.overview-grid > .MuiCard-root');
   const map=await cards.first().boundingBox(), crew=await cards.last().boundingBox();
   assert.ok(Math.abs(map.width-crew.width)<2);
   assert.ok(Math.abs(map.y-crew.y)<2);
   assert.ok(Math.abs(map.height-crew.height)<2);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert.ok(await page.locator('.crew-plan-list').evaluate(e=>e.scrollHeight>e.clientHeight),'crew list scrolls inside fixed panel');
   const panelBefore=await cards.last().boundingBox();
   await page.locator('.crew-plan-list').evaluate(e=>e.scrollTop=e.scrollHeight);
   const panelAfter=await cards.last().boundingBox();
   assert.deepEqual(panelBefore,panelAfter);
   await expect(page.getByRole('button',{name:'Review plan for Engine 32',exact:true})).toBeVisible();
   await page.locator('.crew-plan-list').evaluate(e=>e.scrollTop=0);
   const heat=page.getByRole('button',{name:'Heat map',exact:true});
   const touch=await heat.boundingBox();assert.ok(touch.height>=44 && touch.width>=44);
   await heat.tap();
   await expect(page.getByRole('status',{name:'Heat scale'})).toContainText('Building risk · 0–100');
   await expect(page.locator('.risk-heat-canvas')).toHaveCount(1);
   assert.ok(await page.locator('.risk-heat-canvas').evaluate(c=>c.getContext('2d').getImageData(0,0,c.width,c.height).data.some(v=>v>0)));
   await page.getByRole('button',{name:'3D tilt',exact:true}).tap();
   await expect(page.locator('.map-camera')).toHaveClass(/is-tilted/);
   await expect(page.getByRole('status',{name:'Heat scale'})).toBeVisible();
   await page.screenshot({path:`artifacts/ipad-${viewport.width}.png`});
   await heat.tap(); await expect(page.locator('.risk-heat-canvas')).toHaveCount(0);
   await page.getByRole('button',{name:'Review plan for Crew 1A',exact:true}).tap();
   const dialog=page.getByRole('dialog');
   const reviewMap=await dialog.locator('.crew-route-review').boundingBox(), steps=await dialog.locator('.crew-review-steps').boundingBox();
   assert.ok(Math.abs(reviewMap.width-steps.width)<2);
   await expect(dialog.getByRole('button',{name:'Confirm crew plan',exact:true})).toBeEnabled();
   await page.screenshot({path:`artifacts/ipad-review-${viewport.width}.png`});
   await dialog.getByRole('button',{name:'Close details'}).tap();
   await page.getByRole('button',{name:'Open navigation'}).tap();
   await page.getByRole('presentation').getByRole('link',{name:'Resources',exact:true}).tap();
   await expect(page.getByRole('columnheader',{name:'Priority',exact:true})).toBeVisible();
   await expect(page.locator('tbody tr').first()).toContainText('Crew 1A');
   await page.goto(base+'/?demo=1#/people');
   await expect(page.getByRole('columnheader',{name:'Priority',exact:true})).toHaveCount(2);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   const tables = page.locator('main > .MuiCard-root .table-scroll');
   for (const table of await tables.all()) {
    assert.ok(await table.evaluate(e=>e.getBoundingClientRect().bottom<=e.closest('.MuiCard-root').getBoundingClientRect().bottom+1),'table viewport must not be clipped by its card');
    await table.scrollIntoViewIfNeeded();
    await table.evaluate(e=>e.scrollTop=e.scrollHeight);
    await expect(table.locator('tbody tr').last()).toBeInViewport();
   }
   assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
   await page.close();
  }
  console.log('iPad checks passed: 4 portrait/landscape touch viewports, fixed equal panels, internal scroll, white surfaces, heat/legend/3D, confirmation review, assistance KPI and priority tables; no writes.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
