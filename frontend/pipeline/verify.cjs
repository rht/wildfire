// Focused pitch-slide verification; no operational services are connected.
const { chromium, expect } = require("@playwright/test");
const fs = require("node:fs/promises");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const base = process.env.PIPELINE_BASE_URL || "http://127.0.0.1:18544";
const exportsDir = path.join(__dirname, "public/exports");
const artifacts = path.resolve(__dirname, "../artifacts/pipeline");
(async () => {
  await fs.mkdir(exportsDir, { recursive: true });
  await fs.mkdir(artifacts, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1600, height: 1000 },
      deviceScaleFactor: 1,
    });
    const errors = [],
      requests = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => requests.push(request.url()));
    await page.goto(base);
    await expect(
      page.getByRole("img", {
        name: "From fire alert to coordinated response",
        exact: true,
      }),
    ).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const text = await page.locator(".pitch-slide text").allTextContents();
    expect(text.join(" ").trim().split(/\s+/).length).toBeLessThanOrEqual(130);
    for (const title of [
      "Assess risk & value",
      "Prioritise action",
      "Analyst’s plan",
      "Calls + changing fire conditions update the plan",
    ])
      expect(text).toContain(title);
    expect(text.join(" ").trim().split(/\s+/).length).toBeGreaterThanOrEqual(
      100,
    );
    // Check actual rendered bounds against each card and the full slide.
    expect(
      await page.locator(".pitch-slide text").evaluateAll((elements) =>
        elements
          .filter((el) => {
            const card = el.closest("[data-card]");
            const bounds = (
              card
                ? card.querySelector(".card-frame")
                : document.querySelector(".pitch-slide")
            ).getBoundingClientRect();
            const box = el.getBoundingClientRect();
            return (
              box.left < bounds.left ||
              box.right > bounds.right ||
              box.top < bounds.top ||
              box.bottom > bounds.bottom
            );
          })
          .map((el) => el.textContent),
      ),
    ).toEqual([]);
    for (const width of [1600, 1280, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      const box = await page.locator(".pitch-slide").boundingBox();
      expect(box.width / box.height).toBeCloseTo(16 / 9, 2);
      await expect(
        page.getByRole("link", { name: "PNG image", exact: true }),
      ).toBeVisible();
      await page.screenshot({
        path: path.join(artifacts, `pitch-${width}.png`),
        fullPage: true,
      });
    }
    // Print directly from mobile too: no preparatory media or layout overrides.
    await page.pdf({
      path: path.join(artifacts, "pitch-mobile-print.pdf"),
      preferCSSPageSize: true,
      printBackground: true,
    });
    expect(
      execFileSync(
        "pdfinfo",
        [path.join(artifacts, "pitch-mobile-print.pdf")],
        { encoding: "utf8" },
      ),
    ).toMatch(/Pages:\s+1\b/);
    await page.setViewportSize({ width: 1600, height: 1000 });
    await page
      .locator(".pitch-slide")
      .screenshot({ path: path.join(exportsDir, "responsara-pipeline.png") });
    const png = await fs.readFile(
      path.join(exportsDir, "responsara-pipeline.png"),
    );
    expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1600, 900]);
    await page.pdf({
      path: path.join(exportsDir, "responsara-pipeline.pdf"),
      preferCSSPageSize: true,
      printBackground: true,
      tagged: true,
    });
    const info = execFileSync(
      "pdfinfo",
      [path.join(exportsDir, "responsara-pipeline.pdf")],
      { encoding: "utf8" },
    );
    expect(info).toMatch(/Pages:\s+1\b/);
    const dimensions = info.match(/Page size:\s+([\d.]+) x ([\d.]+) pts/);
    expect(Number(dimensions[1])).toBeCloseTo(1200, 0);
    expect(Number(dimensions[2])).toBeCloseTo(675, 0);
    const pdfText = execFileSync(
      "pdftotext",
      [path.join(exportsDir, "responsara-pipeline.pdf"), "-"],
      { encoding: "utf8" },
    );
    for (const title of [
      "Assess risk & value",
      "Prioritise action",
      "Analyst’s plan",
      "Calls + changing fire conditions update the plan",
    ])
      expect(pdfText).toContain(title);
    expect(pdfText).not.toMatch(/PNG image|Vector PDF|schema|WebSocket|PR #/);
    for (const name of ["PNG image", "Vector PDF"]) {
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        page.getByRole("link", { name, exact: true }).click(),
      ]);
      expect(await download.failure()).toBeNull();
      expect(
        (await fs.readFile(await download.path())).equals(
          await fs.readFile(
            path.join(exportsDir, download.suggestedFilename()),
          ),
        ),
      ).toBe(true);
    }
    expect(errors).toEqual([]);
    expect(
      requests.filter((url) => /\/api\/|slng\.ai|vonage/.test(url)),
    ).toEqual([]);
    console.log(
      `PASS: ${text.join(" ").trim().split(/\s+/).length} words; 5 viewport widths; unclipped SVG text; 1600×900 PNG; one-page 16:9 vector PDF; native mobile print; both downloads; no browser errors or operational requests.`,
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
