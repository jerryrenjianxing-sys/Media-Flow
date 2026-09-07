import {readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

function requestLimit() {
  const value=process.env.MEDIAFLOW_PI_REQUEST_LIMIT ?? '10';
  if(value==='unlimited') return value;
  if(!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value))) throw Error('invalid request limit');
  return Number(value);
}

export default {
  activate(host) {
    host.route('GET','/skill',(_req,res)=>{
      res.download(fileURLToPath(new URL('./mediaflow-platform.zip',import.meta.url)),'mediaflow-platform.zip');
    });
    host.route('GET','/trial',(_req,res)=>{
      let used=0;
      let limit;
      try {limit=requestLimit();}
      catch {return res.status(503).json({error:'请求额度配置无效，停止真实验证。'});}
      const directory=process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR;
      if(directory) {
        try {used=readdirSync(directory).filter(name=>/^[1-9]\d*\.claim$/.test(name)).length;}
        catch(error) {if(error.code!=='ENOENT')return res.status(503).json({error:'额度记录读取失败，停止真实验证。'});}
      }
      if(limit==='unlimited') return res.json({limit,used,remaining:null,
        message:`已记录${used}次真实模型请求；当前不设固定总次数，失败和工具续接仍计入。`});
      res.json({limit,used,remaining:Math.max(0,limit-used),message:used>=limit?`本轮${limit}次真实模型验收额度已用完，未发送新增请求。`:'本轮模型验收请求次数；工具续接和失败均计入。'});
    });
  },
};
