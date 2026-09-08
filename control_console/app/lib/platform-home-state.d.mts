export type PlatformSnapshot = {
  paused?:boolean;
  devices?:{state?:string}[];
  task_summary?:{pending?:number;running?:number};
  virtualization?:{issues?:{diagnostic_id:string}[]};
};
export function platformSummary(snapshot:PlatformSnapshot|null,error:boolean):{
  state:'loading'|'error'|'ready';
  message:string;
  metrics:{online:number|null;running:number|null;pending:number|null;queue:string}|null;
};
export function loadSkillArchive(request:()=>Promise<Response>):Promise<Blob>;
export function loadSkillMarkdown(request:()=>Promise<Response>):Promise<string>;
