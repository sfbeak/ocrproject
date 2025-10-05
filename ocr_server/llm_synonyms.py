# -*- coding: utf-8 -*-
import os, json, hashlib, time, re, glob
from typing import List, Dict

# —— LLM 固定参数（只留 key 用 env）——
BASE_URL = "https://api.deepseek.com"
MODEL    = "deepseek-chat"   # 你想换就改这行

# —— 缓存 ——
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache", "synonyms")
os.makedirs(CACHE_DIR, exist_ok=True)

TTL_SEC = 7 * 24 * 3600  # 7 天缓存

def _cache_path(pdf_key: str, q_norm: str):
    h = hashlib.sha1(q_norm.encode("utf-8")).hexdigest()
    d = os.path.join(CACHE_DIR, pdf_key)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{h}.json")

def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())

def load_vocab_from_ocr_cache(pdf_name: str) -> List[str]:
    """
    从现有 OCR 缓存抽候选词表（最多 ~400 项）：
    - 直接文本
    - 中文 2-6 连续字
    - 英文/缩写片段（A-Z/0-9/_/-//）
    """
    if not pdf_name:
        return []
    safe = pdf_name.replace("/", "_")
    ocr_cache_dir = os.path.join(os.path.dirname(__file__), "cache")
    pattern = os.path.join(ocr_cache_dir, f"{safe}__*.json")
    files = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p), reverse=True)
    if not files:
        return []
    try:
        data = json.load(open(files[0], "r", encoding="utf-8"))
    except Exception:
        return []

    vocab = set()
    zh_pat = re.compile(r"[\u4e00-\u9fff]{2,6}")
    en_pat = re.compile(r"[A-Za-z][A-Za-z0-9_/\-]{1,31}")

    for page in data:
        for h in page.get("hits", []):
            t = str(h.get("text", "")).strip()
            if not t:
                continue
            if 2 <= len(t) <= 32:
                vocab.add(t)
            for m in zh_pat.findall(t):
                vocab.add(m)
            for m in en_pat.findall(t):
                vocab.add(m)

    out = [v for v in vocab if 2 <= len(v) <= 32]
    out.sort(key=lambda x: (len(x), x))
    return out[:400]

def llm_expand_synonyms(query: str, vocab: List[str], pdf_key: str = "global") -> Dict:
    """
    调用 OpenAI 兼容接口（baseUrl 和 model 已写死），温度 0，强制 JSON 输出。
    返回：{"synonyms":[], "abbreviations":[], "english":[]}
    带 7 天文件缓存。
    """
    from openai import OpenAI

    q_norm = _normalize(query)
    cp = _cache_path(pdf_key or "global", q_norm)

    # 命中缓存
    if os.path.exists(cp):
        try:
            data = json.load(open(cp, "r", encoding="utf-8"))
            if time.time() - data.get("_ts", 0) < TTL_SEC:
                return {k: data.get(k, []) for k in ("synonyms","abbreviations","english")}
        except:
            pass

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        # 没 key 就返回空，不抛 500，避免前端挂
        return {"synonyms": [], "abbreviations": [], "english": []}

    # 候选词表适度截断
    vocab = list({v for v in (vocab or []) if 2 <= len(v) <= 32})[:400]

    client = OpenAI(base_url=BASE_URL, api_key=api_key)

    sys_prompt = (
        "你是汽车电路图术语助手。给定“查询词”和“候选词表”，"
        "只在候选词表中挑选真正同义/缩写/英文翻译；若候选表没有合适项，"
        "可补充少量行业常见叫法。严格输出 JSON："
        '{"synonyms":[],"abbreviations":[],"english":[]}'
        "。不要上下位词、不要品牌型号、最多12条。"
    )

    user_prompt = f"""查询词：{query}

候选词表（可选，不必全部使用）：
{json.dumps(vocab, ensure_ascii=False)}
"""

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        obj = json.loads(raw)
    except Exception:
        obj = {"synonyms": [], "abbreviations": [], "english": []}

    # 清洗与截断
    for k in ("synonyms","abbreviations","english"):
        v = obj.get(k, [])
        if not isinstance(v, list):
            v = []
        v = [str(x).strip() for x in v if str(x).strip()]
        obj[k] = v[:6]  # 每类最多 6 个

    # 写缓存
    to_write = dict(obj); to_write["_ts"] = time.time()
    try:
        json.dump(to_write, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except:
        pass

    return obj
