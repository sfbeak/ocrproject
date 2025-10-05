# -*- coding: utf-8 -*-
import os, re, json
from pathlib import Path
from typing import List, Dict, Any, Tuple
from dotenv import load_dotenv

# ------- 环境加载 -------
def _load_env_safely():
    here = Path(__file__).resolve().parent
    for p in [here / ".env", here.parent / ".env"]:
        if p.exists():
            load_dotenv(dotenv_path=p, override=True)
            break
    else:
        load_dotenv(override=True)
    os.environ.setdefault("CACHE_DIR", str(Path(os.environ.get("DATA_DIR", here / "data")) / "cache"))

_load_env_safely()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in .env")

from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY, base_url="https://api.deepseek.com")
QA_MODEL = "deepseek-chat"

ROOT = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get("CACHE_DIR", ROOT / "data" / "cache")).resolve()

# ------- 工具 -------
def _norm_name(s: str) -> str:
    return (s or "").replace("/", "_").replace("\\", "_")

def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())

def _list_ocr_pages(pdf_name: str) -> Tuple[List[Dict[str, Any]], int]:
    safe = _norm_name(pdf_name)
    root = CACHE_DIR / safe
    if not root.exists():
        return [], 0
    cands = sorted(root.glob("page_*_*_rapidocr.json"))
    if not cands:
        return [], 0
    def parse_dpi(p: Path) -> int:
        stem = p.stem  # page_0001_500_rapidocr
        parts = stem.split("_")
        return int(parts[2]) if len(parts) >= 3 else 0
    by_dpi: Dict[int, List[Path]] = {}
    for j in cands:
        by_dpi.setdefault(parse_dpi(j), []).append(j)
    dpi_used = 500 if 500 in by_dpi else sorted(by_dpi.keys())[-1]
    pages = []
    for jp in sorted(by_dpi[dpi_used]):
        try:
            obj = json.loads(jp.read_text("utf-8"))
            hits = obj.get("hits") or obj.get("lines") or []
            pages.append({
                "page": int(obj.get("page") or 0),
                "w": float(obj.get("w") or 0),
                "h": float(obj.get("h") or 0),
                "hits": hits
            })
        except Exception:
            continue
    return pages, dpi_used

# ------- 问题切词（包含针脚/连接器模式） -------
def _terms_from_question(q: str) -> List[str]:
    s_low = (q or "").lower()
    s_up  = (q or "").upper()
    parts = re.findall(r"[\u4e00-\u9fa5]{1,}|[a-z0-9]+", s_low)
    toks = {p for p in parts if len(p) >= 2}
    # 典型模式
    toks.update(re.findall(r"[A-Z]{1,3}-?\d{1,3}", s_up))    # A12 / B-15 / X1 / P07
    toks.update(re.findall(r"C\d+(?:-\d+)?", s_up))          # C123-1 / C456
    toks.update(re.findall(r"[JPX]\d{1,3}(?:-\d+)?", s_up))  # J6 / P12 / X1
    commons = {'ecu','aps','can','针','脚','端子','连接','接地','供电','信号'}
    toks.update({t for t in commons if (t in s_low or t in s_up)})
    return sorted(toks)

# ------- 表格行提取：按行密度聚类 -------
def _group_table_rows(p: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    """
    把同页 hits（OCR 行）按 y 聚成行：
      - 每 5px 归一到一个 row bin
      - 行里条目数多、包含大量数字/短 token → 更像表格
    返回：每个元素是一行（若干 OCR 命中）
    """
    bins: Dict[int, List[Dict[str, Any]]] = {}
    for i, h in enumerate(p.get("hits", [])):
        box = h.get("box") or {}
        y = float(box.get("y", 0.0))
        key = int(round(y / 5.0))
        item = {
            "index": i,
            "text": str(h.get("text") or ""),
            "box": {k: float(box.get(k, 0.0)) for k in ("x","y","w","h")}
        }
        bins.setdefault(key, []).append(item)
    rows = []
    for key in sorted(bins.keys()):
        row = sorted(bins[key], key=lambda t: t["box"]["x"])
        # 行里至少 4 条、且包含较多数字/短token，视为“表格行”
        txt = "".join(x["text"] for x in row)
        digits = len(re.findall(r"[0-9A-Za-z\-]+", txt))
        if len(row) >= 4 and digits >= 4:
            rows.append(row)
    return rows

def _harvest_table_context(pages: List[Dict[str, Any]], terms: List[str]) -> List[Tuple[int, str, List[Dict[str, Any]]]]:
    """
    返回命中问题术语的“表格行上下文”：
      -> [(page, ctx_text, evidence_items), ...]
    """
    res = []
    tset = { _normalize(t) for t in terms if t }
    for p in pages:
        rows = _group_table_rows(p)
        for row in rows:
            line_txt = " | ".join(x["text"] for x in row)
            norm_line = _normalize(line_txt)
            if any(t in norm_line for t in tset):
                res.append((int(p["page"]), line_txt, row))
    return res

# ------- 普通行打分（保留你之前的启发式） -------
def _score_entries(terms: List[str], entries: List[Dict[str, Any]], pages_by_num: Dict[int, Dict[str, Any]]) -> List[Tuple[float, Dict[str, Any]]]:
    tset = { _normalize(t) for t in terms if t }
    scored = []
    for e in entries:
        t = _normalize(e["text"])
        if not t:
            continue
        hit_cnt = sum(1 for kw in tset if kw in t)
        if hit_cnt == 0:
            continue
        page = pages_by_num.get(e["page"], {})
        heights = [h.get("box",{}).get("h",0) for h in page.get("hits",[])]
        heights = sorted(float(x) for x in heights if x)
        h90 = heights[max(0, int(len(heights)*0.9)-1)] if heights else 0.0
        is_title = e["box"]["h"] >= h90
        rows = {}
        for h in page.get("hits",[]):
            by = h.get("box",{}).get("y",0)
            rows[round(by/5)] = rows.get(round(by/5),0)+1
        row_den = rows.get(round(e["box"]["y"]/5),1)
        is_tableish = row_den >= 12
        score = hit_cnt + (3 if is_title else 0) + (1 if is_tableish else 0)
        scored.append((float(score), e))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored

def _collect_entries(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    entries = []
    for p in pages:
        for i, h in enumerate(p["hits"]):
            txt = h.get("text") or ""
            box = h.get("box") or {}
            if not txt or not all(k in box for k in ("x","y","w","h")):
                continue
            entries.append({
                "page": int(p["page"]),
                "index": int(i),
                "text": str(txt),
                "box": {k: float(box[k]) for k in ("x","y","w","h")}
            })
    return entries

def _build_context_from_rows(rows: List[Tuple[int, str, List[Dict[str, Any]]]], limit_pages:int=6) -> Tuple[str, List[Dict[str, Any]]]:
    """
    rows: [(page, line_text, row_items)]
    组装上下文：每页最多取若干行；证据用 row_items
    """
    by_page: Dict[int, List[Tuple[str, List[Dict[str, Any]]]]] = {}
    for pg, line_txt, items in rows:
        by_page.setdefault(pg, []).append((line_txt, items))

    chunks = []
    evidence = []
    for pg in sorted(by_page.keys())[:limit_pages]:
        lines = by_page[pg][:30]  # 每页最多 30 行
        chunks.append(f"[Page {pg} · 表格]\n" + "\n".join(t for t,_ in lines))
        # 每行挑一个代表项作为证据（文本+框）
        for _, items in lines[:10]:
            if not items: continue
            it = items[min(0, len(items)-1)]
            evidence.append({
                "page": int(pg),
                "text": it["text"],
                "box": it["box"]
            })
    context = "\n\n".join(chunks)
    return context, evidence[:12]

def _build_context_from_entries(scored: List[Tuple[float, Dict[str, Any]]], pages_by_num: Dict[int, Dict[str, Any]], top_k:int=80, window:int=2) -> Tuple[str, List[Dict[str, Any]]]:
    chosen = [e for _, e in scored[:top_k]]
    keyset = set()
    final_spans = []
    for e in chosen:
        k = (e["page"], e["index"])
        if k in keyset: continue
        keyset.add(k)
        final_spans.append(e)
    by_page: Dict[int, List[Dict[str, Any]]] = {}
    for e in final_spans:
        by_page.setdefault(e["page"], []).append(e)
    chunks = []
    evidence_out = []
    for pg in sorted(by_page.keys()):
        page = pages_by_num.get(pg, {})
        hits = page.get("hits", [])
        idxs = sorted({i for e in by_page[pg] for i in range(max(0, e["index"]-window), min(len(hits)-1, e["index"]+window)+1)})
        texts = []
        for i in idxs:
            ht = hits[i]
            txt = str(ht.get("text") or "")
            if not txt.strip(): continue
            texts.append(txt)
        if not texts:
            continue
        chunks.append(f"[Page {pg}]\n" + "\n".join(texts[:50]))
        for e in by_page[pg]:
            evidence_out.append({
                "page": int(pg),
                "text": e["text"],
                "box": e["box"]
            })
    context = "\n\n".join(chunks)
    if len(context) > 6000:
        context = context[:6000] + "\n...[截断]"
    return context, evidence_out[:12]

# ------- 主流程 -------
def qa_over_pdf(pdf_name: str, question: str, top_k:int=80, window:int=2) -> Dict[str, Any]:
    pages, dpi_used = _list_ocr_pages(pdf_name)
    pages_by_num = {int(p["page"]): p for p in pages}
    entries = _collect_entries(pages)

    base_terms = _terms_from_question(question)
    terms = base_terms[:]  # 你也可以在这里并上 llm_synonyms 的扩展

    # ① 表格优先：命中相关表格行
    table_rows = _harvest_table_context(pages, terms)
    context, evidence = ("", [])
    if table_rows:
        context, evidence = _build_context_from_rows(table_rows, limit_pages=8)

    # ② 如果表格拿不到，再走普通行打分
    if not context.strip():
        scored = _score_entries(terms, entries, pages_by_num)
        context, evidence = _build_context_from_entries(scored, pages_by_num, top_k=top_k, window=window)

    # ③ 再兜底一层“宽松匹配”
    if not context.strip():
        qbag = set(terms)
        qbag.update(re.findall(r"[A-Za-z]{2,}", question))
        qbag.update(re.findall(r"\d{2,}", question))
        norm_qbag = { _normalize(t) for t in qbag if t }
        greedy = []
        for e in entries:
            t = _normalize(e["text"])
            if any(k in t for k in norm_qbag):
                greedy.append((1.0, e))
        if greedy:
            context, evidence = _build_context_from_entries(greedy, pages_by_num, top_k=200, window=3)

    if not context.strip():
        return {
            "answer": "未从文档中检索到相关内容，无法作答。",
            "pins": [],
            "pages": [],
            "evidence": [],
            "confidence": 0.1,
            "debug": {"pages": len(pages), "entries": len(entries), "terms": terms, "context_len": 0, "used": "none"}
        }

    sys = (
        "你是汽车电路图助手。只依据给定上下文回答连接性问题；"
        "若上下文不足，请回答“无法确定”。优先抽取针脚号、连接器编号、端子标识。"
        "输出 JSON：{answer:string, pins:string[], pages:int[], evidence:{page:int,text:string,box:{x:float,y:float,w:float,h:float}}[], confidence:number(0-1)}。"
        "不要编造坐标和页码。"
    )
    usr = (
        f"问题：{question}\n\n"
        f"上下文（可能包含针脚表/连接表的行）：\n{context}\n\n"
        f"请按 JSON 输出。"
    )

    resp = client.chat.completions.create(
        model=QA_MODEL,
        temperature=0.2,
        messages=[{"role":"system","content":sys},{"role":"user","content":usr}]
    )
    txt = resp.choices[0].message.content or ""
    try:
        data = json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, flags=re.S)
        data = json.loads(m.group(0)) if m else {"answer": txt.strip()[:200]}

    # 证据与 pages 兜底
    if not data.get("evidence"):
        data["evidence"] = evidence
    if not data.get("pages"):
        data["pages"] = sorted({int(e["page"]) for e in data.get("evidence", [])})

    pins = data.get("pins") or []
    if isinstance(pins, str):
        pins = re.split(r"[,\s;/，；]+", pins)
    data["pins"] = [str(p).strip() for p in pins if str(p).strip()]

    try:
        c = float(data.get("confidence", 0.5))
        data["confidence"] = max(0.0, min(1.0, c))
    except Exception:
        data["confidence"] = 0.5

    data.setdefault("debug", {})
    data["debug"].update({
        "pages": len(pages),
        "entries": len(entries),
        "terms": terms[:30],
        "context_len": len(context),
        "used": "tables" if table_rows else "lines"
    })
    return data
