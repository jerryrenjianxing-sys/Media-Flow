import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

test('native uncached shell discovery uses preserved Git root without downloading',
  {skip:process.platform!=='win32'||!process.env.MEDIAFLOW_PI_SOURCE},async()=>{
    const root=mkdtempSync(join(tmpdir(),'mediaflow-shell-'));
    const previous={ProgramFiles:process.env.ProgramFiles,
      'ProgramFiles(x86)':process.env['ProgramFiles(x86)'],USERPROFILE:process.env.USERPROFILE};
    const originalFetch=globalThis.fetch;
    let requests=0;
    globalThis.fetch=async()=>{requests++;throw Error('network disabled in test');};
    try {
      process.env.ProgramFiles=join(root,'apps');
      process.env['ProgramFiles(x86)']=join(root,'apps86');
      process.env.USERPROFILE=join(root,'profile');
      const bash=join(root,'apps','Git','bin','bash.exe');
      mkdirSync(join(root,'apps','Git','bin'),{recursive:true});
      writeFileSync(bash,'fixture; never executed');
      const upstream=await import(pathToFileURL(join(process.env.MEDIAFLOW_PI_SOURCE,'dist/server/ensure-bash.js')));
      assert.equal(existsSync(upstream.windowsBashPath()),false);
      assert.equal(upstream.hasGitBash(),true);
      assert.equal(await upstream.ensureWindowsBash(),null);
      assert.equal(requests,0);
    } finally {
      globalThis.fetch=originalFetch;
      for(const [key,value] of Object.entries(previous)) {
        if(value===undefined) delete process.env[key]; else process.env[key]=value;
      }
    }
});
