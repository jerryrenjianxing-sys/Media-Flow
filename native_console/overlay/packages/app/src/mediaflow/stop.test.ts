import { expect, test } from "bun:test"
import { createServer } from "node:http"
import { stopAutomation } from "./stop"

test("emergency stop sends one real PUT with explicit stopped state", async () => {
  const received: { method: string; body: unknown; path: string }[] = []
  const server = createServer(async (request, response) => {
    response.setHeader("Access-Control-Allow-Origin", "*")
    response.setHeader("Access-Control-Allow-Methods", "PUT, OPTIONS")
    response.setHeader("Access-Control-Allow-Headers", "Content-Type")
    if (request.method === "OPTIONS") { response.end(); return }
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    received.push({ method: request.method!, body: JSON.parse(Buffer.concat(chunks).toString()), path: request.url! })
    response.setHeader("Content-Type", "application/json")
    response.end(JSON.stringify({ ok: true, stopped: true }))
  })
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
  const address = server.address()
  if (!address || typeof address === "string") throw new Error("missing_test_listener")
  try {
    await stopAutomation((input, init) => fetch(new URL(String(input), `http://127.0.0.1:${address.port}`), init))
    expect(received).toEqual([{ method: "PUT", body: { stopped: true }, path: "/api/automation-stop" }])
  } finally { server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve())) }
})

test("unconfirmed and business failure receipts are never successful", async () => {
  for (const receipt of [{ ok: false }, { ok: true }, { ok: true, stopped: false }]) {
    await expect(stopAutomation(async () => Response.json(receipt))).rejects.toThrow()
  }
})

test("transport failures do not retry writes", async () => {
  let calls = 0
  await expect(stopAutomation(async () => { calls++; throw new Error("connection lost") })).rejects.toThrow()
  expect(calls).toBe(1)
})
