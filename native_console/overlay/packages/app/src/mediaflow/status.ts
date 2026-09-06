type Request = (input: string, init?: RequestInit) => Promise<Response>
export type Migration = { state: string; message: string; retryable?: boolean }

export async function migrationStatus(request: Request = fetch): Promise<Migration> {
  const response = await request("/api/agent/status", { signal: AbortSignal.timeout(5000) })
  if (!response.ok) throw new Error("status_unavailable")
  const value = (await response.json()).migration
  if (!value || typeof value.state !== "string" || typeof value.message !== "string") throw new Error("status_unavailable")
  return { state: value.state, message: value.message, ...(typeof value.retryable === "boolean" ? { retryable: value.retryable } : {}) }
}

export async function retryMigration(request: Request = fetch) {
  const response = await request("/api/agent/native-migration/retry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
    signal: AbortSignal.timeout(10000),
  })
  if (!response.ok) throw new Error("migration_retry_unconfirmed")
  return migrationStatus(request)
}
