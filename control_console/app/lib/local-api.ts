export class LocalApiTimeoutError extends Error {
  constructor(public readonly timeoutMs: number) {
    super(`本机服务响应超时（${Math.ceil(timeoutMs / 1000)} 秒），页面已解除等待；请先刷新状态，不要连续重复点击`);
    this.name = "LocalApiTimeoutError";
  }
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
    const response = await fetch(input, { ...init, signal: controller.signal });
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
