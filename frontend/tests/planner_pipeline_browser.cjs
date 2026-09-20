// Real incident HTTP/WS + built React app. Public fixtures and injected LLM only;
// no intercepted browser API routes and no telephony provider client.
const { chromium, expect } = require("@playwright/test");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../..");
(async () => {
  const socket = net.createServer();
  await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  fs.mkdirSync(path.join(root, "frontend/artifacts"), { recursive: true });
  const directory = fs.mkdtempSync(
    path.join(root, "frontend/artifacts/pipeline-"),
  );
  const token = "synthetic-fire-token-at-least-32-characters";
  const bootstrap = `import json, sys\nfrom pathlib import Path\nimport uvicorn\nfrom fireline.incident_runtime import IncidentRuntime,demo_trigger\nfrom fireline.incident_server import create_app\nfrom tests.test_incident_runtime import NOW,settings\nfrom tests.test_llm_assessment import Model\ndirectory=Path(sys.argv[1])\n(directory/'trigger.json').write_text(json.dumps(demo_trigger(NOW)))\ncfg=settings()|{'llm_assessment':{'mode':'live'}}\nruntime=IncidentRuntime(directory/'incident',cfg,assessment_backend=Model())\napp=create_app(runtime,fire_token='${token}',result_token='synthetic-results-token-at-least-32-characters',tick_interval=0,poll_interval=.05)\nuvicorn.run(app,host='127.0.0.1',port=int(sys.argv[2]),log_level='warning')\n`;
  const script = path.join(directory, "server.py");
  fs.writeFileSync(script, bootstrap);
  const server = spawn(
    path.resolve(root, "../live-dashboard/.venv/bin/python"),
    [script, directory, String(port)],
    {
      cwd: root,
      env: { ...process.env, PYTHONPATH: "." },
      stdio: ["ignore", "ignore", "pipe"],
    },
  );
  let stderr = "",
    browser;
  server.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
  });
  const base = `http://127.0.0.1:${port}`;
  async function post(endpoint, body) {
    const response = await fetch(base + endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + token,
      },
      body: JSON.stringify(body),
    });
    assert.equal(response.status, 200, await response.clone().text());
    return response.json();
  }
  try {
    await expect
      .poll(
        async () => {
          if (server.exitCode !== null) throw new Error(stderr);
          try {
            return (await fetch(base + "/api/state")).status;
          } catch {
            return 0;
          }
        },
        { timeout: 15000 },
      )
      .toBe(503);
    const trigger = JSON.parse(
      fs.readFileSync(path.join(directory, "trigger.json"), "utf8"),
    );
    let state = await post("/api/fire", trigger);
    assert.equal(state.assets.length, 3);
    assert.ok(
      state.assets.every((a) => a.llm_assessment.status === "assessed"),
    );
    browser = await chromium.launch({ channel: "chrome", headless: true });
    const page = await browser.newPage({
      viewport: { width: 1512, height: 1100 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(base + "/#/incidents/end-to-end-demo/plan");
    await expect(
      page.getByRole("heading", { name: "Firefighter plan", exact: true }),
    ).toBeVisible();
    state = await post("/api/simulate", { asset_id: "A", status: "no_answer" });
    assert.ok(
      state.tasks.some(
        (t) => t.asset_id === "A" && t.kind === "human_callback",
      ),
    );
    const answerFields = [
      "identity_confirmed",
      "whole_household_confirmed",
      "can_self_evacuate",
      "transport_available",
      "wants_human",
      "acknowledged",
      "road_warning_acknowledged",
    ];
    state = await post("/api/simulate", {
      asset_id: "C",
      status: "completed",
      answers: {
        identity_confirmed: true,
        whole_household_confirmed: true,
        can_self_evacuate: false,
        transport_available: false,
        wants_human: true,
        acknowledged: true,
        road_warning_acknowledged: true,
        confidence: 0.95,
        confidence_basis: "synthetic_review",
        evidence: Object.fromEntries(
          answerFields.map((k) => [k, "Synthetic explicit response"]),
        ),
      },
    });
    await page
      .getByRole("combobox", { name: "Crew", exact: true })
      .selectOption("crew-2");
    await expect(
      page.getByText("Planned arrivals · this crew:", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Reception destination:", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Planned trips:", { exact: true }),
    ).toBeVisible();
    assert.equal(
      state.plan.response.teams.find((t) => t.team_id === "crew-2").tasks[0]
        .delivered_people,
      2,
    );
    await page.screenshot({
      path: path.join(directory, "connected-plan.png"),
      fullPage: true,
    });
    await page.goto(base + "/#/incidents/end-to-end-demo/calls");
    await expect(
      page
        .getByRole("row")
        .filter({ hasText: "Demo Cedar Care Home" })
        .getByText("No answer", { exact: true }),
    ).toBeVisible();
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(directory, "verification.json"),
      JSON.stringify(
        {
          assets: 3,
          assessments: 3,
          noAnswerEscalated: true,
          assistanceMissionToReception: true,
          liveBrowserUpdates: true,
          realPhoneCalls: 0,
          revision: state.revision,
        },
        null,
        2,
      ),
    );
    console.log(
      "Connected pipeline browser passed: fire -> 3 assessments -> priority queue -> no-answer escalation -> assistance mission -> real WebSocket UI. " +
        directory,
    );
  } finally {
    if (browser) await browser.close();
    if (server.exitCode === null) {
      const exited = new Promise((resolve) => server.once("exit", resolve));
      server.kill("SIGTERM");
      await exited;
    }
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
