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
    const progress = page.getByRole("progressbar");
    const stepCard = page.locator("main .MuiCard-root h2").first();
    const next = page.getByRole("button", { name: "Next", exact: true });
    const back = page.getByRole("button", { name: "Back", exact: true });

    // One section per step, with a single progress bar that advances.
    await expect(stepCard).toHaveText("1. Jurisdiction");
    await expect(progress).toHaveAttribute("aria-valuenow", "20");
    await expect(back).toBeDisabled();
    await fill("Fire department name", "Bombers de Test");
    await fill("Fire station GPS (latitude, longitude)", "41.9600, 3.0380");
    await next.click();

    await expect(stepCard).toHaveText("2. Fires");
    await expect(progress).toHaveAttribute("aria-valuenow", "40");
    // A step that cannot be satisfied reports it and refuses to advance.
    await fill("GPS pairs, one per line", "48.8566, 2.3522");
    await next.click();
    await expect(stepCard).toHaveText("2. Fires");
    await expect(page.getByRole("alert").first()).toContainText(
      "Supply at least one GPS pair",
    );
    await expect(
      page.getByText("Outside Catalonia (approximate bounding box)"),
    ).toBeVisible();
    await fill("GPS pairs, one per line", "41.9400, 3.0400\n41.8600, 2.9100");
    await expect(page.getByText("La Bisbal d'Empordà")).toBeVisible();
    await next.click();

    await expect(stepCard).toHaveText("3. Call list");
    // The call list starts empty and one number is required to leave the step.
    await expect(
      page
        .getByText("Phone numbers, one per line", { exact: true })
        .locator("..")
        .locator("textarea"),
    ).toHaveValue("");
    await next.click();
    await expect(stepCard).toHaveText("3. Call list");
    await expect(page.getByRole("alert").first()).toContainText(
      "Supply at least one phone number",
    );
    await fill(
      "Phone numbers, one per line",
      ["+34600000001", "+34600000002", "+34600000003", "+34600000004"].join(
        "\n",
      ),
    );
    // Back and forward keep what was entered.
    await back.click();
    await expect(stepCard).toHaveText("2. Fires");
    await next.click();
    await expect(stepCard).toHaveText("3. Call list");
    await expect(
      page.getByText("4 numbers accepted", { exact: false }),
    ).toBeVisible();
    await next.click();

    await expect(stepCard).toHaveText("4. Crews");
    // No unit type is assumed: every count starts at zero and at least one is required.
    for (const type of ["Ground crew", "Fire engine", "Helicopter"])
      await expect(page.getByLabel(`${type} count`)).toHaveValue("0");
    await next.click();
    await expect(stepCard).toHaveText("4. Crews");
    await expect(page.getByRole("alert").first()).toContainText(
      "Set a crew count above zero",
    );
    await page.getByLabel("Ground crew count").fill("3");
    await page.getByLabel("Helicopter count").fill("1");
    await page
      .getByLabel("Helicopter starting location")
      .selectOption("territory");
    await next.click();

    await expect(stepCard).toHaveText("5. Start demo");
    await expect(progress).toHaveAttribute("aria-valuenow", "100");
    // The last step starts the demo; it does not preview the dashboard.
    await expect(page.locator("main .incident-map")).toHaveCount(0);
    await expect(
      page.getByText("2 fires, 4 numbers to call and 4 crews", {
        exact: false,
      }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Start demo", exact: true }).click();
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
    await expect(page.locator("main .MuiCard-root h2").first()).toHaveText(
      "1. Jurisdiction",
    );
    await page.getByRole("button", { name: "End demo" }).click();
    await expect(page.getByRole("button", { name: "End demo" })).toHaveCount(0);
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
