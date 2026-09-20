// Keep read-only browser regressions independent of confirmations saved in the user's preview.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { expect } = require("@playwright/test");
const root = path.resolve(__dirname, "../..");
(async () => {
  let server;
  try {
    const socket = net.createServer();
    await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
    const port = socket.address().port;
    await new Promise((resolve) => socket.close(resolve));
    fs.mkdirSync(path.join(root, "frontend/artifacts"), { recursive: true });
    const directory = fs.mkdtempSync(
      path.join(root, "frontend/artifacts/browser-smoke-"),
    );
    const base = `http://127.0.0.1:${port}`;
    server = spawn(
      path.resolve(root, "../live-dashboard/.venv/bin/python"),
      [
        "-m",
        "fireline.dashboard_server",
        "--demo",
        "--port",
        String(port),
        "--approvals-database",
        path.join(directory, "approvals.sqlite3"),
      ],
      {
        cwd: root,
        env: { ...process.env, PYTHONPATH: "." },
        stdio: ["ignore", "ignore", "pipe"],
      },
    );
    let output = "";
    server.stderr.on("data", (chunk) => {
      output += chunk.toString();
    });
    await expect
      .poll(
        async () => {
          if (server.exitCode !== null) throw new Error(output);
          try {
            return (await fetch(base + "/api/state")).ok;
          } catch {
            return false;
          }
        },
        { timeout: 15000 },
      )
      .toBe(true);
    for (const file of ["react_browser.cjs", "live_browser.cjs"]) {
      const child = spawn(process.execPath, [path.join(__dirname, file)], {
        cwd: path.join(root, "frontend"),
        env: { ...process.env, DASHBOARD_BASE_URL: base },
        stdio: "inherit",
      });
      const code = await new Promise((resolve) => child.once("exit", resolve));
      if (code !== 0) throw new Error(`${file} failed`);
    }
  } finally {
    if (server && server.exitCode === null) {
      const exited = new Promise((resolve) => server.once("exit", resolve));
      server.kill("SIGTERM");
      await exited;
    }
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
