# OCR 服务（带缓存）
- 缓存目录：`ocr_server/cache/`，文件名：`<pdf>__<dpi>_<tile>_<overlap>.json`
- 接口：
  - `POST /ocr_pdf`：命中缓存直接返回；`force=true` 强制重建
  - `GET /ocr_cache?pdf_name=...&dpi=...&tile=...&overlap=...`：读取缓存