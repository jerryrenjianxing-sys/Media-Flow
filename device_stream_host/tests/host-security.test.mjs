import assert from "node:assert/strict";
import { once } from "node:events";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import test from "node:test";
import WebSocket from "ws";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function rejectedUpgrade(url, origin, statusCode) {
  await new Promise((resolvePromise, rejectPromise) => {
    const socket = new WebSocket(url, { origin });
    socket.once("unexpected-response", (_request, response) => {
      try {
        assert.equal(response.statusCode, statusCode);
        response.resume();
        resolvePromise();
      } catch (error) { rejectPromise(error); }
    });
    socket.once("open", () => rejectPromise(new Error("upgrade unexpectedly succeeded")));
    socket.once("error", () => undefined);
  });
}

test("host binds loopback and rejects unsafe upgrades and generic routes", async () => {
  const temporary = await mkdtemp(join(tmpdir(), "mediaflow-stream-test-"));
  const secretPath = join(temporary, "stream.secret");
  await writeFile(secretPath, "x".repeat(64), "utf8");
  const port = 48149;
  const child = spawn(process.execPath, [join(root, "src", "server.mjs")], {
    cwd: root,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      RISKFLOW_STREAM_HOST: "127.0.0.1",
      RISKFLOW_STREAM_PORT: String(port),
      RISKFLOW_STREAM_API: "http://127.0.0.1:9",
      RISKFLOW_STREAM_SECRET_PATH: secretPath,
      RISKFLOW_SCRCPY_SERVER: join(root, "vendor", "scrcpy-server-v3.3.3"),
    },
  });
  try {
    await Promise.race([
      once(child.stdout, "data"),
      new Promise((_, rejectPromise) => setTimeout(() => rejectPromise(new Error("host startup timeout")), 5000)),
    ]);
    const health = await fetch(`http://127.0.0.1:${port}/health`);
    assert.equal(health.status, 200);
    assert.deepEqual(await health.json(), { ok: true, protocol: 1, active_streams: 0 });
    assert.equal((await fetch(`http://127.0.0.1:${port}/shell`)).status, 404);
    const session = "a".repeat(32);
    const token = "b".repeat(36);
    await rejectedUpgrade(`ws://127.0.0.1:${port}/v1/sessions/${session}?token=${token}`, "http://evil.example", 403);
    await rejectedUpgrade(`ws://127.0.0.1:${port}/v1/sessions/${session}?token=short`, "http://127.0.0.1:3000", 401);
  } finally {
    child.kill();
    await Promise.race([once(child, "exit"), new Promise((resolvePromise) => setTimeout(resolvePromise, 2000))]);
    await rm(temporary, { recursive: true, force: true });
  }
});
