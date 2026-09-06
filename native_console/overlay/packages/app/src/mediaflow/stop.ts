/** One explicit write, never retried: a lost receipt is an unknown outcome. */
export async function stopAutomation(request: (input: string, init: RequestInit) => Promise<Response> = fetch) {
  const response = await request("/api/automation-stop", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stopped: true }),
    signal: AbortSignal.timeout(10000),
  })
  if (!response.ok) throw new Error("stop_unconfirmed")
  const receipt = await response.json()
  if (receipt?.ok !== true || receipt?.stopped !== true) throw new Error("stop_unconfirmed")
}
