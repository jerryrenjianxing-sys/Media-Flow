export class LocalApiTimeoutError extends Error {
  constructor(public readonly timeoutMs: number) {
    super(`本机服务响应超时（${Math.ceil(timeoutMs / 1000)} 秒），页面已解除等待；请先刷新状态，不要连续重复点击`);
    this.name = "LocalApiTimeoutError";
  }
}

function instanceApiInput(input: RequestInfo | URL): RequestInfo | URL {
  if (typeof window === "undefined" || !window.mediaflowDesktop) return input;
  const original = input instanceof Request ? input.url : String(input);
  let requested: URL;
  try { requested = new URL(original); }
  catch { return input; }
  const loopback = ["127.0.0.1", "localhost", "[::1]"];
  if (!["http:", "https:"].includes(requested.protocol) || !loopback.includes(requested.hostname) || requested.port !== "48138") return input;

  // Resolve before fetch: a cross-origin redirect makes Chromium send Origin:
  // null, which the isolated API correctly refuses. Never fall back to the
  // production address when a desktop instance configuration is unavailable.
  const bridge = window.mediaflowDesktop;
  if (typeof bridge.getInstance !== "function") throw new Error("桌面实例配置暂不可用，未发送本机请求。");
  let instance;
  try { instance = bridge.getInstance(); }
  catch { throw new Error("桌面实例配置暂不可用，未发送本机请求。"); }
  if (!instance?.apiUrl) throw new Error("桌面实例配置暂不可用，未发送本机请求。");
  const api = new URL(instance.apiUrl);
  const acceptance = instance.acceptance === true || instance.id?.startsWith("acceptance") === true;
  if (!["http:", "https:"].includes(api.protocol) || !loopback.includes(api.hostname) || api.username || api.password || api.pathname !== "/" || api.search || api.hash ||
    (acceptance && (!api.port || api.port === "48138"))) throw new Error("桌面实例 API 地址无效，未发送本机请求。");
  requested.protocol = api.protocol;
  requested.host = api.host;
  return input instanceof Request ? new Request(requested, input) : requested;
}

export async function fetchLocalApi(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = 12_000,
): Promise<Response> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const callerSignal = init.signal;
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  if (callerSignal) {
    if (callerSignal.aborted) abortFromCaller();
    else callerSignal.addEventListener("abort", abortFromCaller, { once: true });
  }
  try {
    const response = await fetch(instanceApiInput(input), { ...init, signal: controller.signal });
    const body = await response.arrayBuffer();
    return new Response(body.byteLength ? body : null, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  } catch (error) {
    if (timedOut) throw new LocalApiTimeoutError(timeoutMs);
    throw error;
  } finally {
    globalThis.clearTimeout(timer);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }
}
