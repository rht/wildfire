const { chromium, expect } = require("@playwright/test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const root = path.resolve(__dirname, "../..");
(async () => {
  let child, browser;
  let base = process.env.DASHBOARD_CREW_ORDER_BASE_URL;
  const artifactRoot = path.join(root, "frontend/artifacts");
  fs.mkdirSync(artifactRoot, { recursive: true });
  const database = path.join(
    fs.mkdtempSync(path.join(artifactRoot, "crew-order-smoke-")),
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
      if (!["GET", "HEAD"].includes(request.method()))
        writes.push({ url: request.url(), body: request.postDataJSON() });
    });
    const originalState = await (await fetch(base + "/api/state")).json();
    const builtDemo = await (
      await fetch(base + "/assets/design-demo.json")
    ).json();
    const gavarres = builtDemo.find((incident) => incident.id === "gavarres");
    assert.ok(gavarres);
    const engine = gavarres.plan.response.teams.find(
      (team) => team.team_id === "gavarres-unit-1",
    );
    const originalOrder = engine.tasks.map((task) => task.action_id);
    const reversed = [...originalOrder].reverse();
    const orderIn = (scope) =>
      scope
        .locator(".crew-stop-table tbody tr")
        .evaluateAll((rows) => rows.map((row) => row.dataset.stopId));
    const review = async (name) => {
      await page
        .getByRole("button", { name: `Review plan for ${name}`, exact: true })
        .click();
      await expect
        .poll(
          async () =>
            (await page
              .getByRole("dialog")
              .getByRole("button", { name: "Move Can Puig up", exact: true })
              .isEnabled()) ||
            (await page
              .getByRole("dialog")
              .getByRole("button", { name: "Move Can Puig down", exact: true })
              .isEnabled()),
        )
        .toBe(true);
      return page.getByRole("dialog");
    };

    await page.goto(base + "/?demo=1#/incidents/gavarres/plan");
    await expect(page.locator("main .crew-route-map")).toHaveCount(1);
    const crewSelector = page.getByRole("combobox", { name: "Crew", exact: true });
    await expect(crewSelector).toHaveValue("gavarres-unit-0");
    await expect(page.getByLabel("Plan map for Crew 1A", { exact: true })).toBeVisible();
    await crewSelector.selectOption(engine.team_id);
    await expect(page.locator("main .crew-route-map")).toHaveCount(1);
    await expect(page.getByLabel("Plan map for Engine 12", { exact: true })).toBeVisible();
    await expect(page.locator("main .crew-stop-table")).toContainText("Can Puig");
    await expect(page.locator("main .crew-stop-table")).not.toContainText("Vall Repòs");
    await page.goto(base + "/?demo=1#/incidents/cap-creus/plan");
    await expect(crewSelector).toHaveValue("cap-creus-unit-0");
    await expect(page.locator("main .crew-route-map")).toHaveCount(1);
    assert.deepEqual(writes, [], "crew selection does not save or dispatch");
    await page.goto(base + "/?demo=1#/overview");
    let dialog = await review("Engine 12");
    const desktopDialog = await dialog.boundingBox();
    assert.deepEqual(
      desktopDialog,
      { x: 0, y: 0, width: 1512, height: 1050 },
      "review fills the workspace",
    );
    const header = dialog.locator(".detail-dialog-header");
    await expect(
      header.getByRole("button", { name: "Close details", exact: true }),
    ).toBeVisible();
    await expect(
      header.getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toBeEnabled();
    const mapPanel = await dialog.locator(".crew-route-review").boundingBox();
    const tablePanel = await dialog.locator(".crew-review-steps").boundingBox();
    assert.ok(
      mapPanel.x + mapPanel.width <= tablePanel.x,
      "map is left of the itinerary table",
    );
    assert.ok(
      Math.abs(mapPanel.y - tablePanel.y) < 2,
      "panels share a top edge",
    );
    assert.ok(
      mapPanel.height > 600 && tablePanel.height > 600,
      `panels fill the available review height: ${JSON.stringify({ mapPanel, tablePanel })}`,
    );
    assert.ok(
      mapPanel.y + mapPanel.height <= 1050 &&
        tablePanel.y + tablePanel.height <= 1050,
      "tall panels remain inside the viewport with internal scrolling",
    );
    const headerBounds = await header.boundingBox();
    const tableScroller = dialog.getByRole("region", {
      name: "Crew visit order and evidence",
    });
    await tableScroller.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    assert.deepEqual(
      await header.boundingBox(),
      headerBounds,
      "confirmation header stays fixed while the table scrolls",
    );
    await tableScroller.evaluate((element) => {
      element.scrollTop = 0;
    });
    assert.deepEqual(await orderIn(dialog), ["__start__", ...originalOrder]);
    await expect(
      dialog.locator(".crew-stop-table tbody tr").first(),
    ).toContainText("Planned starting point");
    await expect(
      dialog.locator(".crew-stop-table tbody tr").first(),
    ).toContainText("Source:");
    const destination = dialog.locator(
      `.crew-stop-table tr[data-stop-id="${originalOrder[1]}"]`,
    );
    const pin = dialog.locator(
      `.crew-stop-pin button[data-stop-id="${originalOrder[1]}"]`,
    );
    await page.mouse.move(0, 0);
    await destination.focus();
    await expect(pin).toHaveAttribute("aria-pressed", "true");
    await expect(dialog.locator(".leaflet-tooltip")).toContainText("Can Puig");
    assert.ok(
      await dialog.locator(".leaflet-tooltip").evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return bounds.width >= 120 && bounds.height <= 150;
      }),
      "destination tooltip stays readable rather than collapsing into a vertical letter column",
    );
    await dialog.locator(".crew-stop-table tbody tr").first().focus();
    await pin.hover();
    await expect(destination).toHaveClass(/is-active/);
    await expect(dialog.locator(".leaflet-tooltip")).toContainText("Can Puig");
    await expect(destination).toBeInViewport();

    await dialog
      .getByRole("button", { name: "Move Can Puig up", exact: true })
      .click();
    assert.deepEqual(await orderIn(dialog), ["__start__", ...reversed]);
    await expect(dialog).toContainText("Draft order");
    await expect(
      dialog.getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toBeDisabled();
    await expect(dialog.locator(".crew-stop-table")).toContainText(
      "Unavailable until validation",
    );
    await expect(dialog.locator(".crew-route-arrow")).toHaveCount(0);
    await dialog
      .getByRole("button", { name: "Validate order", exact: true })
      .click();
    await expect(dialog).toContainText("Validated order");
    await expect(
      dialog.getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toBeEnabled();
    await expect(
      dialog.locator(".crew-stop-table tbody tr").nth(1),
    ).toContainText("Travel: 4 min");
    await expect(
      dialog.locator(".crew-stop-table tbody tr").nth(2),
    ).toContainText("Travel: 4 min");
    await expect(dialog.locator(".crew-stop-table")).not.toContainText(
      "Unavailable until validation",
    );
    await page.route("**/api/crew-approvals", (route) =>
      route.request().method() === "POST"
        ? route.fulfill({ status: 409, json: { error: "review_changed" } })
        : route.continue(),
    );
    await dialog
      .getByRole("button", { name: "Confirm crew plan", exact: true })
      .click();
    await expect(dialog).not.toContainText("Validated order");
    await expect(
      dialog.getByRole("button", { name: "Validate order", exact: true }),
    ).toBeEnabled();
    await expect(
      dialog.getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toBeDisabled();
    assert.deepEqual(await orderIn(dialog), ["__start__", ...reversed]);
    await page.unroute("**/api/crew-approvals");
    await dialog
      .getByRole("button", { name: "Validate order", exact: true })
      .click();
    await expect(dialog).toContainText("Validated order");
    const other = await browser.newPage();
    other.on("pageerror", (error) => errors.push(error.message));
    await other.goto(base + "/?demo=1#/overview");
    await other
      .getByRole("button", { name: "Review plan for Engine 12", exact: true })
      .click();
    const otherDialog = other.getByRole("dialog");
    await expect(
      otherDialog.getByRole("button", {
        name: "Confirm crew plan",
        exact: true,
      }),
    ).toBeEnabled();
    const [otherSaved] = await Promise.all([
      other.waitForResponse(
        (response) =>
          response.url().endsWith("/api/crew-approvals") &&
          response.request().method() === "POST",
      ),
      otherDialog
        .getByRole("button", { name: "Confirm crew plan", exact: true })
        .click(),
    ]);
    assert.equal(otherSaved.status(), 200);
    assert.ok(
      (await otherSaved.json()).plans.find(
        (plan) => plan.team_id === engine.team_id,
      ).approval,
    );
    await expect(otherDialog).toContainText("Confirmed by @mirrdj");
    await expect(dialog).not.toContainText("Validated order", {
      timeout: 12000,
    });
    await expect(
      dialog.getByRole("button", { name: "Validate order", exact: true }),
    ).toBeEnabled();
    await expect(
      dialog.getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toBeDisabled();
    assert.deepEqual(await orderIn(dialog), ["__start__", ...reversed]);
    await other.close();
    await dialog
      .getByRole("button", { name: "Validate order", exact: true })
      .click();
    await expect(dialog).toContainText("Validated order");
    await dialog
      .getByRole("button", { name: "Confirm crew plan", exact: true })
      .click();
    await expect(dialog).toContainText("Confirmed by @mirrdj");
    assert.deepEqual(await orderIn(dialog), ["__start__", ...reversed]);
    await page.screenshot({
      path: path.join(artifactRoot, "crew-order-confirmed.png"),
      fullPage: true,
      animations: "disabled",
    });
    await dialog.getByRole("button", { name: "Close details" }).click();

    if (child) {
      await stopServer();
      await startServer();
    }
    await page.reload();
    dialog = await review("Engine 12");
    await expect(dialog).toContainText("Confirmed by @mirrdj");
    assert.deepEqual(await orderIn(dialog), ["__start__", ...reversed]);
    await dialog.getByRole("button", { name: "Close details" }).click();
    await page.goto(base + "/?demo=1#/incidents/gavarres/plan");
    await page.getByRole("combobox", { name: "Crew", exact: true }).selectOption(engine.team_id);
    const engineCard = page.locator(".plan-teams > .MuiCard-root").filter({
      has: page.getByRole("heading", { name: "Engine 12", exact: true }),
    });
    await expect(engineCard).toContainText("Analyst-approved visit order");
    assert.deepEqual(await orderIn(engineCard), ["__start__", ...reversed]);
    await expect(
      engineCard.getByRole("button", { name: /Move Can Puig/ }),
    ).toHaveCount(0);

    await page.goto(base + "/?demo=1#/overview");
    await page
      .getByRole("button", { name: "Review plan for Crew 1A", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    const crew = gavarres.plan.response.teams.find(
      (team) => team.team_id === "gavarres-unit-0",
    );
    const secondName = gavarres.assets.find(
      (asset) => asset.asset_id === crew.tasks[1].asset_id,
    ).name;
    await dialog
      .getByRole("button", { name: `Move ${secondName} up`, exact: true })
      .click();
    await dialog
      .getByRole("button", { name: "Validate order", exact: true })
      .click();
    await expect(dialog).toContainText("Order cannot be approved");
    await expect(dialog).toContainText(/deadline/i);
    await expect(
      dialog.getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toBeDisabled();
    await dialog
      .getByRole("button", { name: "Restore supplied order", exact: true })
      .click();
    assert.deepEqual(await orderIn(dialog), [
      "__start__",
      ...crew.tasks.map((task) => task.action_id),
    ]);
    await expect(
      dialog.getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toBeEnabled();
    await dialog.getByRole("button", { name: "Close details" }).click();

    const mobile = await browser.newPage({
      viewport: { width: 768, height: 1024 },
      isMobile: true,
      hasTouch: true,
    });
    mobile.on("pageerror", (error) => errors.push(error.message));
    await mobile.goto(base + "/?demo=1#/incidents/gavarres/plan");
    await mobile.getByRole("combobox", { name: "Crew", exact: true }).selectOption(engine.team_id);
    await expect(mobile.locator("main .crew-route-map")).toHaveCount(1);
    await expect(
      mobile.getByRole("heading", { name: "Firefighter plan", exact: true }),
    ).toBeVisible();
    assert.ok(
      await mobile.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "page stays inside mobile viewport",
    );
    await mobile
      .getByRole("button", { name: "Review plan for Engine 12", exact: true })
      .tap();
    const mobileDialog = mobile.getByRole("dialog");
    assert.deepEqual(
      await mobileDialog.boundingBox(),
      { x: 0, y: 0, width: 768, height: 1024 },
      "touch review uses the whole viewport",
    );
    await expect(
      mobileDialog
        .locator(".detail-dialog-header")
        .getByRole("button", { name: "Close details", exact: true }),
    ).toBeVisible();
    await expect(mobileDialog).toContainText("Confirmed by @mirrdj");
    await mobileDialog
      .locator(`.crew-stop-pin button[data-stop-id="${reversed[0]}"]`)
      .tap();
    await expect(mobileDialog.locator(".leaflet-tooltip")).toContainText(
      "Can Puig",
    );
    assert.ok(
      await mobileDialog.locator(".leaflet-tooltip").evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return bounds.width > 100 && bounds.height < 160;
      }),
      "mobile tooltip keeps a readable width and bounded height",
    );
    assert.deepEqual(await orderIn(mobileDialog), ["__start__", ...reversed]);
    const scroller = mobileDialog.getByRole("region", {
      name: "Crew visit order and evidence",
    });
    assert.ok(
      await scroller.evaluate(
        (element) => element.scrollWidth > element.clientWidth,
      ),
      "wide table has its own horizontal scroll",
    );
    assert.ok(
      await mobileDialog.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return bounds.left >= 0 && bounds.right <= innerWidth;
      }),
      "dialog stays inside mobile viewport",
    );
    await scroller.evaluate((element) => {
      element.scrollLeft = element.scrollWidth;
      element.scrollTop = element.scrollHeight;
    });
    await expect(
      mobileDialog.getByRole("button", {
        name: "Move Can Puig down",
        exact: true,
      }),
    ).toBeVisible();
    await mobile.screenshot({
      path: path.join(artifactRoot, "crew-order-mobile.png"),
      fullPage: true,
      animations: "disabled",
    });
    await mobile.close();

    const query = new URLSearchParams({
      source: "design_demo",
      incident_id: gavarres.id,
      snapshot_id: gavarres.snapshot_id,
      revision: gavarres.revision,
    });
    const saved = await (
      await fetch(base + "/api/crew-approvals?" + query)
    ).json();
    const savedEngine = saved.plans.find(
      (plan) => plan.team_id === engine.team_id,
    );
    assert.deepEqual(savedEngine.reviewed_plan.action_ids, reversed);
    assert.ok(
      savedEngine.reviewed_plan.tasks.every(
        (task) => task.status === "proposed",
      ),
      "approval never starts or dispatches work",
    );
    assert.equal(
      saved.plans.find((plan) => plan.team_id === crew.team_id).approval,
      null,
    );
    assert.deepEqual(
      await (await fetch(base + "/api/state")).json(),
      originalState,
    );
    assert.deepEqual(errors, []);
    assert.equal(
      writes.filter((write) => write.url.endsWith("/api/crew-plan-preview"))
        .length,
      4,
    );
    assert.equal(
      writes.filter((write) => write.url.endsWith("/api/crew-approvals"))
        .length,
      2,
    );
    assert.ok(
      writes.every((write) =>
        /\/api\/(crew-plan-preview|crew-approvals)$/.test(write.url),
      ),
      "review never writes dispatch endpoints",
    );
    console.log(
      "Crew-order browser passed: starting row, map/table link, validated order, infeasible order, saved reload/restart/page order, mobile containment and no dispatch writes.",
    );
  } finally {
    if (browser) await browser.close();
    await stopServer();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
