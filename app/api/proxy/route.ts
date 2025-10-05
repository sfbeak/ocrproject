import { NextResponse } from 'next/server';
import fs from 'node:fs';
import path from 'node:path';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
function sanitize(baseDir: string, f: string) {
  const name = decodeURIComponent(f).replace(/\\|\//g, '');
  return path.join(baseDir, name);
}
export async function GET(req: Request) {
  const url = new URL(req.url);
  const f = url.searchParams.get('f');
  if (!f) return NextResponse.json({ error: 'missing f' }, { status: 400 });
  const base = path.join(process.cwd(), 'public', 'pdfs');
  const filePath = sanitize(base, f);
  if (!filePath.startsWith(base) || !fs.existsSync(filePath)) return NextResponse.json({ error: 'not found' }, { status: 404 });
  const stat = fs.statSync(filePath); const size = stat.size;
  const range = req.headers.get('range');
  const headers: Record<string,string> = { 'Accept-Ranges':'bytes','Content-Type':'application/pdf' };
  if (range) {
    const m = range.match(/bytes=(\d+)-(\d*)/);
    if (m) {
      const start = parseInt(m[1],10);
      const end = m[2] ? parseInt(m[2],10) : size-1;
      const chunkSize = (end-start)+1;
      headers['Content-Range'] = `bytes ${start}-${end}/${size}`;
      headers['Content-Length'] = String(chunkSize);
      const stream = fs.createReadStream(filePath, { start, end });
      return new NextResponse(stream as any, { status: 206, headers });
    }
  }
  headers['Content-Length'] = String(size);
  const stream = fs.createReadStream(filePath);
  return new NextResponse(stream as any, { status: 200, headers });
}
export async function HEAD(req: Request) {
  const url = new URL(req.url);
  const f = url.searchParams.get('f');
  if (!f) return new NextResponse(null, { status: 400 });
  const base = path.join(process.cwd(), 'public', 'pdfs');
  const filePath = sanitize(base, f);
  if (!filePath.startsWith(base) || !fs.existsSync(filePath)) return new NextResponse(null, { status: 404 });
  const stat = fs.statSync(filePath);
  return new NextResponse(null, { status: 200, headers: { 'Accept-Ranges':'bytes','Content-Type':'application/pdf','Content-Length':String(stat.size) }});
}