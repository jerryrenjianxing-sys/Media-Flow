'use strict';

function createDesktopExit({ waitForStartup, updates, runtime, quit }) {
  let pending = null;
  return {
    get pending() { return pending !== null; },
    request(stopBackground) {
      if (pending) return pending;
      pending = (async () => {
        await waitForStartup();
        await updates.prepareExit({ keepBackground: !stopBackground });
        if (stopBackground) {
          if (!runtime) throw new Error('后台实例尚未核实，不能完全退出。');
          await runtime.stop();
        }
        quit();
      })().finally(() => { pending = null; });
      return pending;
    }
  };
}
module.exports = { createDesktopExit };
