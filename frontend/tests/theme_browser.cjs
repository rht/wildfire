const { chromium, expect } = require("@playwright/test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const base = process.env.DASHBOARD_BASE_URL || "http://127.0.0.1:18522";
const artifacts = path.resolve(__dirname, "../artifacts");
const luminance = (rgb) =>
  rgb
    .map((value) => value / 255)
    .map((value) =>
      value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
    )
    .reduce(
      (sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index],
      0,
    );
const contrast = ({ foreground, background }) => {
  const values = [luminance(foreground), luminance(background)].sort(
    (a, b) => b - a,
  );
  return (values[0] + 0.05) / (values[1] + 0.05);
};
async function colors(locator) {
  return locator.evaluate((element) => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const rgba = (color) => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      return [...context.getImageData(0, 0, 1, 1).data];
    };
    const blend = (over, under) =>
      under.map(
        (channel, index) =>
          channel * (1 - over[3] / 255) + (over[index] * over[3]) / 255,
      );
    const ancestors = [];
    for (let node = element; node; node = node.parentElement)
      ancestors.push(node);
    const background = ancestors
      .reverse()
      .reduce(
        (under, node) =>
          blend(rgba(getComputedStyle(node).backgroundColor), under),
        [255, 255, 255],
      );
    return {
      background,
      foreground: blend(rgba(getComputedStyle(element).color), background),
    };
  });
}
async function readableSurface(locator, mode) {
  const brightness = expect.poll(async () =>
    luminance((await colors(locator)).background),
  );
  if (mode === "dark") await brightness.toBeLessThan(0.3);
  else await brightness.toBeGreaterThan(0.75);
  await expect
    .poll(async () => contrast(await colors(locator)))
    .toBeGreaterThanOrEqual(4.5);
}
async function toggle(scope, page, target) {
  const button = scope.getByRole("button", {
    name: `Switch to ${target} theme`,
    exact: true,
  });
  await button.focus();
  await button.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-theme", target);
  await expect(
    scope.getByRole("button", {
      name: `Switch to ${target === "dark" ? "light" : "dark"} theme`,
      exact: true,
    }),
  ).toBeVisible();
}
async function unfilteredMap(scope) {
  const map = scope.locator(".crew-route-map").first();
  await expect(map.locator(".crew-stop-pin button").first()).toBeVisible();
  assert.deepEqual(
    await map.evaluate((element) => {
      const inspected = new Set([
        element,
        ...element.querySelectorAll(".leaflet-tile, .leaflet-tile-pane"),
      ]);
      for (
        let ancestor = element.parentElement;
        ancestor;
        ancestor = ancestor.parentElement
      )
        inspected.add(ancestor);
      return [...inspected]
        .filter((node) => getComputedStyle(node).filter !== "none")
        .map((node) => ({
          className: node.className,
          filter: getComputedStyle(node).filter,
          blend: getComputedStyle(node).mixBlendMode,
        }));
    }),
    [],
    "theme does not invert or filter geographic imagery",
  );
}

(async () => {
  fs.mkdirSync(artifacts, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.DASHBOARD_BROWSER_EXECUTABLE ||
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  });
  const errors = [],
    writes = [];
  const watch = (page) => {
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (!["GET", "HEAD"].includes(request.method()))
        writes.push(`${request.method()} ${request.url()}`);
    });
  };
  try {
    const page = await browser.newPage({
      viewport: { width: 1512, height: 1050 },
      colorScheme: "dark",
    });
    watch(page);
    await page.goto(base + "/?demo=1#/overview");
    await expect(
      page.getByRole("button", { name: "Switch to dark theme", exact: true }),
    ).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await readableSurface(page.locator("body"), "light");
    const initialLight = (await colors(page.locator("body"))).background;
    await toggle(page, page, "dark");
    await readableSurface(page.locator("body"), "dark");
    assert.equal(
      await page.evaluate(() => localStorage.getItem("responsara-theme")),
      "dark",
    );
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(
      page.getByRole("button", { name: "Switch to light theme", exact: true }),
    ).toBeVisible();
    await readableSurface(page.locator("body"), "dark");

    await page.goto(base + "/?demo=1#/incidents/gavarres/plan");
    let dialog = page.getByRole("dialog");
    await readableSurface(dialog, "dark");
    await readableSurface(
      dialog.locator(".crew-stop-table thead th").first(),
      "dark",
    );
    await readableSurface(
      dialog.locator(".crew-stop-fact > span").first(),
      "dark",
    );
    await unfilteredMap(dialog);
    await dialog.getByRole("button", { name: "3D tilt", exact: true }).click();
    await expect(dialog.locator(".map-camera")).toHaveClass(/is-tilted/);
    const markerPositions = () =>
      dialog
        .locator(".crew-stop-pin")
        .evaluateAll((markers) =>
          markers.map((marker) => marker.style.transform).join(";"),
        );
    const beforeZoom = await markerPositions();
    await dialog.getByRole("button", { name: "Zoom in", exact: true }).click();
    await expect.poll(markerPositions).not.toBe(beforeZoom);
    const zoomed = await markerPositions();
    await toggle(dialog.locator(".detail-dialog-header"), page, "light");
    await readableSurface(dialog, "light");
    assert.deepEqual(
      (await colors(page.locator("body"))).background,
      initialLight,
      "light appearance returns unchanged",
    );
    await expect(dialog.locator(".map-camera")).toHaveClass(/is-tilted/);
    assert.equal(
      await markerPositions(),
      zoomed,
      "theme change preserves map zoom and position",
    );
    await dialog.getByRole("button", { name: "2D", exact: true }).click();
    await toggle(dialog.locator(".detail-dialog-header"), page, "dark");
    await page.screenshot({
      path: path.join(artifacts, "theme-dark-incident-plan.png"),
      animations: "disabled",
    });

    await dialog
      .getByRole("button", { name: "Review plan for Crew 1A", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await expect(dialog.locator(".detail-dialog-title")).toContainText(
      "Crew 1A",
    );
    await readableSurface(dialog, "dark");
    const lastMoveUp = dialog
      .getByRole("button", { name: /^Move .* up$/ })
      .last();
    await expect(lastMoveUp).toBeEnabled();
    await lastMoveUp.click();
    await expect(dialog).toContainText("Draft order");
    const draftOrder = await dialog
      .locator(".crew-stop-table tbody tr")
      .evaluateAll((rows) => rows.map((row) => row.dataset.stopId));
    await toggle(dialog.locator(".detail-dialog-header"), page, "light");
    assert.deepEqual(
      await dialog
        .locator(".crew-stop-table tbody tr")
        .evaluateAll((rows) => rows.map((row) => row.dataset.stopId)),
      draftOrder,
      "theme switch preserves the local reviewed draft",
    );
    await expect(dialog).toContainText("Draft order");
    await expect(
      dialog.getByRole("button", { name: "Confirm crew plan", exact: true }),
    ).toBeDisabled();
    await unfilteredMap(dialog);
    await toggle(dialog.locator(".detail-dialog-header"), page, "dark");
    await readableSurface(
      dialog.locator(".crew-stop-table thead th").first(),
      "dark",
    );
    await dialog
      .getByRole("button", { name: "Restore supplied order", exact: true })
      .click();
    await dialog
      .getByRole("button", { name: "Close details", exact: true })
      .click();
    await expect(
      page.getByRole("dialog").locator(".detail-dialog-title").first(),
    ).toContainText("Firefighter plan");

    for (const viewport of [
      { width: 768, height: 1024 },
      { width: 1024, height: 768 },
    ]) {
      await page.setViewportSize(viewport);
      dialog = page.getByRole("dialog");
      assert.deepEqual(await dialog.boundingBox(), { x: 0, y: 0, ...viewport });
      const header = dialog.locator(".detail-dialog-header").first();
      await expect(
        header.getByRole("button", {
          name: "Switch to light theme",
          exact: true,
        }),
      ).toBeVisible();
      const themeBounds = await header
        .getByRole("button", { name: "Switch to light theme", exact: true })
        .boundingBox();
      assert.ok(
        themeBounds.x >= 0 &&
          themeBounds.x + themeBounds.width <= viewport.width &&
          themeBounds.y + themeBounds.height <= viewport.height,
        "theme control remains inside tablet header",
      );
      const layout = await dialog
        .locator(".MuiDialogContent-root, .incident-crew-plan, .plan-teams")
        .evaluateAll((elements) =>
          elements.map((element) => ({
            width: element.clientWidth,
            height: element.clientHeight,
            scrollWidth: element.scrollWidth,
            scrollHeight: element.scrollHeight,
            mayScroll: element.matches(".plan-teams"),
            bounds: element.getBoundingClientRect().toJSON(),
          })),
        );
      assert.ok(
        layout.every(
          (element) =>
            element.bounds.right <= viewport.width + 1 &&
            element.bounds.bottom <= viewport.height + 1 &&
            (element.mayScroll ||
              (element.scrollWidth <= element.width + 1 &&
                element.scrollHeight <= element.height + 1)),
        ),
        `theme preserves tablet containment: ${JSON.stringify(layout)}`,
      );
      await toggle(header, page, "light");
      await toggle(header, page, "dark");
      await unfilteredMap(dialog);
      await page.screenshot({
        path: path.join(artifacts, `theme-dark-${viewport.width}.png`),
        animations: "disabled",
      });
    }
    await toggle(
      page.getByRole("dialog").locator(".detail-dialog-header"),
      page,
      "light",
    );
    assert.equal(
      await page.evaluate(() => localStorage.getItem("responsara-theme")),
      "light",
    );
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await readableSurface(page.getByRole("dialog"), "light");

    const blocked = await browser.newContext({
      viewport: { width: 1024, height: 768 },
      colorScheme: "dark",
    });
    await blocked.addInitScript(() =>
      Object.defineProperty(window, "localStorage", {
        configurable: true,
        get() {
          throw new DOMException("Storage unavailable", "SecurityError");
        },
      }),
    );
    const blockedPage = await blocked.newPage();
    watch(blockedPage);
    await blockedPage.goto(base + "/?demo=1#/overview");
    await expect(blockedPage.locator("html")).toHaveAttribute(
      "data-theme",
      "light",
    );
    await toggle(blockedPage, blockedPage, "dark");
    await readableSurface(blockedPage.locator("body"), "dark");
    await toggle(blockedPage, blockedPage, "light");
    await readableSurface(blockedPage.locator("body"), "light");
    await blocked.close();
    assert.deepEqual(errors, []);
    assert.deepEqual(
      writes,
      [],
      "appearance changes and local draft review never write to backend endpoints",
    );
    console.log(
      "Theme browser passed: explicit light default, persisted modes, both dialog levels, readable surfaces, unfiltered map with preserved view, preserved draft, tablet containment and unavailable storage; no backend writes.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
