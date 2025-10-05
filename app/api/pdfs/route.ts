import { NextResponse } from 'next/server';
import fs from 'node:fs';
import path from 'node:path';
export const dynamic = 'force-dynamic';
export async function GET() {
  const pub = path.join(process.cwd(), 'public', 'pdfs');
  if (!fs.existsSync(pub)) return NextResponse.json({ items: [] });
  const files = fs.readdirSync(pub).filter(n => n.toLowerCase().endsWith('.pdf'));
  files.sort((a,b)=> a.localeCompare(b, 'zh-Hans-CN'));
  const items = files.map((name, idx) => ({ id: idx, name: name.replace(/\.pdf$/i,''), file: `/pdfs/${name}` }));
  return NextResponse.json({ items });
}