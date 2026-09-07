/** Acceptance-only wire guard. Does not inspect or buffer conversation payloads. */
import { mkdirSync, openSync, closeSync, writeSync, fsyncSync } from 'node:fs';
import { join } from 'node:path';

export function normalizeRequestLimit(value = 10) {
  if (value === 'unlimited') return value;
  if (typeof value === 'string' && /^[1-9]\d*$/.test(value)) value = Number(value);
  if (!Number.isSafeInteger(value) || value < 1) throw Error('MediaFlow request limit is invalid');
  return value;
}

export function createBudgetFetch({directory, endpoint, limit = 10, fetch: upstream}) {
  const target = new URL(endpoint);
  const requestLimit = normalizeRequestLimit(limit);
  return async function budgetFetch(input, init) {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
    const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (method === 'POST' && url.origin === target.origin && url.pathname === target.pathname) {
      if (!directory) throw Error('MediaFlow request ledger is required');
      mkdirSync(directory, {recursive:true});
      let reserved = false;
      for (let slot = 1; requestLimit === 'unlimited' || slot <= requestLimit; slot++) {
        if (!Number.isSafeInteger(slot)) throw Error('MediaFlow request ledger exhausted safe numbering');
        let fd;
        try { fd = openSync(join(directory, `${slot}.claim`), 'wx'); }
        catch (error) { if (error.code === 'EEXIST') continue; throw error; }
        try { writeSync(fd, JSON.stringify({slot, at:new Date().toISOString(), pid:process.pid})); fsyncSync(fd); }
        finally { closeSync(fd); }
        reserved = true;
        break;
      }
      if (!reserved) throw Error(`MediaFlow 本轮${requestLimit}次真实模型请求额度已用完；未发送新请求。`);
    }
    return upstream(input, init);
  };
}

if (process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR) {
  globalThis.fetch = createBudgetFetch({
    directory:process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR,
    endpoint:'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions',
    limit:process.env.MEDIAFLOW_PI_REQUEST_LIMIT ?? 10,
    fetch:globalThis.fetch.bind(globalThis),
  });
}
