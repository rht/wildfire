const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1512,height:1100}}), errors=[], writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push(r.method());});
  const base=process.env.DASHBOARD_BASE_URL || 'http://127.0.0.1:18522';
  await page.goto(base+'/?demo=1#/overview');
  await expect(page.locator('body')).toHaveCSS('background-color','rgb(0, 0, 0)');
  await expect(page.getByText('Nobody left behind',{exact:true})).toBeVisible();
  await expect(page.locator('.brand-wordmark').first()).toHaveText('ResponsAra');
  const mapCard=page.locator('.overview-grid > .MuiCard-root').first(), crewCard=page.locator('.overview-grid > .MuiCard-root').last();
  assert.ok(Math.abs((await crewCard.boundingBox()).width-(await mapCard.boundingBox()).width)<2);
  await page.getByRole('button',{name:'3D tilt',exact:true}).click();
  await expect(page.getByRole('button',{name:'3D tilt',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.map-camera')).toHaveClass(/is-tilted/);
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  await page.getByRole('button',{name:'Zoom out',exact:true}).click();
  await expect.poll(() => page.locator('.leaflet-tile-loaded').count()).toBeGreaterThan(0);
  await page.screenshot({path:'artifacts/dark-overview.png',animations:'disabled'});
  await page.getByRole('button',{name:'2D',exact:true}).click();
  await page.getByRole('button',{name:'Review plan for Crew 1A',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await dialog.getByRole('button',{name:'3D tilt',exact:true}).click();
  await expect(dialog.locator('.map-camera')).toHaveClass(/is-tilted/);
  await expect(dialog.getByRole('button',{name:'Confirm crew plan',exact:true})).toBeEnabled();
  await page.screenshot({path:'artifacts/dark-crew-review.png',animations:'disabled'});
  await page.getByRole('button',{name:'Close details'}).click();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'artifacts/dark-mobile.png',animations:'disabled'});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await page.getByRole('button',{name:'Open navigation'}).click();
  await expect(page.locator('.MuiDrawer-root .brand-wordmark')).toBeVisible();
  await page.getByRole('presentation').getByRole('link',{name:'Buildings & risk',exact:true}).click();
  await expect(page.getByRole('columnheader',{name:'Distance to fire',exact:true})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Assessed at',exact:true})).toHaveCount(0);
  // Test displayed distances against a supplied REST snapshot, not derived coordinates.
  const state={schema_version:'coordination-state-1',scenario_id:'distance-test',revision:1,snapshot_id:'s',as_of:new Date().toISOString(),assets:[{asset_id:'a',name:'Near building',distance_to_fire_m:425},{asset_id:'b',name:'Far building',distance_to_fire_m:1250},{asset_id:'c',name:'Unknown building'}],plan:{locations:[],response:null},contacts:{ranked:[],review:[]},calls:[],teams:[],tasks:[],events:[],errors:[]};
  await page.route('**/api/state',r=>r.fulfill({json:state}));
  await page.routeWebSocket('**/api/updates*',()=>{});
  await page.setViewportSize({width:1512,height:1100});
  for(const route of ['/buildings','/incidents/distance-test/buildings']) {
   await page.goto(base+'/#'+route);
   await expect(page.getByRole('columnheader',{name:'Distance to fire',exact:true})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Assessed at',exact:true})).toHaveCount(0);
   await expect(page.locator('tbody tr').filter({hasText:'Near building'})).toContainText('425 m');
   await expect(page.locator('tbody tr').filter({hasText:'Far building'})).toContainText('1.25 km');
   await expect(page.locator('tbody tr').filter({hasText:'Unknown building'}).locator('.distance-to-fire')).toHaveText('Not supplied');
  }
  await page.getByRole('button',{name:'View building Near building',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('Distance to fire');
  await expect(page.getByRole('dialog')).toContainText('425 m');
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  console.log('Appearance browser passed: black theme, reference branding, equal map/crew panels, 3D toggles/zoom, mobile, global/scoped building distances; no writes.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
