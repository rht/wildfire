// The onboarding page needs no backend: it must work while the API gate is showing.
const { chromium, expect } = require("@playwright/test");
const { spawn } = require("node:child_process");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
(async () => {
  assert.ok(
    fs.existsSync(path.join(root, "dist/index.html")),
    "run `npm run build` before the onboarding browser test",
  );
  const socket = net.createServer();
  await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const server = spawn(
    process.execPath,
    [
      path.join(root, "node_modules/vite/bin/vite.js"),
      "preview",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--strictPort",
    ],
    { cwd: root, stdio: ["ignore", "ignore", "inherit"] },
  );
  let browser;
  try {
    await expect
      .poll(
        async () => {
          if (server.exitCode !== null)
            throw new Error("preview server exited");
          try {
            return (await fetch(base + "/")).ok;
          } catch {
            return false;
          }
        },
        { timeout: 20000 },
      )
      .toBe(true);
    browser = await chromium.launch({
      headless: true,
      executablePath:
        process.env.DASHBOARD_BROWSER_EXECUTABLE ||
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    });
    const page = await browser.newPage({
      viewport: { width: 1512, height: 1050 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error" && !m.text().includes("Failed to load resource"))
        errors.push(m.text());
    });

    // Reachable by address only, and never offered as a navigation item.
    await page.goto(base + "/#/onboarding");
    await page.getByRole("heading", { name: "Demo onboarding" }).waitFor();
    const navigation = page.locator('nav[aria-label="Main navigation"] a');
    await expect(navigation).toHaveCount(5);
    await expect(navigation.filter({ hasText: /onboarding/i })).toHaveCount(0);

    const fill = async (label, value) => {
      const field = page.getByText(label, { exact: true }).locator("..");
      await field.locator("input, textarea").first().fill(value);
    };
    await fill("Fire department name", "Bombers de Test");
    await fill("Fire station GPS (latitude, longitude)", "41.9600, 3.0380");
    await fill("GPS pairs, one per line", "41.9400, 3.0400\n41.8600, 2.9100");
    await fill(
      "Phone numbers, one per line",
      ["+34600000001", "+34600000002", "+34600000003", "+34600000004"].join(
        "\n",
      ),
    );
    await page.getByLabel("Ground crew count").fill("3");
    await page.getByLabel("Fire engine count").fill("0");
    await page.getByLabel("Evacuation bus count").fill("0");
    await page.getByLabel("Helicopter count").fill("1");
    await page
      .getByLabel("Helicopter starting location")
      .selectOption("territory");
    await expect(page.getByText("Ready to build")).toBeVisible();
    await expect(page.getByText("La Bisbal d'Empordà").first()).toBeVisible();

    await page.getByRole("button", { name: "Build dashboard" }).click();
    await page
      .getByRole("heading", { name: "Operations overview", exact: true })
      .waitFor();
    await expect(page.getByText("Session demo", { exact: true })).toBeVisible();
    assert.ok(
      await page
        .locator('[data-testid="metric-active"]')
        .innerText()
        .then((text) => text.includes("2")),
      "two supplied pairs make two incidents",
    );

    await page.goto(base + "/#/resources");
    await page.getByRole("heading", { name: "Resources" }).waitFor();
    await expect(page.getByText("Bombers de Test").first()).toBeVisible();
    await expect(page.locator("main tbody tr")).toHaveCount(4);

    await page.goto(base + "/#/incidents");
    await expect(page.locator("main tbody tr")).toHaveCount(2);
    await page.locator("main tbody tr a").first().click();
    await page.getByRole("link", { name: "Calls & follow-up" }).click();
    await page.getByRole("button", { name: /Voice assistant to call/ }).click();
    await expect(
      page.getByText("+34600000", { exact: false }).first(),
    ).toBeVisible();

    // The configuration survives a reload and ends when it is cleared, after which
    // the dashboard is back on whatever source it is configured with.
    await page.reload();
    await expect(page.getByText("Session demo", { exact: true })).toBeVisible();
    await page.goto(base + "/#/onboarding");
    await page.getByRole("button", { name: "Clear session data" }).click();
    await expect(
      page.getByRole("button", { name: "Clear session data" }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "Back to dashboard" }).click();
    await expect(page.getByText("Session demo", { exact: true })).toHaveCount(
      0,
    );
    await expect(page.locator('select[aria-label="Data source"]')).toHaveValue(
      "connected",
    );
    await expect(
      page.locator('select[aria-label="Data source"] option'),
    ).toHaveCount(2);
    assert.equal(
      await page.evaluate(() =>
        sessionStorage.getItem("responsara.onboarding.v1"),
      ),
      null,
    );

    assert.deepEqual(errors, []);
    console.log("onboarding browser checks passed");
  } finally {
    await browser?.close();
    server.kill();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
