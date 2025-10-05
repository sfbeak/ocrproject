# -*- coding: utf-8 -*-
import os, json, gc, unicodedata, io
from pathlib import Path
from typing import List, Tuple
from dotenv import load_dotenv
import numpy as np
from PIL import Image
import requests
import fitz  # PyMuPDF
from flask import Flask, request, jsonify
from flask_cors import CORS
from paddleocr import PaddleOCR
from llm_synonyms import llm_expand_synonyms, load_vocab_from_ocr_cache
from llm_qa import qa_over_pdf
# ---------------- 环境加固（保留你原有设置，不改动） ----------------
load_dotenv()
def load_env_safely():
    here = Path(__file__).resolve().parent            # ocr_server/
    candidates = [
        here / ".env",                                # ocr_server/.env（推荐）
        here.parent / ".env",                         # 项目根/.env
    ]
    loaded_from = None
    for p in candidates:
        if p.exists():
            load_dotenv(dotenv_path=p, override=True) # 覆盖空值/旧值
            loaded_from = str(p)
            break
    if not loaded_from:
        # 退一步：按当前工作目录向上搜索
        load_dotenv(override=True)
        loaded_from = "auto(find_dotenv)"

load_env_safely()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in .env")
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("CPU_NUM_THREADS", "4")
os.environ.setdefault("OMP_NUM_THREADS", "4")

# ---------------- Flask ----------------
app = Flask(__name__)
CORS(app)

# ---------------- 默认参数（保持与前端一致） ----------------
DPI_DEFAULT = 500
TILE_SIZE_DEFAULT = 1400
OVERLAP_DEFAULT = 0.12

# ---------------- 目录、路径 ----------------
ROOT_DIR  = Path(__file__).resolve().parent
PDF_DIR   = Path(os.environ.get("PDF_DIR", ROOT_DIR.parent / "pdfs")).resolve()
DATA_DIR  = Path(os.environ.get("DATA_DIR", ROOT_DIR / "data")).resolve()
CACHE_DIR = Path(os.environ.get("CACHE_DIR", DATA_DIR / "cache")).resolve()
os.environ.setdefault("CACHE_DIR", str(CACHE_DIR))

PDF_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def norm(s: str) -> str:
    # 统一 Unicode 形式，并去掉潜在路径分隔
    return unicodedata.normalize('NFC', s or '').replace(os.sep, '_')

def cache_path(pdf_name: str, dpi: int, tile: int, overlap: float) -> str:
    # 兼容你原有的“整本合并缓存”命名方式
    safe = pdf_name.replace("/", "_")
    return str((CACHE_DIR / f"{safe}__{dpi}_{tile}_{overlap}.json"))

def chunked(seq, n=10):
    for i in range(0, len(seq), n):
        yield seq[i:i+n]

def write_json_atomic(path: Path, obj: dict):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)

# ---------------- OCR 引擎（保留你原有参数） ----------------
ocr = PaddleOCR(
    lang='ch',
    use_angle_cls=True,
    det_limit_side_len=2048,  # 速度/召回折中
    use_gpu=False,
    cpu_threads=4
)

# ---------------- 工具函数（保留并小修） ----------------
def rotate_image(im: Image.Image, deg: int) -> Image.Image:
    if deg == 0:
        return im
    return im.rotate(deg, expand=True)  # 逆时针为正

def tiles(img: Image.Image, size=1400, overlap=0.12):
    # 目前未直接使用（保留）
    W, H = img.size
    dx = int(size * (1 - overlap)); dy = dx
    xs = list(range(0, max(1, W - size) + 1, dx))
    ys = list(range(0, max(1, H - size) + 1, dy))
    if W > size and (W - size) % dx != 0: xs.append(W - size)
    if H > size and (H - size) % dy != 0: ys.append(H - size)
    xs = sorted(set(xs)); ys = sorted(set(ys))
    for xi in xs:
        for yi in ys:
            yield (xi, yi), img.crop((xi, yi, xi + size, yi + size))

def safe_ocr_lines(im: Image.Image) -> list:
    try:
        res = ocr.ocr(np.array(im), cls=True)  # 期望 RGB
        if not res or not isinstance(res, list):
            return []
        lines = res[0] if len(res) else []
        return lines or []
    except Exception:
        app.logger.exception("OCR call failed")
        return []

def ocr_patch(patch: Image.Image):
    """
    针对小块做 OCR，尝试 0/90 两种角度，取 score 高的一种。
    """
    cands = []
    for rot in [0, 90]:
        imr = rotate_image(patch, rot)
        if imr.mode != "RGB":
            imr = imr.convert("RGB")  # 关键：确保 3 通道
        lines = safe_ocr_lines(imr)
        score = sum(max(0, float(t[1][1])) for t in lines) if lines else 0.0
        cands.append((score, rot, lines))
    cands.sort(key=lambda x: x[0], reverse=True)
    return cands[0]

def rotate_box_back(
    box: List[List[float]],
    rot_deg: int,
    patch_w: int,
    patch_h: int,
    offset_xy: Tuple[int, int]
):
    """
    将“在已旋转 patch 坐标系中的四点框”映射回“整页未旋转坐标系”，
    再加上 patch 左上角在整页中的偏移。
    约定：rot_deg 为逆时针角度；Pillow.rotate(deg) 同样是逆时针。
    """
    pts = np.array(box, dtype=np.float32)  # [[x',y'], ...] in ROTATED patch coords
    x0, y0 = offset_xy

    if rot_deg == 0:
        xs, ys = pts[:, 0], pts[:, 1]
    elif rot_deg == 90:
        # 逆时针90°：x' = y, y' = W - x ；反变换：x = W - y', y = x'
        xs = patch_w - pts[:, 1]
        ys = pts[:, 0]
    elif rot_deg == 180:
        # 180°：x' = W - x, y' = H - y ；反变换：x = W - x', y = H - y'
        xs = patch_w - pts[:, 0]
        ys = patch_h - pts[:, 1]
    elif rot_deg == 270:
        # 逆时针270°（=顺时针90°）：x' = H - y, y' = x ；反变换：x = y', y = H - x'
        xs = pts[:, 1]
        ys = patch_h - pts[:, 0]
    else:
        xs, ys = pts[:, 0], pts[:, 1]

    # 加回整页偏移
    xs = xs + x0; ys = ys + y0
    xmin, ymin = float(xs.min()), float(ys.min())
    xmax, ymax = float(xs.max()), float(ys.max())
    return xmin, ymin, xmax - xmin, ymax - ymin

# ---------------- 单页 OCR：按 clip 分块渲染 → OCR → 坐标还原 ----------------
def ocr_one_page(doc, page_index: int, dpi: int, tile: int, overlap: float):
    """
    返回：
    {
      'page': 1-based 页码,
      'w': 整页像素宽(基于dpi),
      'h': 整页像素高(基于dpi),
      'hits': [ {'text':str,'conf':float,'box':{'x':int,'y':int,'w':int,'h':int}}, ... ]
    }
    坐标单位：整页像素，与前端 mapBox 的 (w,h) 对齐。
    """
    page = doc.load_page(page_index)
    scale = dpi / 72.0
    rect = page.rect
    W = int(rect.width * scale)
    H = int(rect.height * scale)

    step = max(1, int(tile - tile * overlap))  # 实际步长
    mtx = fitz.Matrix(scale, scale)
    hits = []

    for y0 in range(0, H, step):
        y1 = min(y0 + tile, H)
        for x0 in range(0, W, step):
            x1 = min(x0 + tile, W)
            clip = fitz.Rect(x0/scale, y0/scale, x1/scale, y1/scale)

            try:
                # 只渲 clip，灰度、无 alpha（显著省内存）
                pix = page.get_pixmap(matrix=mtx, clip=clip, colorspace=fitz.csGRAY, alpha=False)
                im = Image.frombytes("L", (pix.w, pix.h), pix.samples)  # 灰度
            except Exception:
                fitz.TOOLS.store_shrink(1); gc.collect()
                continue

            # 单块 OCR（会转 RGB，并尝试 0/90）
            score, rot, lines = ocr_patch(im)

            for ln in (lines or []):
                try:
                    poly = ln[0]
                    txt, conf = ln[1][0], ln[1][1]
                    bx, by, bw, bh = rotate_box_back(poly, rot, im.width, im.height, (x0, y0))
                    hits.append({
                        'text': str(txt),
                        'conf': float(conf) if conf is not None else 0.0,
                        'box': {'x': int(bx), 'y': int(by), 'w': int(max(1, bw)), 'h': int(max(1, bh))}
                    })
                except Exception:
                    continue

            # 释放这一块
            del im, pix
            fitz.TOOLS.store_shrink(1); gc.collect()

    del page
    fitz.TOOLS.store_shrink(1); gc.collect()

    return {'page': page_index + 1, 'w': W, 'h': H, 'hits': hits}

# ---------------- 路由：每 10 页一批 OCR（参数不变） ----------------
@app.post('/ocr_pdf')
def ocr_pdf():
    data = request.get_json(force=True) or {}
    pdf_url   = data.get('pdf_url', '')
    pdf_name  = norm(data.get('pdf_name', 'doc.pdf'))
    dpi       = int(data.get('dpi', DPI_DEFAULT))
    tile      = int(data.get('tile', TILE_SIZE_DEFAULT))
    overlap   = float(data.get('overlap', OVERLAP_DEFAULT))
    pages_arg = data.get('pages')  # 可无；有就只处理这些页（1-based）

    # 打开 PDF（前端已做本地代理 URL）
    buf = io.BytesIO(requests.get(pdf_url, timeout=30).content)
    doc = fitz.open(stream=buf, filetype='pdf')
    total = doc.page_count

    # 需要处理的页（0-based 下标）
    if pages_arg:
        pages_idx = sorted({max(0, min(total-1, int(p)-1)) for p in pages_arg})
    else:
        pages_idx = list(range(total))

    # 缓存目录（与原有命名保持兼容）
    root = (CACHE_DIR / pdf_name)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'meta.json').write_text(json.dumps({'name': pdf_name}, ensure_ascii=False), 'utf-8')

    # 若 force，清理旧页与旧合并缓存
    if data.get('force'):
        for j in root.glob(f'page_*_{dpi}_rapidocr.json'):
            try:
                j.unlink()
            except:
                pass
        try:
            Path(cache_path(pdf_name, dpi, tile, overlap)).unlink()
        except Exception:
            pass

    # === 分批 OCR：每 10 页一批 ===
    for batch in chunked(pages_idx, 10):
        for i in batch:
            out = ocr_one_page(doc, i, dpi=dpi, tile=tile, overlap=overlap)
            jpath = root / f'page_{i+1:04d}_{dpi}_rapidocr.json'
            write_json_atomic(jpath, out)

            # 当页完成即释放
            del out
            fitz.TOOLS.store_shrink(1)
            gc.collect()

        # 批尾再收一次
        fitz.TOOLS.store_shrink(1); gc.collect()

    # 统一按页序读回（也作为返回值）
    result_pages = []
    for i in pages_idx:
        jpath = root / f'page_{i+1:04d}_{dpi}_rapidocr.json'
        if jpath.exists():
            try:
                result_pages.append(json.loads(jpath.read_text('utf-8')))
            except Exception:
                continue

    # 写“整本合并缓存”（兼容 /ocr_cache）
    combine_path = Path(cache_path(pdf_name, dpi, tile, overlap))
    write_json_atomic(combine_path, result_pages)

    doc.close()
    fitz.TOOLS.store_shrink(1); gc.collect()
    return jsonify(result_pages)

# ---------------- 读取整本合并缓存（兼容旧前端） ----------------
@app.get("/ocr_cache")
def ocr_cache_get():
    pdf_name = request.args.get("pdf_name")
    dpi = int(request.args.get("dpi", DPI_DEFAULT))
    tile = int(request.args.get("tile", TILE_SIZE_DEFAULT))
    overlap = float(request.args.get("overlap", OVERLAP_DEFAULT))
    if not pdf_name:
        return jsonify({"error": "pdf_name required"}), 400
    p = cache_path(pdf_name, dpi, tile, overlap)
    if not os.path.exists(p):
        return jsonify({"error": "not_found"}), 404
    with open(p, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))

# ---------------- LLM 同义词（保留） ----------------
@app.post("/synonyms")
def synonyms():
    data = request.get_json(force=True)
    query = str(data.get("query", "")).strip()
    pdf_name = str(data.get("pdf_name", "")).strip()
    if not query:
        return jsonify({"synonyms": [], "abbreviations": [], "english": []})

    vocab = load_vocab_from_ocr_cache(pdf_name) if pdf_name else []
    res = llm_expand_synonyms(query, vocab, pdf_key=(pdf_name or "global"))
    return jsonify(res)

@app.get("/cache_stats")
def cache_stats():
    pdf_name = request.args.get("pdf_name")
    dpi = int(request.args.get("dpi", DPI_DEFAULT))
    tile = int(request.args.get("tile", TILE_SIZE_DEFAULT))
    overlap = float(request.args.get("overlap", OVERLAP_DEFAULT))
    if not pdf_name:
        return jsonify({"error": "pdf_name required"}), 400

    root = (CACHE_DIR / norm(pdf_name))
    if not root.exists():
        return jsonify({"error": "cache_dir_not_found"}), 404

    stats = []
    for j in sorted(root.glob(f"page_*_{dpi}_rapidocr.json")):
        try:
            obj = json.loads(j.read_text('utf-8'))
            stats.append({"page": obj.get("page"), "hits": len(obj.get("hits") or [])})
        except Exception:
            continue

    # 合并缓存（/ocr_cache 用的那份）
    combine = Path(cache_path(pdf_name, dpi, tile, overlap))
    comb = None
    if combine.exists():
        try:
            arr = json.loads(combine.read_text('utf-8'))
            comb = {
                "pages_in_file": len(arr),
                "total_hits_in_file": sum(len(p.get("hits") or []) for p in arr)
            }
        except Exception:
            comb = {"error": "combine_read_failed"}

    return jsonify({
        "pages_indexed": len(stats),
        "nonzero_pages": sum(1 for s in stats if s["hits"] > 0),
        "total_hits": sum(s["hits"] for s in stats),
        "first_10": stats[:10],
        "combine": comb
    })

@app.post("/qa")
def qa_endpoint():
    data = request.get_json(force=True) or {}
    pdf_name = str(data.get("pdf_name") or "").strip()
    question = str(data.get("question") or "").strip()
    top_k = int(data.get("top_k", 80))
    window = int(data.get("window", 2))
    if not pdf_name or not question:
        return jsonify({"error": "pdf_name & question required"}), 400
    try:
        res = qa_over_pdf(pdf_name, question, top_k=top_k, window=window)
        return jsonify(res)
    except Exception as e:
        app.logger.exception("QA failed")
        return jsonify({"error": "qa_failed", "detail": str(e)}), 500

# ---------------- 启动 ----------------
if __name__ == "__main__":
    # 提醒：若要更省内存，可在启动脚本里设置：
    # export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 MALLOC_ARENA_MAX=2
    app.run(host="127.0.0.1", port=8000, debug=False)
