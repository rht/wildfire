// Render the current UI against terminal call records, then explicit lifecycle updates.
const { chromium, expect } = require('@playwright/test');
const assert = require('node:assert/strict');
(async () => {
  const state = process.env.DASHBOARD_CALL_STATE_URL
    ? await (await fetch(process.env.DASHBOARD_CALL_STATE_URL)).json()
    : {
      schema_version: 'coordination-state-1', scenario_id: 'end-to-end-demo',
      snapshot_id: 'call-count-test', revision: 1, as_of: new Date().toISOString(),
      input_mode: 'offline_demo', contacts: {ranked: [], review: []},
      plan: {locations: [], response: null}, events: [], teams: [], tasks: [], errors: [],
      assets: ['A', 'B', 'C'].map(asset_id => ({asset_id, name: `Location ${asset_id}`})),
      calls: ['A', 'B', 'C'].map(asset_id => ({asset_id, request_id: `snapshot-${'request-'.repeat(30)}-${asset_id}`, status: 'no_answer', dispatch_state: 'bound', human_followup_required: true})),
    };
  assert.equal(state.calls.length, 3);
  assert.ok(state.calls.every(call => call.status === 'no_answer'));
  const browser = await chromium.launch({headless: true, executablePath: process.env.DASHBOARD_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 1512, height: 1050}});
    const errors = [], writes = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (!['GET', 'HEAD'].includes(request.method())) writes.push(request.method()); });
    await page.route('**/api/state', route => route.fulfill({json: state}));
    let stream;
    await page.routeWebSocket('**/api/updates*', socket => {stream = socket;});
    const base = process.env.DASHBOARD_BASE_URL || 'http://127.0.0.1:18522';
    await page.goto(`${base}/?demo=0#/incidents/${state.incident_id || state.scenario_id}/calls`);
    await expect.poll(() => !!stream).toBe(true);
    const card = label => page.getByRole('button', {name: new RegExp(`^${label}`)});
    await expect(card('Voice assistant to call')).toContainText('0');
    await expect(card('Call attempted')).toContainText('3');
    await expect(card('Call completed')).toContainText('0');
    await expect(card('Human follow-up')).toContainText('3');
    await expect(page.locator('tbody tr')).toHaveCount(3);
    assert.ok(await page.locator('.call-request-id').evaluateAll(elements => elements.every(element => element.getBoundingClientRect().width <= 181 && getComputedStyle(element).overflowWrap === 'anywhere')));
    await page.screenshot({path: 'artifacts/call-counts.png', animations: 'disabled'});
    await card('Voice assistant to call').click();
    await expect(page.locator('tbody tr')).toHaveCount(0);
    await card('Call attempted').click();
    await expect(page.locator('tbody tr')).toHaveCount(3);
    await card('Call completed').click();
    await expect(page.locator('tbody tr')).toHaveCount(0);
    const retry = {asset_id: state.calls[0].asset_id, request_id: 'explicit-queued-retry', status: 'queued', dispatch_state: 'not_started'};
    state.calls.push(retry);
    state.revision++;
    stream.send(JSON.stringify(state));
    await expect(card('Voice assistant to call')).toContainText('1');
    await expect(card('Call attempted')).toContainText('3');
    await card('Voice assistant to call').click();
    await expect(page.locator('tbody tr')).toHaveCount(1);
    await expect(page.locator('tbody tr')).toContainText('Queued request');
    for (const queueState of ['cancelled', 'review', 'started']) {
      retry.queue_state = queueState;
      state.revision++;
      stream.send(JSON.stringify(state));
      await expect(card('Voice assistant to call')).toContainText('0');
      await expect(page.locator('tbody tr')).toHaveCount(0);
    }
    await card('All locations').click();
    await expect(page.locator('tbody')).toContainText('Queue: Started');
    retry.queue_state = 'pending';
    state.revision++;
    stream.send(JSON.stringify(state));
    await expect(card('Voice assistant to call')).toContainText('1');
    await card('Voice assistant to call').click();
    await expect(page.locator('tbody tr')).toHaveCount(1);
    retry.status = 'completed';
    retry.dispatch_state = 'bound';
    state.revision++;
    stream.send(JSON.stringify(state));
    await expect(card('Voice assistant to call')).toContainText('0');
    await expect(card('Call attempted')).toContainText('3');
    await expect(card('Call completed')).toContainText('1');
    await card('Call completed').click();
    await expect(page.locator('tbody tr')).toHaveCount(2);
    await expect(page.locator('tbody')).toContainText('explicit-queued-retry');
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, []);
    console.log('Call count browser passed: no-answer attempts, explicit retry, completion, unique-location counts and wrapped IDs; no writes.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
