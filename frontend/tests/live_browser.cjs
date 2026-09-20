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
    state.revision = 4;
    state.assets = [{ asset_id: "a", name: "Legacy location" }];
    state.plan = {
      locations: [],
      response: {
        steps: [
          {
            asset_id: "a",
            action_id: "legacy-step",
            start_min: 1,
            finish_min: 2,
          },
        ],
      },
    };
    await page.route("**/api/crew-approvals?*", (route) =>
      route.fulfill({
        json: {
          source: "connected",
          incident_id: "test",
          snapshot_id: "snap",
          revision: 4,
          analyst: "@mirrdj",
          plans: [],
          events: [],
        },
      }),
    );
    await page.goto(base + "/#/incidents/test/plan");
    await page
      .getByRole("button", {
        name: "Review plan for Unspecified crew",
        exact: true,
      })
      .click();
    await expect(page.getByRole("dialog")).toContainText(
      "A crew identifier and current proposed plan are needed",
    );
    await expect(
      page
        .getByRole("dialog")
        .getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toBeDisabled();
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
    state.revision = 5;
    state.teams = [{ team_id: "crew-1", name: "Crew map test" }];
    state.assets = [
      { asset_id: "a", name: "First destination", latitude: 41.1, longitude: 2.1 },
      { asset_id: "b", name: "Second destination", latitude: 41.2, longitude: 2.2 },
      { asset_id: "c", name: "Third destination", latitude: 41.3, longitude: 2.3 },
    ];
    state.plan.response = {
      teams: [{
        team_id: "crew-1",
        current_location: { latitude: 41, longitude: 2, observed_at: "2026-09-20T10:30:00Z", source: "Operations <img data-injected>" },
        start_node: [99, 99],
        tasks: state.assets.map((asset, index) => ({
          action_id: `stop-${index}`,
          asset_id: asset.asset_id,
          status: index === 0 ? "confirmed" : "proposed",
          path_lonlat: [[2 + index / 10, 41 + index / 10], [asset.longitude, asset.latitude]],
        })),
      }],
    };
    stream.send(JSON.stringify(state));
    await page.getByRole("button", { name: "Review plan for Crew map test", exact: true }).click();
    const map = page.getByLabel("Plan map for Crew map test", { exact: true });
    await expect(map).toBeVisible();
    await expect(map.locator(".crew-stop-pin")).toHaveText(["1", "2", "3"]);
    await expect(map.locator(".crew-position-pin")).toHaveCount(1);
    await expect(map.locator('path[stroke-dasharray="8 7"]')).toHaveCount(3);
    await expect(page.getByRole("dialog")).toContainText("GPS: 41.00000, 2.00000");
    await expect(page.getByRole("dialog")).toContainText("Observed: 2026-09-20 10:30:00 UTC");
    await expect(page.getByRole("dialog")).toContainText("Source: Operations <img data-injected>");
    assert.equal(await page.locator("[data-injected]").count(), 0);
    await map.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "artifacts/crew-route-map.png", animations: "disabled" });
    state.revision = 6;
    state.plan.response.teams[0].current_location = null;
    state.plan.response.teams[0].tasks[1].path_lonlat = [[2, 41], null, [2.2, 41.2]];
    stream.send(JSON.stringify(state));
    await expect(map.locator(".crew-position-pin")).toHaveCount(0);
    assert.deepEqual(errors, []);
    await expect(map.locator('path[stroke-dasharray="8 7"]')).toHaveCount(2);
    await expect(page.getByRole("dialog")).toContainText("Current crew location not supplied.");
    await expect(page.getByRole("dialog")).toContainText("Path not supplied for stops: 2.");
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
    await page.goto(base + "/#/incidents/test/evacuation");
    await expect(page.getByRole("heading", { name: "Identified groups", exact: true })).toBeVisible();
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
