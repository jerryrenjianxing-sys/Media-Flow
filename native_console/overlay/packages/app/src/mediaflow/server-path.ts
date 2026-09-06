/** Preserve a configured reverse-proxy base path for root-relative native APIs.
 * Request payloads, event bodies, headers and cancellation remain untouched.
 */
export function prefixFetch(baseUrl: string, request: typeof globalThis.fetch) {
  const base = new URL(baseUrl)
  const prefix = base.pathname.replace(/\/+$/, "")
  if (!prefix) return request
  return Object.assign((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : input)
    if (url.origin !== base.origin || url.pathname === prefix || url.pathname.startsWith(prefix + "/")) return request(input, init)
    url.pathname = prefix + url.pathname
    return request(input instanceof Request ? new Request(url, input) : url, init)
  }, { preconnect: request.preconnect })
}
