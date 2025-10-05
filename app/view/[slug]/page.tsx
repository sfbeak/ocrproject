'use client';
// @ts-nocheck

import React, { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Document, Page, pdfjs } from 'react-pdf';

const ORIGIN = typeof window !== 'undefined' ? window.location.origin : '';
pdfjs.GlobalWorkerOptions.workerSrc = `${ORIGIN}/pdfjs/pdf.worker.min.js`;

// 与后端保持一致
const OCR_PARAMS = { dpi: 500, tile: 1400, overlap: 0.12 };

function normalize(s: string) {
  return (s || '').replace(/\s+/g, '').toLowerCase();
}

function expandSynonyms(q: string): string[] {
  const base = normalize(q);
  if (!base) return [];
  const map: Record<string, string[]> = {
    '油门踏板': ['踏板位置传感器', 'accelerator pedal', 'accelerator pedal sensor', 'aps'],
    '挂车控制模块': ['挂车控制单元', 'trailer control module', 'tcm'],
    'ecu': ['控制单元', 'engine control unit', '电子控制单元']
  };
  const extra = map[base] || [];
  const bag = new Set([base, ...extra.map(normalize)]);
  return Array.from(bag).filter(Boolean);
}

export default function Viewer() {
  const p = useParams() as any;
  const seg = Array.isArray(p?.slug) ? p.slug[0] : p?.slug;
  const slugStr = decodeURIComponent(String(seg ?? ''));

  // 文件与渲染
  const [file, setFile] = useState<string | null>(null);
  const [proxyUrl, setProxyUrl] = useState<string | null>(null);

  // Document 实例与挂载控制
  const [docKey, setDocKey] = useState(0);         // 强制重挂载 Document
  const [docReady, setDocReady] = useState(false); // Document 成功加载
  const pdfProxy = useRef<any>(null);              // PDFDocumentProxy

  // 页码与缩放
  const [numPages, setNumPages] = useState(0);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1.0);

  // 渲染锁与排队
  const [isRendering, setIsRendering] = useState(false);
  const [pendingPage, setPendingPage] = useState<number | null>(null);
  const [queuedJump, setQueuedJump] = useState<number | null>(null);
  const renderStallTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 页面尺寸/就绪
  const pageCssSizeRef = useRef<Map<number, { w: number; h: number }>>(new Map());
  const [readyPages, setReadyPages] = useState<Set<number>>(new Set());

  // OCR / 搜索
  const [ocrPages, setOcrPages] = useState<any[] | null>(null);
  const [isOcrRunning, setIsOcrRunning] = useState(false);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<any[]>([]);
  const [activeHitIdx, setActiveHitIdx] = useState(-1);
  const [isIndexing, setIsIndexing] = useState(false);
  const [useOCR, setUseOCR] = useState(true);
  const [useLLM, setUseLLM] = useState(false);

  // —— 文档问答（Q&A）
  const [qaQuestion, setQaQuestion] = useState('');
  const [qaAnswer, setQaAnswer] = useState<any | null>(null);
  const [qaHits, setQaHits] = useState<any[]>([]);
  const [qaLoading, setQaLoading] = useState(false);

  // 叠加 overlay（搜索 + 问答证据）
  const overlays = useMemo(() => {
    return [...hits, ...qaHits];
  }, [hits, qaHits]);

  // 稳定 file 传参，避免 Document 被误重建
  const docFile = useMemo(() => {
    const url = proxyUrl || file || '';
    return url ? { url } : null;
  }, [proxyUrl, file]);

  // 固定不变的 Document options
  const docOptions = useMemo(() => ({
    disableAutoFetch: true,
    disableStream: true,
    stopAtErrors: true,
    cMapUrl: `${ORIGIN}/pdfjs/cmaps/`,
    cMapPacked: true,
    standardFontDataUrl: `${ORIGIN}/pdfjs/standard_fonts/`,
  }), []);

  // 拉取文件列表并定位 slug
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const r = await fetch('/api/pdfs', { cache: 'no-store' });
        const data = await r.json();
        const items: any[] = data.items || [];

        const item =
        items.find((x) => String(x.id) === slugStr) ||
        items.find((x) => String(x.file) === slugStr) ||
        items.find((x) => String(x.file?.split('/').pop()) === slugStr);

        const f = item?.file || null;
        if (!mounted) return;

        // 重置文档状态
        pdfProxy.current = null;
        setDocReady(false);
        setFile(f);
        setDocKey((k) => k + 1); // 切文件时重挂载 Document

        if (f) {
          const name = f.split('/').pop()!;
          setProxyUrl(`/api/proxy?f=${encodeURIComponent(name)}`);

          // 恢复 OCR 缓存
          try {
            const OCR_BASE = 'http://127.0.0.1:8000';
            const url = `${OCR_BASE}/ocr_cache?pdf_name=${encodeURIComponent(name)}&dpi=${OCR_PARAMS.dpi}&tile=${OCR_PARAMS.tile}&overlap=${OCR_PARAMS.overlap}`;
            const rr = await fetch(url);
            if (mounted && rr.ok) {
              const cached = await rr.json();
              setOcrPages(cached);
            } else {
              setOcrPages(null);
            }
          } catch {
            setOcrPages(null);
          }
        } else {
          setProxyUrl(null);
          setOcrPages(null);
        }

        // 清理页码/状态
        setPageNumber(1);
        setNumPages(0);
        setReadyPages(new Set());
        setHits([]);
        setActiveHitIdx(-1);

        // 清理 QA 状态
        setQaQuestion('');
        setQaAnswer(null);
        setQaHits([]);
      } catch (e) {
        console.error('load /api/pdfs failed:', e);
      }
    })();
    return () => { mounted = false; };
  }, [slugStr]);

  // —— Document 看门狗：加载超时则强制重挂载（偶发卡“文档加载中…”）
  useEffect(() => {
    if (!docFile) return;
    if (docReady) return;
    const t = setTimeout(() => {
      if (!docReady) {
        setDocKey((k) => k + 1);
      }
    }, 2500);
    return () => clearTimeout(t);
  }, [docFile, docReady]);

  // Document 加载成功 → 文档就绪（react-pdf v6/v7：回调参数就是 PDFDocumentProxy）
  const onLoadSuccess = (pdf: any) => {
    pdfProxy.current = pdf;
    setNumPages(pdf.numPages);
    setDocReady(true);
  };
  const onLoadError = (e: any) => {
    console.error('onLoadError', e);
    pdfProxy.current = null;
    setDocReady(false);
  };
  const onSourceError = (e: any) => {
    console.error('onSourceError', e);
    pdfProxy.current = null;
    setDocReady(false);
  };

  // 渲染成功/失败：记录尺寸、解锁，并清理渲染超时定时器
  const clearRenderTimer = () => {
    if (renderStallTimer.current) {
      clearTimeout(renderStallTimer.current);
      renderStallTimer.current = null;
    }
  };

  const onRenderSuccess = (pnum: number) => () => {
    const container = document.getElementById(`page-${pnum}`);
    const cv = container?.querySelector('canvas') as HTMLCanvasElement | null;

    const w = cv?.clientWidth || 0;
    const h = cv?.clientHeight || 0;
    const prev = pageCssSizeRef.current.get(pnum);
    const sizeChanged = !prev || prev.w !== w || prev.h !== h;
    if (w && h && sizeChanged) {
      pageCssSizeRef.current.set(pnum, { w, h });
    }

    setReadyPages((prevSet) => {
      if (!prevSet.has(pnum) || sizeChanged) {
        const next = new Set(prevSet);
        next.add(pnum);
        return next;
      }
      return prevSet;
    });

    clearRenderTimer();
    setIsRendering(false);
  };

  const onRenderError = (e?: any) => {
    console.warn('onRenderError', e);
    clearRenderTimer();
    setIsRendering(false);
  };

  const isPageReady = (p: number) => {
    return readyPages.has(p) && !!pageCssSizeRef.current.get(p);
  };

  const clampPage = (p: number) => Math.max(1, Math.min(numPages || 1, p));

  // 仅在“就绪 + 不在渲染中 + 有 PDF 实例”时，才真正切页
  useEffect(() => {
    if (!docReady) return;
    if (pendingPage == null) return;
    if (isRendering) return;
    if (!pdfProxy.current) return;

    const next = clampPage(pendingPage);
    setPageNumber(next);
    setIsRendering(true);
    setReadyPages(new Set());
    setPendingPage(null);

    // —— 渲染锁超时保护：4s 未解锁则自动解锁
    clearRenderTimer();
    renderStallTimer.current = setTimeout(() => {
      console.warn('[render] stall detected, unlocking.');
      setIsRendering(false);
    }, 4000);
  }, [pendingPage, isRendering, docReady, numPages]);

  // 搜索/下一处在未就绪时先排队
  useEffect(() => {
    if (!docReady) return;
    if (queuedJump == null) return;
    if (isRendering) return;
    if (!pdfProxy.current) return;

    setPendingPage(clampPage(queuedJump));
    setQueuedJump(null);
  }, [queuedJump, docReady, isRendering, numPages]);

  const requestPage = (target: number) => {
    setPendingPage(clampPage(target));
  };

  // OCR 像素 → 当前 canvas CSS
  const mapBox = (page: number, box: { x: number; y: number; w: number; h: number }) => {
    const ocrMap = new Map((ocrPages || []).map((p: any) => [p.page, p]));
    const ocr = ocrMap.get(page);
    const css = pageCssSizeRef.current.get(page);
    if (!ocr || !css) {
      return { left: box.x, top: box.y, width: box.w, height: box.h };
    }
    const sx = css.w / ocr.w;
    const sy = css.h / ocr.h;
    return { left: box.x * sx, top: box.y * sy, width: box.w * sx, height: box.h * sy };
  };

  // 触发 OCR
  const runOCR = async (force = false) => {
    if (!file) return;
    setIsOcrRunning(true);
    try {
      const OCR_BASE = 'http://127.0.0.1:8000';
      const base = typeof window !== 'undefined' ? window.location.origin : '';
      const url = base + (proxyUrl || file);
      const name = file.split('/').pop()!;
      const body = { pdf_url: url, pdf_name: name, force, ...OCR_PARAMS };

      const resp = await fetch(`${OCR_BASE}/ocr_pdf`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const data = await resp.json();
      setOcrPages(prev => {
        const map = new Map<number, any>();
        (prev || []).forEach(p => map.set(p.page, p));
        (data || []).forEach((p: any) => map.set(p.page, p));
        return Array.from(map.values()).sort((a, b) => a.page - b.page);
      });
    } catch (e) {
      console.error(e);
      alert('OCR 服务请求失败，请确认 127.0.0.1:8000 正在运行');
    } finally {
      setIsOcrRunning(false);
    }
  };

  function rankHits(items: any[]) {
    return items.slice().sort((a, b) => (b.score || 0) - (a.score || 0));
  }

  const performSearch = async () => {
    if (!query.trim()) {
      setHits([]);
      return;
    }
    if (useOCR && !ocrPages) {
      alert('请先执行 OCR 或等待缓存加载');
      return;
    }

    setIsIndexing(true);
    const q = normalize(query);
    let terms = expandSynonyms(q);

    if (useLLM && file) {
      try {
        const OCR_BASE = 'http://127.0.0.1:8000';
        const name = file.split('/').pop()!;
        const r = await fetch(`${OCR_BASE}/synonyms`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query, pdf_name: name })
        });
        if (r.ok) {
          const syn = await r.json();
          const extra = [
            ...(syn.synonyms || []),
            ...(syn.abbreviations || []),
            ...(syn.english || [])
          ].map((s: string) => normalize(s));
          terms = Array.from(new Set<string>([...terms, ...extra].filter(Boolean)));
        }
      } catch (e) {
        console.warn('LLM 同义词失败：', e);
      }
    }

    const allHits: any[] = [];
    if (useOCR && ocrPages) {
      for (const p of ocrPages) {
        const heights = p.hits.map((h: any) => h.box.h);
        const sortedH = heights.slice().sort((a: number, b: number) => a - b);
        const h90 = sortedH.length ? sortedH[Math.max(0, Math.floor(sortedH.length * 0.9) - 1)] : 0;

        const rows: Record<string, number> = {};
        p.hits.forEach((h: any) => {
          const key = Math.round(h.box.y / 5);
          rows[key] = (rows[key] || 0) + 1;
        });
        const rowDensity = (y: number) => rows[Math.round(y / 5)] || 1;

        p.hits.forEach((h: any, idx: number) => {
          const txt = normalize(h.text);
          if (!txt) return;
          const include = terms.some((t) => txt.includes(t));
          if (!include) return;

          const isTitle = h.box.h >= h90;
          const isTableish = rowDensity(h.box.y) >= 12;
          const score = 1 + (isTableish ? 1 : 0) + (isTitle ? 3 : 0);

          allHits.push({
            page: p.page,
            index: idx,
            text: h.text,
            score,
            box: h.box,
            source: 'ocr',
            rawConf: h.conf
          });
        });
      }
    }

    const ranked = rankHits(allHits);
    setHits(ranked);

    if (ranked.length) {
      setActiveHitIdx(0);
      const top = ranked[0];
      requestAnimationFrame(() => {
        if (!docReady || !pdfProxy.current) setQueuedJump(top.page);
        else setPendingPage(top.page);
      });
    } else {
      setActiveHitIdx(-1);
    }

    setIsIndexing(false);
  };

  const gotoNext = () => {
    if (!hits.length) return;
    const next = (activeHitIdx + 1) % hits.length;
    setActiveHitIdx(next);
    const h = hits[next];
    requestAnimationFrame(() => {
      if (!docReady || !pdfProxy.current) setQueuedJump(h.page);
      else setPendingPage(h.page);
    });
  };

  // —— 文档问答
  const askQA = async () => {
    if (!file || !qaQuestion.trim()) return;
    try {
      setQaLoading(true);
      setQaAnswer(null);
      setQaHits([]);

      const name = file.split('/').pop()!;
      const OCR_BASE = 'http://127.0.0.1:8000';
      const r = await fetch(`${OCR_BASE}/qa`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ pdf_name: name, question: qaQuestion, top_k: 80, window: 2 })
      });
      const data = await r.json();
      setQaAnswer(data);

      const ev = data?.evidence || [];
      const mapped = ev.map((e: any, i: number) => ({
        source: 'qa',
        page: Number(e.page || 1),
                                                    index: i,
                                                    text: e.text || '',
                                                    score: (data?.confidence ?? 0.5) + 1, // 让 QA 证据靠前一些
                                                    box: e.box || {x:0,y:0,w:0,h:0}
      }));
      setQaHits(mapped);

      if (mapped.length) {
        const top = mapped[0];
        requestAnimationFrame(() => {
          if (!docReady || !pdfProxy.current) setQueuedJump(top.page);
          else setPendingPage(top.page);
        });
      }
    } catch (e) {
      console.error(e);
      alert('QA 请求失败');
    } finally {
      setQaLoading(false);
    }
  };

  const locateEvidence = (page: number, idx: number) => {
    requestAnimationFrame(() => {
      if (!docReady || !pdfProxy.current) setQueuedJump(Number(page));
      else setPendingPage(Number(page));
    });
      // 等一会儿再滚动到 overlay
      setTimeout(() => {
        const el = document.getElementById(`hit-qa-${page}-${idx}`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 250);
  };

  // 组件卸载：清理渲染超时定时器
  useEffect(() => {
    return () => {
      if (renderStallTimer.current) clearTimeout(renderStallTimer.current);
    };
  }, []);

  // —— 渲染

  if (!file) {
    return (
      <div className="card">
      未找到文件。返回 <Link href="/">列表</Link>
      </div>
    );
  }

  return (
    <div className="card" style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 380px) 1fr', gap: 16 }}>
    <aside style={{ borderRight: '1px solid #1d2230', paddingRight: 12, overflow: 'auto', maxHeight: '80vh' }}>
    <div className="toolbar" style={{ marginBottom: 12 }}>
    <Link href="/" className="btn">← 返回</Link>
    </div>

    <h3 style={{ marginTop: 0 }}>搜索</h3>
    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
    <input type="checkbox" checked={useOCR} onChange={(e) => setUseOCR(e.target.checked)} />
    <span>使用 OCR（推荐）</span>
    </label>

    {useOCR ? (
      <div className="row" style={{ marginTop: 8, gap: 8, flexWrap: 'wrap' }}>
      <button className="btn" onClick={() => runOCR(false)} disabled={isOcrRunning}>
      {isOcrRunning ? 'OCR 处理中…' : '执行 OCR（可用缓存）'}
      </button>
      <button className="btn" onClick={() => runOCR(true)} disabled={isOcrRunning}>
      清除并重建
      </button>
      <span className="pill">{ocrPages ? `已索引 ${ocrPages.length} 页` : '未索引'}</span>
      </div>
    ) : null}

    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
    <input type="checkbox" checked={useLLM} onChange={(e) => setUseLLM(e.target.checked)} />
    <span>LLM 同义词增强</span>
    </label>

    <div className="row" style={{ marginTop: 12 }}>
    <input
    className="input"
    placeholder="输入元器件，如：油门踏板 / APS"
    value={query}
    onChange={(e) => setQuery(e.target.value)}
    />
    <button className="btn" onClick={performSearch} disabled={isIndexing || (useOCR && !ocrPages)}>
    {isIndexing ? '索引中…' : '查找'}
    </button>
    </div>

    <div className="muted" style={{ marginTop: 8 }}>
    同义词已启用：油门踏板⇄踏板位置传感器⇄Accelerator Pedal Sensor⇄APS 等{useLLM ? '（含 LLM 增强）' : ''}
    </div>

    <div className="row" style={{ marginTop: 12, justifyContent: 'space-between' }}>
    <span className="badge">匹配：{hits.length}</span>
    <button className="btn" onClick={gotoNext} disabled={!hits.length || isRendering}>下一处</button>
    </div>

    <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
    {hits.map((h, i) => (
      <div
      key={`${h.source}-${h.page}-${h.index}`}
      className="result"
      onClick={() => {
        setActiveHitIdx(i);
        requestAnimationFrame(() => {
          if (!docReady || !pdfProxy.current) setQueuedJump(h.page);
          else setPendingPage(h.page);
        });
      }}
      >
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span>第 {h.page} 页</span>
      <span className="pill">score {Number(h.score || 0).toFixed(2)}</span>
      </div>
      <div className="muted" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={h.text}>
      {h.text}
      </div>
      </div>
    ))}
    </div>

    <hr style={{border:'none', borderTop:'1px solid #1d2230', margin:'12px 0'}} />

    <h3 style={{ marginTop: 0 }}>问答</h3>
    <div className="row" style={{ marginTop: 8 }}>
    <input
    className="input"
    placeholder="例如：油门踏板连接到 ECU 的哪些针脚？"
    value={qaQuestion}
    onChange={(e) => setQaQuestion(e.target.value)}
    />
    <button className="btn" onClick={askQA} disabled={qaLoading}>
    {qaLoading ? '思考中…' : '问一下'}
    </button>
    </div>

    {qaAnswer ? (
      <div className="card" style={{ marginTop: 10 }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>答案</div>
      <div style={{ whiteSpace: 'pre-wrap' }}>{qaAnswer.answer || '—'}</div>
      <div className="muted" style={{ marginTop: 6 }}>
      置信度：{Math.round((qaAnswer.confidence ?? 0.5) * 100)}%
      {qaAnswer.pins?.length ? <> · 针脚：{qaAnswer.pins.join(' / ')}</> : null}
      </div>

      {Array.isArray(qaAnswer.evidence) && qaAnswer.evidence.length ? (
        <div style={{ marginTop: 10, display:'grid', gap:6 }}>
        {qaAnswer.evidence.map((e: any, i: number) => (
          <div key={`ev-${i}`} className="result" style={{ padding: 8 }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <span>证据 P{e.page}</span>
          <div className="row">
          <button className="btn" onClick={() => locateEvidence(Number(e.page), i)}>定位</button>
          </div>
          </div>
          <div className="muted" style={{ whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
          {e.text || '—'}
          </div>
          </div>
        ))}
        <div className="row" style={{ marginTop: 6 }}>
        <button className="btn" onClick={() => { setQaHits([]); setQaAnswer(null); }}>清除标注</button>
        </div>
        </div>
      ) : null}
      </div>
    ) : null}
    </aside>

    <main>
    <div className="toolbar" style={{ marginBottom: 8, gap: 8, flexWrap: 'wrap' }}>
    <button className="btn" onClick={() => setScale((s) => Math.max(0.6, s - 0.2))} disabled={isRendering}>缩小</button>
    <button className="btn" onClick={() => setScale((s) => Math.min(3, s + 0.2))} disabled={isRendering}>放大</button>
    <button className="btn" onClick={() => setPendingPage(pageNumber - 1)} disabled={pageNumber <= 1}>上一页</button>
    <button className="btn" onClick={() => setPendingPage(pageNumber + 1)} disabled={pageNumber >= numPages}>下一页</button>
    <span className="pill">第 {pageNumber} / {numPages} 页</span>
    </div>

    <div style={{ height: '80vh', overflow: 'auto', border: '1px solid #1d2230', borderRadius: 12, padding: 8 }}>
    <Document
    key={`doc-${docKey}-${slugStr}`}  // ← 强制重挂载用
    file={docFile}
    onLoadSuccess={onLoadSuccess}
    onLoadError={onLoadError}
    onSourceError={onSourceError}
    options={docOptions}
    loading={<div>加载 PDF…</div>}
    >
    {/* 显示条件更宽松：只要文档就绪且有页数，就挂 Page */}
    {docReady && numPages > 0 ? (
      <div id={`page-${pageNumber}`} style={{ position: 'relative', margin: '0 auto 12px', width: 'fit-content' }}>
      <Page
      key={`${pageNumber}-${Math.round(scale * 100)}`}
      pageNumber={pageNumber}
      scale={scale}
      renderTextLayer={false}
      renderAnnotationLayer={false}
      onRenderSuccess={onRenderSuccess(pageNumber)}
      onRenderError={onRenderError}
      />
      {/* 页未 ready 时不画 overlay；每页最多 300 个框 */}
      {isPageReady(pageNumber) ? (
        overlays
        .filter((h) => h.page === pageNumber)
        .slice(0, 300)
        .map((h) => {
          const css = (h.source === 'ocr' || h.source === 'qa')
          ? mapBox(h.page, h.box)
          : { left: h.box.x, top: h.box.y, width: h.box.w, height: h.box.h };
          return (
            <div
            key={`ovl-${h.source}-${h.page}-${h.index}`}
            id={`hit-${h.source}-${h.page}-${h.index}`}
            style={{
              position: 'absolute',
              border: '2px solid #4da3ff',
              background: 'rgba(77,163,255,.15)',
                  left: css.left + 'px',
                  top: css.top + 'px',
                  width: css.width + 'px',
                  height: css.height + 'px',
                  pointerEvents: 'none'
            }}
            />
          );
        })
      ) : null}
      </div>
    ) : (
      <div style={{ padding: 24, textAlign: 'center' }}>文档加载中…</div>
    )}
    </Document>
    </div>
    </main>
    </div>
  );
}
