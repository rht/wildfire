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
    page.on("request", (r) => {
      if (!["GET", "HEAD"].includes(r.method())) writes.push(r.method());
    });
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
    await page
      .getByRole("link", { name: "Gavarres forest fire", exact: true })
      .first()
      .click();
    await page
      .getByRole("link", { name: "Calls & follow-up", exact: true })
      .click();
    await page.getByRole("button", { name: /Human follow-up/ }).click();
    await page
      .getByRole("button", {
        name: "View call details for Vall Repòs care home",
      })
      .click();
    await page.getByRole("dialog").waitFor();
    assert.match(await page.getByRole("dialog").innerText(), /Human requested/);
    await page.getByRole("button", { name: "Close details" }).click();
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
    await page.screenshot({
      path: "artifacts/response-plan.png",
      fullPage: true,
    });
    await page.goto(base + "/?demo=1#/buildings");
    await page.getByLabel("Search buildings").fill("Vall Repòs");
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await page
      .getByRole("button", { name: "View building Vall Repòs care home" })
      .click();
    assert.match(await page.getByRole("dialog").innerText(), /3,200,000/);
    await page.getByRole("button", { name: "Close details" }).click();
    await page.getByLabel("Search buildings").fill("");
    await page.getByLabel("Assessment from").fill("2026-09-20");
    await expect(page.locator("tbody tr")).toHaveCount(9);
    await page.screenshot({ path: "artifacts/buildings.png", fullPage: true });
    await page.goto(base + "/?demo=1#/log");
    await page
      .getByRole("heading", { name: "Activity log", exact: true })
      .waitFor();
    const newest = await page.locator("tbody tr").first().innerText();
    await page.getByLabel("Event order").selectOption("oldest");
    assert.notEqual(await page.locator("tbody tr").first().innerText(), newest);
    await page.screenshot({
      path: "artifacts/activity-log.png",
      fullPage: true,
    });
    await page.goto(base + "/?demo=1#/overview");
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
