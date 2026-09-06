import { expect, test } from "bun:test"
import { migrationStatus, retryMigration } from "./status"

test("status exposes only the migration receipt", async () => {
  const receipt = { state: "failed", message: "preserved", retryable: true }
  expect(await migrationStatus(async () => Response.json({ interface: "native", migration: receipt, unrelated: "hidden" }))).toEqual(receipt)
})

test("retry sends the required JSON request once then refreshes status", async () => {
  const calls: string[] = []
  const result = await retryMigration(async (input, init) => {
    calls.push(`${init?.method ?? "GET"} ${input}`)
    if (init?.method === "POST") {
      if (new Headers(init.headers).get("Content-Type") !== "application/json") return Response.json({ error: "json_required" }, { status: 415 })
      expect(JSON.parse(String(init.body))).toEqual({})
    }
    return Response.json(init?.method === "POST" ? { state: "starting" } : { migration: { state: "migrated", message: "ready" } })
  })
  expect(calls).toEqual(["POST /api/agent/native-migration/retry", "GET /api/agent/status"])
  expect(result.state).toBe("migrated")
})

test("unavailable status and failed writes reject without retries", async () => {
  await expect(migrationStatus(async () => Response.json({ error: "unavailable" }, { status: 503 }))).rejects.toThrow()
  let calls = 0
  await expect(retryMigration(async () => { calls++; throw new Error("offline") })).rejects.toThrow()
  expect(calls).toBe(1)
})
