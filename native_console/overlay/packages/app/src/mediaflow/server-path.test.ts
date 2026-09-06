import { expect, test } from "bun:test"
import { detectServerProtocol } from "../utils/server-protocol"
import { createApiForServer, createSdkForServer } from "../utils/server"
import { checkServerHealth } from "../utils/server-health"
import { terminalWebSocketURL } from "../utils/terminal-websocket-url"
import { prefixFetch } from "./server-path"

const server = { url: "http://localhost:4096/opencode/" }

test("prefix adapter preserves request contents, cancellation, and response identity", async () => {
  const controller = new AbortController()
  const headers = new Headers({ "Content-Type": "application/json", Authorization: "Basic fixture" })
  const body = JSON.stringify({ prompt: "unchanged" })
  const response = new Response("event: fixture\ndata: unchanged\n\n", { headers: { "Content-Type": "text/event-stream" } })
  const init = { method: "POST", headers, body, signal: controller.signal }
  const request = Object.assign(async (input: RequestInfo | URL, options?: RequestInit) => {
    expect(new URL(input instanceof Request ? input.url : input).pathname).toBe("/opencode/api/session")
    expect(options).toBe(init)
    return response
  }, { preconnect: globalThis.fetch.preconnect })
  expect(await prefixFetch(server.url, request)("http://localhost:4096/api/session", init)).toBe(response)
})

test("prefix adapter does not duplicate prefixes or change foreign origins", async () => {
  const paths: string[] = []
  const request = Object.assign(async (input: RequestInfo | URL) => {
    paths.push(String(input))
    return new Response()
  }, { preconnect: globalThis.fetch.preconnect })
  const fetch = prefixFetch(server.url, request)
  await fetch("http://localhost:4096/opencode/api/health")
  await fetch("https://example.invalid/api/health")
  expect(paths).toEqual(["http://localhost:4096/opencode/api/health", "https://example.invalid/api/health"])
})
function transport(paths: string[]) {
  return Object.assign(async (input: RequestInfo | URL) => {
    const url = new URL(input instanceof Request ? input.url : input)
    paths.push(url.pathname)
    return Response.json({ healthy: true, version: "1.18.29" })
  }, { preconnect: globalThis.fetch.preconnect })
}

test("protocol probes preserve a server's subpath", async () => {
  const paths: string[] = []
  expect(await detectServerProtocol(server, transport(paths))).toBe("v1")
  expect(paths).toEqual(["/opencode/global/health"])
})

test("native and legacy clients preserve the configured server subpath", async () => {
  const paths: string[] = []
  const fetch = transport(paths)
  await createApiForServer({ server, fetch }).health.get()
  await createSdkForServer({ server, fetch }).global.health()
  expect(paths).toEqual(["/opencode/api/health", "/opencode/global/health"])
})

test("health polling uses the prefixed API", async () => {
  const paths: string[] = []
  expect((await checkServerHealth(server, transport(paths))).healthy).toBe(true)
  expect(paths).toEqual(["/opencode/api/health"])
})

test("both PTY protocols keep the prefix without duplicate slashes", () => {
  for (const protocol of ["v1", "v2"] as const) {
    const url = terminalWebSocketURL({ protocol, url: server.url, id: "pty_test", directory: "project", cursor: 0, ticket: "fixture" })
    expect(url.pathname).toBe(`/opencode/${protocol === "v1" ? "" : "api/"}pty/pty_test/connect`)
    expect(url.searchParams.get("ticket")).toBe("fixture")
  }
})
