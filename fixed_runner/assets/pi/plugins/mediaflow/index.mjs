import {readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

export default {
  activate(host) {
    host.route('GET','/skill',(_req,res)=>{
      res.download(fileURLToPath(new URL('./mediaflow-platform.zip',import.meta.url)),'mediaflow-platform.zip');
    });
    host.route('GET','/trial',(_req,res)=>{
      let used=0;
      const directory=process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR;
      if(directory) {
        try {used=readdirSync(directory).filter(name=>/^(?:[1-9]|10)\.claim$/.test(name)).length;}
        catch(error) {if(error.code!=='ENOENT')return res.status(503).json({error:'额度记录读取失败，停止真实验证。'});}
      }
      res.json({limit:10,used,remaining:Math.max(0,10-used),message:used>=10?'本轮10次真实模型验收额度已用完，未发送新增请求。':'本轮模型验收请求次数；工具续接和失败均计入。'});
    });
  },
};
