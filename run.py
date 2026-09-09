"""Entry point produksi. Jalankan: python run.py"""
import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        workers=1,          # 1 worker: state line disimpan in-memory + WebSocket
        log_level="info",
    )
