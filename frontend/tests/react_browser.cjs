const { chromium, expect } = require("@playwright/test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.DASHBOARD_BROWSER_EXECUTABLE ||
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  });
  const base = process.env.DASHBOARD_BASE_URL || "http://127.0.0.1:18522";
  fs.mkdirSync("artifacts", { recursive: true });
  try {
    const page = await browser.newPage({
        viewport: { width: 1512, height: 1050 },
      }),
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
    await page.mouse.move(0, 0);
    await page.goto(base + "/?demo=1#/overview");
    await page
      .getByRole("heading", { name: "Operations overview", exact: true })
      .waitFor();
    assert.equal(
      await page
        .locator('[data-testid="metric-active"]')
        .innerText()
        .then((t) => t.includes("3")),
      true,
    );
    await expect(page.locator("main table")).toHaveCount(0);
    await expect(
      page.getByText("Awaiting analyst confirmation", { exact: true }),
    ).toHaveCount(6);
    await expect(
      page.getByText("6 need confirmation", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("navigation", {
        name: "Incident navigation",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", {
        name: "Review plan for Crew 1A",
        exact: true,
      }),
    ).toContainText("5 min margin");
    await expect(
      page.getByRole("button", { name: /^Review plan for/ }),
    ).toHaveCount(7);
    await page
      .getByRole("button", { name: "Review plan for Crew 1A", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText(
      "Accessible transport confirmed",
    );
    await expect(
      page
        .getByRole("dialog")
        .getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "Close details" }).click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    await page
      .getByRole("button", {
        name: "Open incident Gavarres forest fire",
        exact: true,
      })
      .hover();
    await expect(page).toHaveURL(/overview/);
    await page
      .getByRole("button", {
        name: "Open incident Gavarres forest fire",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(/incidents\/gavarres\/summary/);
    await expect(page.getByTestId("metric-active")).toHaveCount(0);
    await expect(page.getByTestId("metric-structures")).toContainText("4");
    await page.mouse.move(0, 0);
    await page.goto(base + "/?demo=1#/overview");
    await page.getByTestId("metric-active").getByRole("link").click();
    await expect(
      page.getByRole("heading", { name: "Incidents", exact: true }),
    ).toBeVisible();
    await expect(page.locator("tbody tr")).toHaveCount(3);
    await page
      .getByRole("link", { name: "Gavarres forest fire", exact: true })
      .click();
    await expect(page.getByTestId("metric-deployed")).toContainText("3");
    await expect(page.getByTestId("metric-structures")).toContainText("4");
    await expect(page.getByTestId("metric-people")).toContainText("4");
    await page.getByTestId("metric-deployed").getByRole("link").click();
    await expect(page.locator("tbody tr")).toHaveCount(3);
    await expect(
      page.getByRole("columnheader", { name: "Plan", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Review plan for Crew 1A", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText(
      "Accessible transport confirmed",
    );
    await page.getByRole("button", { name: "Close details" }).click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    await page
      .getByRole("link", { name: "Calls & follow-up", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Call history", exact: true }),
    ).toBeVisible();
    await page.getByLabel("Caller", { exact: true }).selectOption("human");
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await expect(page.locator("tbody tr")).toContainText("Escola de la Bisbal");
    await page.getByLabel("Caller", { exact: true }).selectOption("agent");
    await page.getByLabel("Call outcome").selectOption("no_answer");
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await page.getByRole("button", { name: "Reset filters" }).click();
    await page.getByRole("button", { name: /Voice assistant to call/ }).click();
    await expect(page.locator("tbody tr")).toHaveCount(2);
    await page.getByRole("button", { name: /Human follow-up/ }).click();
    await page
      .getByRole("button", {
        name: "View call details for Vall Repòs care home",
      })
      .click();
    await page.getByRole("dialog").waitFor();
    assert.match(await page.getByRole("dialog").innerText(), /Human requested/);
    await expect(page.getByRole("dialog")).toContainText("41.95300, 3.02200");
    await page.getByRole("button", { name: "Close details" }).click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    await page
      .getByRole("link", { name: "Firefighter plan", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Firefighter plan", exact: true })
      .waitFor();
    assert.match(
      await page.locator("main").innerText(),
      /Accessible transport confirmed/,
    );
    await page
      .getByRole("button", { name: "Review plan for Crew 1A", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText(
      "Fire analyst confirmation required",
    );
    await expect(page.getByRole("dialog")).toContainText(
      "Confirmation cannot be saved yet",
    );
    await page.getByRole("button", { name: "Close details" }).click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    await page.waitForFunction(() => {
      const tiles = [...document.querySelectorAll(".leaflet-tile")];
      return (
        tiles.length > 0 && tiles.every((t) => t.complete && t.naturalWidth > 0)
      );
    });
    await page.screenshot({
      path: "artifacts/response-plan.png",
      fullPage: true,
    });
    await page.goto(base + "/?demo=1#/buildings");
    await expect(page.locator(".risk-status .MuiChip-root")).toHaveCount(12);
    const badgeWidths = await page
      .locator(".risk-status .MuiChip-root")
      .evaluateAll((nodes) =>
        nodes.map((n) => n.getBoundingClientRect().width),
      );
    assert.equal(new Set(badgeWidths).size, 1);
    await page.getByLabel("Search buildings").fill("Vall Repòs");
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await page
      .getByRole("button", { name: "View building Vall Repòs care home" })
      .click();
    assert.match(await page.getByRole("dialog").innerText(), /3,200,000/);
    await page.getByRole("button", { name: "Close details" }).click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    await page.getByLabel("Search buildings").fill("");
    await page.getByLabel("Assessment from").fill("2026-09-20");
    await expect(page.locator("tbody tr")).toHaveCount(9);
    await page.screenshot({ path: "artifacts/buildings.png", fullPage: true });
    await page.goto(base + "/?demo=1#/log");
    await page
      .getByRole("heading", { name: "Activity log", exact: true })
      .waitFor();
    const logTagWidths = await page
      .locator("tbody .status-tag")
      .evaluateAll((nodes) =>
        nodes.map((n) => n.getBoundingClientRect().width),
      );
    assert.equal(new Set(logTagWidths).size, 1);
    const newest = await page.locator("tbody tr").first().innerText();
    await page.getByLabel("Event order").selectOption("oldest");
    assert.notEqual(await page.locator("tbody tr").first().innerText(), newest);
    await page.screenshot({
      path: "artifacts/activity-log.png",
      fullPage: true,
    });
    await page.mouse.move(0, 0);
    await page.goto(base + "/?demo=1#/overview");
    await page.waitForFunction(() => {
      const tiles = [...document.querySelectorAll(".leaflet-tile")];
      return (
        tiles.length > 0 && tiles.every((t) => t.complete && t.naturalWidth > 0)
      );
    });
    await page.screenshot({ path: "artifacts/overview.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
    );
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page
      .getByRole("link", { name: "Buildings & risk", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Buildings & risk", exact: true })
      .waitFor();
    await page.screenshot({ path: "artifacts/mobile.png", fullPage: true });
    await page.setViewportSize({ width: 1512, height: 1050 });
    await page.goto(base + "/#/overview");
    await page.getByText("Connected", { exact: true }).waitFor();
    await expect(page.getByTestId("metric-active")).toContainText("1");
    assert.match(
      await page.locator('[data-testid="metric-deployed"]').innerText(),
      /—/,
    );
    assert.equal(
      await page.getByText("Gavarres forest fire", { exact: true }).count(),
      0,
    );
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, []);
    console.log(
      "React browser smoke passed: routes, call reasons, response steps, valuation/date filters, log order, mobile, live/demo separation; no writes or page errors.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
