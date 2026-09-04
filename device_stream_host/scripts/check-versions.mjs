import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(await readFile(join(root, "runtime-manifest.json"), "utf8"));
const project = JSON.parse(await readFile(join(root, "package.json"), "utf8"));

for (const [name, expected] of Object.entries(manifest.tango)) {
  if (project.dependencies[name] !== expected) {
    throw new Error(`${name} must stay pinned to ${expected}`);
  }
  const installed = JSON.parse(
    await readFile(join(root, "node_modules", name, "package.json"), "utf8"),
  ).version;
  if (installed !== expected) {
    throw new Error(`${name} installed ${installed}, expected ${expected}`);
  }
}

const serverPath = join(root, manifest.scrcpyServer.file);
const digest = createHash("sha256").update(await readFile(serverPath)).digest("hex");
if (digest !== manifest.scrcpyServer.sha256) {
  throw new Error(`scrcpy server checksum mismatch: ${digest}`);
}

process.stdout.write(
  `${JSON.stringify({ ok: true, protocol: manifest.protocol, scrcpy: manifest.scrcpyServer.version })}\n`,
);
