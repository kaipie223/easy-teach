from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from api import chat, upload, generate, export, speech

app = FastAPI(title=settings.app_name, debug=settings.debug)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat.router,       prefix="/api/chat",       tags=["对话"])
app.include_router(upload.router,     prefix="/api/upload",     tags=["上传"])
app.include_router(generate.router,   prefix="/api/generate",   tags=["生成"])
app.include_router(export.router,     prefix="/api/export",     tags=["导出"])
app.include_router(speech.router,     prefix="/api/speech",     tags=["语音"])


@app.get("/")
def root():
    return {"app": settings.app_name, "status": "running"}
