import Link from 'next/link';
import { headers } from 'next/headers';
export const dynamic = 'force-dynamic';
async function getList() {
  const h = headers();
  const host = h.get('x-forwarded-host') || h.get('host');
  const proto = h.get('x-forwarded-proto') || 'http';
  const base = `${proto}://${host}`;
  const r = await fetch(`${base}/api/pdfs`, { cache: 'no-store' });
  if (!r.ok) return { items: [] };
  return r.json();
}
export default async function Home() {
  const { items } = await getList();
  return (
    <div className="grid">
      {items.map((p:any)=> (
        <div key={p.id} className="card">
          <h3 style={{marginTop:0}}>{p.name}</h3>
          <div className="row" style={{justifyContent:'space-between'}}>
            <span className="pill">文件：{p.file.split('/').pop()}</span>
            <a className="btn" href={`/view/${p.id}`}>打开</a>
          </div>
        </div>
      ))}
      {!items.length && <div className="card">把 PDF 放到 <code>public/pdfs/</code> 目录，然后刷新本页。</div>}
    </div>
  );
}