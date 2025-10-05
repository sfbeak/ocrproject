module.exports = { reactStrictMode: true };
const OCR_TARGET = process.env.OCR_BASE ?? 'http://127.0.0.1:8000';

module.exports = {
  async rewrites() {
    return [
      // 把 /__ocr/* 转发到后端 Flask
      { source: '/__ocr/:path*', destination: `${OCR_TARGET}/:path*` },
    ];
  },
};