// Controlled transport regression: actual React build, mock only REST/WebSocket boundary.
const { chromium, expect } = require("@playwright/test");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.DASHBOARD_BROWSER_EXECUTABLE ||
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  });
  try {
    const page = await browser.newPage(),
      errors = [],
      writes = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error" && !m.text().includes("Failed to load resource"))
        errors.push(m.text());
    });
    page.on("request", (r) => {
      if (!["GET", "HEAD"].includes(r.method())) writes.push(r.method());
    });
    const base = process.env.DASHBOARD_BASE_URL || "http://127.0.0.1:18522";
    const state = {
      schema_version: "coordination-state-1",
      scenario_id: "test",
      snapshot_id: "snap",
      revision: 1,
      as_of: new Date().toISOString(),
      input_mode: "offline_demo",
      assets: [
        {
          asset_id: "a",
          name: "Test location <img data-injected>",
          estimated_occupancy: 100,
        },
      ],
      contacts: {
        ranked: [],
        review: [{ asset_id: "a", review_reasons: ["missing_fire_arrival"] }],
      },
      calls: [
        {
          request_id: "one",
          asset_id: "a",
          status: "completed",
          can_self_evacuate: true,
          observed_at: "2026-09-20T10:00:00Z",
        },
      ],
      plan: { locations: [], response: null },
      events: [
        {
          event_id: "one",
          kind: "assessment",
          as_of: "2026-09-20T10:00:00Z",
          notes: "Initial assessment",
        },
      ],
      teams: [],
      tasks: [],
      errors: [],
    };
    let stream;
    await page.route("**/api/state", (r) => r.fulfill({ json: state }));
    await page.routeWebSocket("**/api/updates*", (ws) => {
      stream = ws;
    });
    await page.goto(base + "/#/incidents/test/calls");
    await expect.poll(() => !!stream).toBe(true);
    await page
      .getByRole("button", {
        name: "View call details for Test location <img data-injected>",
      })
      .click();
    await expect(page.getByRole("dialog")).toContainText(
      "Missing fire arrival",
    );
    state.revision = 2;
    state.calls.push({
      request_id: "two",
      asset_id: "a",
      status: "completed",
      reported_needs_assistance: true,
      wants_human: true,
      observed_at: "2026-09-20T10:01:00Z",
    });
    state.events = [
      {
        event_id: "two",
        kind: "human_followup",
        as_of: "2026-09-19T09:00:00Z",
        notes: "Late report, received second",
      },
    ];
    stream.send(JSON.stringify(state));
    await expect(page.getByRole("dialog")).toContainText("Assistance needed");
    await expect(page.getByRole("dialog")).toContainText("Human requested");
    assert.equal(await page.locator("[data-injected]").count(), 0);
    state.revision = 3;
    state.assets = [];
    state.contacts = { ranked: [], review: [] };
    state.calls = [];
    state.events = [];
    stream.send(JSON.stringify(state));
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.goto(base + "/#/log");
    await expect(page.locator("tbody tr")).toHaveCount(2);
    await expect(page.locator("tbody tr").first()).toContainText(
      "Late report, received second",
    );
    await page.getByLabel("Event order").selectOption("oldest");
    await expect(page.locator("tbody tr").first()).toContainText(
      "Initial assessment",
    );
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, []);
    console.log(
      "React live-state regression passed: review reasons, selected call updates/removal, literal hostile text, retained log arrival order.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
