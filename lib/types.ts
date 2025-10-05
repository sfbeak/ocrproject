export type Box = { x:number;y:number;w:number;h:number };
export type Hit = { page:number; index:number; text:string; score:number; box:Box; source:'pdf'|'ocr'; rawConf?:number };
export type OCRPage = { page:number; w:number; h:number; hits:{ text:string; conf:number; box:Box }[] };
