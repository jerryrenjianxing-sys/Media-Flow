export type SessionUi = {text:string;requestId:string|null;sentText?:string;notice:string;answers:Record<string,string|string[]>};
export function shouldSendKey(event:{key:string;shiftKey?:boolean;isComposing?:boolean;keyCode?:number;repeat?:boolean}):boolean;
export function resizeComposer(input:HTMLTextAreaElement|null):void;
type StorageLike = Pick<Storage,"getItem"|"setItem">;
export function rememberSession(storage:StorageLike,id:string):void;
export function chooseSession(search:string,storage:StorageLike,ids:string[]):string;
export function loadSessionUi(storage:StorageLike,id:string):SessionUi;
export function saveSessionUi(storage:StorageLike,id:string,value:SessionUi):boolean;
