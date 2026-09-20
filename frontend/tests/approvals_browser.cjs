const { chromium, expect } = require("@playwright/test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const root = path.resolve(__dirname, "../..");
(async () => {
  let child, browser;
  let base = process.env.DASHBOARD_APPROVAL_BASE_URL;
  const artifactRoot = path.join(root, "frontend/artifacts");
  fs.mkdirSync(artifactRoot, { recursive: true });
  const database = path.join(
    fs.mkdtempSync(path.join(artifactRoot, "approval-smoke-")),
    "approvals.sqlite3",
  );
  let port;
  async function stopServer() {
    if (child && child.exitCode === null) {
      const exited = new Promise((resolve) => child.once("exit", resolve));
      child.kill("SIGTERM");
      await exited;
    }
  }
  async function startServer() {
    child = spawn(
      path.resolve(root, "../live-dashboard/.venv/bin/python"),
      [
        "-m",
        "fireline.dashboard_server",
        "--demo",
        "--port",
        String(port),
        "--approvals-database",
        database,
      ],
      {
        cwd: root,
        env: { ...process.env, PYTHONPATH: "." },
        stdio: ["ignore", "ignore", "pipe"],
      },
    );
    let failure = "";
    child.stderr.on("data", (chunk) => {
      failure += chunk.toString();
    });
    await expect
      .poll(
        async () => {
          if (child.exitCode !== null) throw new Error(failure);
          try {
            return (await fetch(base + "/api/state")).ok;
          } catch {
            return false;
          }
        },
        { timeout: 15000 },
      )
      .toBe(true);
  }
  try {
    if (!base) {
      const socket = net.createServer();
      await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
      port = socket.address().port;
      await new Promise((resolve) => socket.close(resolve));
      base = `http://127.0.0.1:${port}`;
      await startServer();
    }
    browser = await chromium.launch({
      headless: true,
      executablePath:
        process.env.DASHBOARD_BROWSER_EXECUTABLE ||
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    });
    const page = await browser.newPage({
      viewport: { width: 1512, height: 1050 },
    });
    const errors = [],
      writes = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (request.method() === "POST") writes.push(request.url());
    });
    const original = await (await fetch(base + "/api/state")).json();
    const builtDemo = await (
      await fetch(base + "/assets/design-demo.json")
    ).json();
    assert.match(builtDemo[0].snapshot_id, /^gavarres-snapshot-[a-f0-9]{64}$/);
    const oldSnapshot = new URLSearchParams({
      source: "design_demo",
      incident_id: "gavarres",
      snapshot_id: "gavarres-snapshot",
      revision: "1",
    });
    assert.equal(
      (await fetch(base + "/api/crew-approvals?" + oldSnapshot)).status,
      409,
    );
    await page.goto(base + "/?demo=1#/overview");
    await page
      .getByRole("button", { name: "Review plan for Crew 1A", exact: true })
      .click();
    const confirm = page
      .getByRole("dialog")
      .getByRole("button", { name: "Confirm crew plan", exact: true });
    await expect(confirm).toBeEnabled();
    await confirm.click();
    await expect(page.getByRole("dialog")).toContainText(
      "Confirmed by @mirrdj",
    );
    await expect(
      page
        .getByRole("dialog")
        .getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toHaveCount(0);
    await page.screenshot({
      path: path.join(artifactRoot, "confirmed-crew-plan.png"),
      animations: "disabled",
      fullPage: true,
    });
    await page.getByRole("button", { name: "Close details" }).click();
    await expect(
      page.getByText("5 need confirmation", { exact: true }),
    ).toBeVisible();
    if (child) {
      await stopServer();
      await startServer();
    }
    await page.reload();
    await expect(
      page.getByText("5 need confirmation", { exact: true }),
    ).toBeVisible();
    await page.goto(base + "/?demo=1#/incidents/gavarres/resources");
    await page
      .getByRole("button", { name: "Review plan for Crew 1A", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText(
      "Confirmed by @mirrdj",
    );
    await page.getByRole("button", { name: "Close details" }).click();
    await page
      .getByRole("button", { name: "Review plan for Engine 12", exact: true })
      .click();
    await page.route("**/api/crew-approvals", (route) =>
      route.request().method() === "POST"
        ? route.fulfill({ status: 409, json: { error: "plan_changed" } })
        : route.continue(),
    );
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Confirm crew plan", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText("The plan changed");
    await expect(page.getByRole("dialog")).not.toContainText(
      "Confirmed by @mirrdj",
    );
    await page.unroute("**/api/crew-approvals");
    const other = await browser.newPage();
    await other.goto(base + "/?demo=1#/overview");
    await expect(
      other.getByText("5 need confirmation", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Confirm crew plan", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText(
      "Confirmed by @mirrdj",
    );
    await expect(
      other.getByText("4 need confirmation", { exact: true }),
    ).toBeVisible({ timeout: 12000 });
    await page.goto(base + "/?demo=1#/log");
    await page.getByLabel("Event type").selectOption("plan_confirmed");
    await expect(page.locator("tbody tr")).toHaveCount(2);
    await expect(page.locator("tbody")).toContainText("@mirrdj");
    const connectedQuery = new URLSearchParams({
      source: "connected",
      incident_id: original.incident_id || original.scenario_id,
      snapshot_id: original.snapshot_id,
      revision: original.revision,
    });
    const connected = await (
      await fetch(base + "/api/crew-approvals?" + connectedQuery)
    ).json();
    assert.deepEqual(connected.events, []);
    assert.deepEqual(await (await fetch(base + "/api/state")).json(), original);
    assert.deepEqual(errors, []);
    assert.equal(writes.length, 3);
    assert.ok(writes.every((url) => url.endsWith("/api/crew-approvals")));
    console.log(
      "Approval browser passed: save, server restart/reload, cross-view and cross-tab state, stale rejection, audit log, source isolation, unchanged coordination state.",
    );
  } finally {
    if (browser) await browser.close();
    await stopServer();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
