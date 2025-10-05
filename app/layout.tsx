import './globals.css';
export const metadata = { title: '电路图 PDF · OCR 搜索', description: '扫描件也能搜与定位' };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="container">
          <h1 style={{margin:'12px 0 4px'}}>PDF 电路图 · OCR 搜索</h1>
          <p className="muted" style={{marginTop:0}}>扫描件友好 · 同义词扩展 · 标题/表格加权 · 一键“下一处” · 持久化缓存</p>
          {children}
        </div>
      </body>
    </html>
  );
}