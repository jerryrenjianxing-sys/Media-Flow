export type SessionUi = {text:string;requestId:string|null;notice:string;answers:Record<string,string>};
type StorageLike = Pick<Storage,"getItem"|"setItem">;
export function rememberSession(storage:StorageLike,id:string):void;
export function chooseSession(search:string,storage:StorageLike,ids:string[]):string;
export function loadSessionUi(storage:StorageLike,id:string):SessionUi;
export function saveSessionUi(storage:StorageLike,id:string,value:SessionUi):boolean;
