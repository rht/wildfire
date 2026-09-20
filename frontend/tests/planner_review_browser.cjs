const { chromium, expect } = require("@playwright/test");
const { spawn } = require("node:child_process");
const path = require("node:path");
const net = require("node:net");
const fs = require("node:fs");
(async () => {
  const socket = net.createServer();
  await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  const frontend = path.resolve(__dirname, "..");
  const server = spawn(
    process.execPath,
    [
      path.join(frontend, "node_modules/vite/bin/vite.js"),
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--strictPort",
    ],
    { cwd: frontend, stdio: "ignore" },
  );
  let browser;
  try {
    const base = `http://127.0.0.1:${port}`;
    await expect
      .poll(
        async () => {
          try {
            return (await fetch(base)).ok;
          } catch {
            return false;
          }
        },
        { timeout: 15000 },
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
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${base}/tests/fixtures/planner-review.html`);
    await expect(
      page.getByText("Complete evacuation planned", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Pickup only · onward transport unresolved", {
        exact: true,
      }),
    ).toBeVisible();
    const valuationReview = page.getByRole("region", {
      name: "Valuation review",
    });
    await expect(
      valuationReview.getByText("Intervention review", { exact: true }),
    ).toBeVisible();
    await expect(valuationReview).not.toContainText("missed planning deadline");
    await expect(valuationReview).toContainText("Unknown value");
    await expect(
      page.getByText("Urgent intervention review", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/A missed planning deadline does not resolve the need/),
    ).toBeVisible();
    await expect(
      page.getByText("Engine 12 · Rescue 4", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Reception North", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Fragile under supplied uncertainty", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Sensitivity unknown", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/People benefit: 16/)).toBeVisible();
    await expect(
      page
        .locator(".crew-stop-fact")
        .filter({ hasText: "Planned arrivals · this crew:" }),
    ).toContainText("16");
    await expect(
      page.getByText(
        /Plan estimates only; actual arrivals require confirmation/,
      ),
    ).toBeVisible();
    await expect(page.getByText(/3\. Delivery/)).toBeVisible();
    await expect(
      page
        .locator(".crew-stop-fact")
        .filter({ hasText: "Planned arrivals · whole mission:" }),
    ).toContainText("24");
    await expect(
      page.locator(".crew-stop-fact").filter({ hasText: "Planned trips:" }),
    ).toContainText("2");
    for (const button of await page
      .getByRole("button", { name: /^Move / })
      .all())
      await expect(button).toBeDisabled();
    await expect(
      page.getByText(/Coordinated review required/).first(),
    ).toBeVisible();
    fs.mkdirSync(path.join(frontend, "artifacts"), { recursive: true });
    await page.screenshot({
      path: path.join(frontend, "artifacts/planner-review-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 768, height: 1024 });
    await expect(
      page.getByRole("region", { name: "Crew visit order and evidence" }),
    ).toBeVisible();
    if (errors.length) throw Error(errors.join("\n"));
    console.log(
      "Planner review browser: urgent needs, planned journeys, joint crews, uncertainty and disabled reorder passed.",
    );
  } finally {
    if (browser) await browser.close();
    server.kill("SIGTERM");
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
