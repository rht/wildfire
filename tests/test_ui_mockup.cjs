const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { JSDOM } = require("jsdom");

const mockupPath = path.join(__dirname, "..", "design", "ui-mockup.html");
const html = fs.readFileSync(mockupPath, "utf8");

function loadMockup() {
  const dom = new JSDOM(html, {
    runScripts: "outside-only",
    url: "https://mockup.test/",
  });
  const { window } = dom;
  const markerTooltips = [];
  const markers = [];

  const layerGroup = {
    addTo() { return this; },
    clearLayers() {},
  };
  window.L = {
    map() {
      return { setView() { return this; } };
    },
    tileLayer() {
      return { addTo() { return this; } };
    },
    layerGroup() {
      return Object.create(layerGroup);
    },
    polygon() {
      return {
        bindTooltip() { return this; },
        addTo() { return this; },
      };
    },
    circleMarker() {
      const handlers = {};
      const marker = {
        addTo() { return this; },
        bindTooltip(content) {
          markerTooltips.push(content);
          return this;
        },
        on(eventName, handler) {
          handlers[eventName] = handler;
          return this;
        },
        fire(eventName) {
          handlers[eventName]?.({ target: this });
        },
      };
      markers.push(marker);
      return marker;
    },
  };

  const appScript = [...window.document.scripts].find(script => !script.src);
  assert.ok(appScript, "inline application script is present");
  window.eval(`${appScript.textContent}
    window.__mockupTest = {
      updateAsset(index, updates) {
        Object.assign(current.assets[index], updates);
        render();
      }
    };
  `);

  return { dom, window, markerTooltips, markers };
}

test("snapshot fields render as text while queue selection and tooltips remain interactive", () => {
  const { dom, window, markerTooltips, markers } = loadMockup();
  const { document } = window;
  const hostile = {
    id: `asset-'\"><img data-injected src=x>`,
    name: `<img data-injected src=x>Camp`,
    type: `<svg data-injected>school</svg>`,
    reason: `<script data-injected>bad()</script>`,
    source: `<a data-injected href=//attacker.test>source</a>`,
    notes: `<iframe data-injected>notes</iframe>`,
  };

  window.__mockupTest.updateAsset(0, {
    asset_id: hostile.id,
    name: hostile.name,
    asset_type: hostile.type,
    review_reasons: [hostile.reason],
    sources: [{
      fields: ["estimated_<occupancy>"],
      source: hostile.source,
      observed_at: "2026-07-02<script>",
      notes: hostile.notes,
    }]
  });

  assert.equal(document.querySelectorAll("[data-injected]").length, 0);
  assert.match(document.getElementById("reviewQueue").textContent, /<img data-injected src=x>Camp/);

  const queueRow = [...document.querySelectorAll("#reviewQueue .qrow")]
    .find(row => row.textContent.includes(hostile.name));
  assert.ok(queueRow, "hostile-name asset remains in the review queue");
  queueRow.click();

  assert.match(document.getElementById("detailPanel").textContent, /asset-'\"><img data-injected src=x>/);
  assert.match(document.getElementById("detailPanel").textContent, /<a data-injected href=\/\/attacker.test>source<\/a>/);
  assert.equal(document.querySelectorAll("[data-injected]").length, 0);

  const assetMarker = markers.find(marker => marker.assetId === hostile.id);
  assert.ok(assetMarker, "hostile-ID asset retains a map marker");
  assetMarker.fire("click");
  assert.match(document.getElementById("detailPanel").textContent, /<img data-injected src=x>Camp/);

  const actionButton = [...document.querySelectorAll(".taskActions button")]
    .find(button => button.textContent === "confirm occupancy");
  assert.ok(actionButton, "task action remains available for a hostile asset ID");
  actionButton.click();
  assert.match(document.getElementById("changeLog").textContent, /<img data-injected src=x>Camp/);
  assert.equal(document.querySelectorAll("[data-injected]").length, 0);

  const assetTooltip = markerTooltips.find(content => content.textContent?.includes(hostile.name));
  assert.ok(assetTooltip instanceof window.HTMLElement, "asset tooltip uses an HTML element");
  assert.match(assetTooltip.textContent, /<img data-injected src=x>Camp/);
  assert.equal(assetTooltip.querySelectorAll("[data-injected]").length, 0);

  dom.window.close();
});

test("task assignment, snapshot advance, and change-log controls preserve behavior", () => {
  const { dom, window } = loadMockup();
  const { document, Event } = window;

  assert.equal(document.querySelectorAll("#rankedList .row").length, 5);
  document.querySelector("#rankedList .row").click();

  const contactButton = [...document.querySelectorAll(".taskActions button")]
    .find(button => button.textContent === "contact facility");
  assert.ok(contactButton, "contact action is available");
  contactButton.click();

  const teamSelect = document.querySelector(".assignRow select");
  assert.ok(teamSelect, "team selector appears after creating a task");
  teamSelect.value = "t-creu-roja-1";
  teamSelect.dispatchEvent(new Event("change", { bubbles: true }));
  assert.match(document.getElementById("detailPanel").textContent, /assigned/);
  assert.match(document.getElementById("changeLog").textContent, /Creu Roja team 1/);

  document.getElementById("nextUpdateBtn").click();
  assert.equal(document.getElementById("nextUpdateBtn").disabled, true);
  assert.equal(document.getElementById("nextUpdateBtn").textContent, "No further updates (demo)");
  assert.match(document.getElementById("detailPanel").textContent, /assigned/);

  document.getElementById("changeLogBtn").click();
  assert.equal(document.getElementById("changeLog").classList.contains("open"), true);

  dom.window.close();
});
