import type { Hit } from './types'; export function rankHits(hits:Hit[]):Hit[]{ return [...hits].sort((a,b)=> b.score - a.score || a.page - b.page || a.index - b.index); }
