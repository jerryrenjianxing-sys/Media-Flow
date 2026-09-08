import {readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

export default {
  activate(host) {
    host.route('GET','/skill',(_req,res)=>{
      res.download(fileURLToPath(new URL('./mediaflow-platform.zip',import.meta.url)),'mediaflow-platform.zip');
    });
    host.route('GET','/trial',(_req,res)=>{
      let used=0;
      const limit='unlimited';
      const directory=process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR;
      if(directory) {
        try {used=readdirSync(directory).filter(name=>/^[1-9]\d*\.claim$/.test(name)).length;}
        catch(error) {if(error.code!=='ENOENT')return res.status(503).json({error:'用量记录读取失败，请检查记录目录。'});}
      }
      return res.json({limit,used,remaining:null,
        message:`已记录${used}次真实模型请求；当前不设固定总次数，失败和工具续接仍计入。`});
    });
  },
};
