# PDF 电路图检索 & 问答（OCR 增强）

面向 **整页位图 / 竖排中文 / 微小字体** 的电路图检索系统。  
支持单文档关键词搜索（标题/表格/正文相关度排序）、逐处跳转高亮、PaddleOCR 索引、以及 **文档内连接性问答（Q&A）**。

---

## ✨ 功能

- **PDF 展示**：列表页自动扫描 `pdfs/`；详情页分页渲染、缩放、翻页，加载看门狗避免“文档加载中…”卡住  
- **关键词搜索（单 PDF）**：OCR/非 OCR 两路；优先级：**标题 > 表格 > 正文**；支持“下一处”跳转  
- **智能定位**：将 OCR 坐标映射到当前 Page Canvas，正确缩放/偏移后高亮  
- **OCR 增强**：PaddleOCR（CPU），500 DPI + 切块（tile/overlap），**每 10 页一批**落盘缓存  
- **LLM 同义词**：可选调用（APS / Accelerator Pedal Sensor / 踏板位置传感器 等）  
- **文档问答（Q&A）**：**表格优先**构造上下文（针脚/连接表），返回答案 + 置信度 + 证据框；一键“定位”证据

---

## 🧱 目录结构

```
/                       # Next.js 14 (App Router)
├─ app/
│  ├─ page.tsx              # 列表页：扫描 /pdfs
│  └─ view/[slug]/page.tsx  # 详情页：PDF 渲染/搜索/QA/overlay
├─ app/api/
│  ├─ pdfs/route.ts         # 列表 API：扫描本地 /pdfs
│  └─ proxy/route.ts        # 将 /pdfs/<name>.pdf 代理为可访问 URL
├─ public/pdfjs/            # pdf.worker + cmaps + standard_fonts
├─ pdfs/                    # 放你的 PDF
└─ ocr_server/
   ├─ main.py               # Flask 后端：OCR/缓存/同义词/QA 路由
   ├─ llm_synonyms.py       # LLM 同义词（OpenAI 兼容）
   ├─ llm_qa.py             # QA（表格优先 + 证据返回）
   └─ data/cache/<PDF_NAME>/
         page_0001_500_rapidocr.json
         page_0002_500_rapidocr.json
         ...
```

---

## 🛠 技术选型 & 决策

- **渲染**：`react-pdf` + `pdf.js`（静态资源置于 `public/pdfjs/`），解决位图型 PDF 的展示与缩放  
- **稳定性**：单页渲染 + 渲染锁 + 看门狗（2.5s 未就绪重挂载，4s 渲染超时解锁）  
- **OCR**：PaddleOCR（CPU），`dpi=500 / tile=1400 / overlap=0.12`；**10 页一批**写缓存，控制内存峰值  
- **排序启发式**：标题（行高 ≥ 90 分位）加权 +3；表格（同行密度高）加权 +1  
- **QA**：先抽 **表格行**（针脚/连接/端子表），无表格命中再退回普通行，最后做宽松兜底；LLM 仅基于上下文，不足则返回“无法确定”  
- **同义词**：规则库 + LLM 扩展（可关）；避免漏查 APS/TCM/ECU 的跨语言/缩写变体

---

## ⚙️ 环境

- Node.js ≥ 18  
- Python 3.10/3.11  
- Arch / Ubuntu / macOS 均可

### 依赖安装

**前端**
```bash
# 项目根
pnpm install   # 或 npm i / yarn
```

**pdf.js 静态资源（必须）**
```
public/pdfjs/
  ├─ pdf.worker.min.js
  ├─ cmaps/              # Adobe-GB1-UCS2.bcmap 等
  └─ standard_fonts/
```
> 可从 `node_modules/pdfjs-dist/` 复制：
> - `build/pdf.worker.min.js` → `public/pdfjs/`
> - `cmaps/` → `public/pdfjs/cmaps/`
> - `standard_fonts/` → `public/pdfjs/standard_fonts/`

**后端（Flask）**
```bash
cd ocr_server
# 可选：conda create -n ocrproject python=3.11 -y && conda activate ocrproject
pip install -r requirements.txt
```

`ocr_server/requirements.txt`
```
flask
flask-cors
paddleocr==2.7.0.3
PyMuPDF
numpy
Pillow
python-dotenv
openai
requests
```

**LLM Key**
- 在 **项目根** 或 **`ocr_server/`** 放置 `.env`（任意其一即可被加载）：
```
OPENAI_API_KEY=sk-xxxxxxx
```

---

## ▶️ 运行

**后端**
```bash
cd ocr_server
python main.py
# http://127.0.0.1:8000
```

**前端**
```bash
# 项目根
pnpm dev    # 或 npm run dev / yarn dev
# http://localhost:3000
```

---

## 🧭 使用说明

1. 打开首页 → 自动列出 `pdfs/` 下的 PDF  
2. 点击“打开”进入详情页  
3. （建议）先点击 **“执行 OCR（可用缓存）”**  
   - 首次对大文档较慢；**每 10 页一批**写缓存，可多次点击累积  
4. **搜索**
   - 输入元件名/缩写（如：`油门踏板`/`APS`/`C123-1`）→ “查找”  
   - 可勾选“LLM 同义词增强”扩大召回  
   - “下一处”按相关度跳转；左侧点击某一项可切页定位  
5. **问答（连接性）**
   - 在“问答”输入：例如 **“油门踏板 APS 连接到 ECU 的哪些针脚？”**  
   - 返回答案、置信度、证据框；点击“定位”直达证据

---

## 🔌 后端 API

### `POST /ocr_pdf`
- **Body**：`{ pdf_url, pdf_name, dpi, tile, overlap, force? }`
- **行为**：按 **10 页一批** OCR → 每页写 `page_XXXX_DPI_rapidocr.json`；返回本次完成页数组

### `GET /ocr_cache`
- **Query**：`pdf_name, dpi, tile, overlap`
- **返回**：若存在缓存，返回合并页数组；否则 404

### `POST /synonyms`
- **Body**：`{ query, pdf_name }`
- **返回**：`{ synonyms, abbreviations, english }`

### `POST /qa`
- **Body**：`{ pdf_name, question, top_k?, window? }`
- **返回**
```json
{
  "answer": "…",
  "pins": ["A12","C123-1"],
  "pages": [12,13],
  "evidence": [
    {"page":12,"text":"…","box":{"x":123,"y":456,"w":78,"h":16}}
  ],
  "confidence": 0.78,
  "debug": { "pages": 120, "entries": 36540, "terms": ["aps","ecu","c123-1"], "context_len": 2580, "used": "tables" }
}
```

**OCR 缓存（每页 JSON）**
```json
{
  "page": 1,
  "w": 4961, "h": 7016,
  "hits": [
    { "text": "加速踏板位置传感器 APS", "conf": 0.92, "box": { "x": 310, "y": 982, "w": 640, "h": 28 } }
  ]
}
```

---

## 🔧 配置与调优

- 前后端 **OCR 参数需一致**（前端常量 `OCR_PARAMS`；后端按传参）  
  - `dpi = 500`：小字更清晰（内存占用 ↑）  
  - `tile = 1400`：块大召回更稳（速度略慢）  
  - `overlap = 0.12`：减少切块边缘漏字  
- 内存受限时：降低 `dpi`（如 360/420）或 `tile`（1200）  
- 线程（已在后端设置默认值，可按需覆盖）  
  - `CPU_NUM_THREADS=4`、`OMP_NUM_THREADS=4`

---

## 🩺 排障

- **PDF 一直“文档加载中…”**
  - 确认 `public/pdfjs/` 下存在 `pdf.worker.min.js / cmaps / standard_fonts`
  - 已内置看门狗：2.5s 未就绪会重挂载
- **`bcmap is not a valid URL`**
  - `cMapUrl` 必须指向 `/pdfjs/cmaps/`（已在 `page.tsx` 配置）
- **`messageHandler is null / sendWithPromise`**
  - 切页过快或组件卸载时仍在渲染；已加入渲染锁和超时解锁  
- **/ocr_cache 404**
  - 尚未 OCR 或 `pdf_name` 不一致；执行“OCR（可用缓存）”或“清除并重建”
- **OOM（内存不足被杀）**
  - 已改“10 页一批”落盘；减少同时打开的大文档 Tab；必要时降 DPI

---

## 🔐 安全

- 前端 `/api/proxy` 仅允许代理 `pdfs/` 目录下文件名（防 SSRF）  
- LLM 仅接收 **经检索裁剪后的上下文** 与问题，不上传整份 PDF

---

## 🗺 Roadmap（可选）

- 跨 PDF 搜索 & QA  
- 向量 PDF 优先抽线 → **网表**（component-pin ↔ net），QA 查图不猜  
- 针脚/连接器编号正则抽取 + 结构化索引  
- 标注导出（CSV/JSON）  
- 后端任务队列（大文档 OCR 异步）

---

## 📜 License

本仓库与线上演示仅授权用于招聘评估与非生产演示（Evaluation Only）。
不得用于生产或再分发；如需使用，请与本人联系。
