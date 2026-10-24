from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.routes import router
from app.core.config import settings

app=FastAPI(title="KAVACHNET API",description="Shared foundation for cyber-resilient edge intelligence.",version="0.1.0")
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in settings.allowed_origins.split(",")],allow_credentials=True,allow_methods=["GET","POST","PATCH","OPTIONS"],allow_headers=["Authorization","Content-Type","X-CSRF-Token"])
app.include_router(router,prefix="/api/v1")
