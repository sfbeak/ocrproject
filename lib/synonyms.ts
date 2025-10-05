export function normalize(s:string){ return s.toLowerCase().trim().replace(/\s+/g,''); }
const DICT: Record<string,string[]> = {
  '油门踏板': ['踏板位置传感器','加速踏板','acceleratorpedalsensor','aps'],
  '踏板位置传感器': ['油门踏板','acceleratorpedalsensor','aps'],
  'aps': ['acceleratorpedalsensor','油门踏板','踏板位置传感器'],
  '挂车控制模块': ['挂车模块','挂车制动控制','trailercontrolmodule','tcm'],
  'ecu': ['pcm','发动机控制单元','enginecontrolunit'],
  '仪表': ['组合仪表','cluster','ipc'],
  '喇叭': ['horn'],
  '点烟器': ['cigarette','cigarlighter','poweroutlet'],
  '大灯': ['前照灯','headlamp','headlight'],
  '空调': ['a/c','空调系统'],
  '电动窗': ['车窗','window','pw']
};
export function expandSynonyms(q:string):string[]{
  const n = normalize(q); const base = [n];
  for (const [k, vals] of Object.entries(DICT)) {
    const kn = normalize(k);
    if (n === kn || vals.map(normalize).includes(n)) base.push(kn, ...vals.map(normalize));
  }
  return Array.from(new Set(base));
}
export function candidateTerms(terms:string[]):string[]{ return terms.map(t => t.replace(/[^\p{L}\p{N}]/gu,'')); }