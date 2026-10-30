import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.routes import router
from app.api.v1.network import router as network_router
from app.core.config import settings
from app.modules.network_resilience.worker import run_delivery_worker
from app.modules.network_resilience.measurements import recover_interrupted_runs, shutdown_measurement_runs
from app.modules.network_resilience.ripe_atlas import recover_interrupted_ingestions

@asynccontextmanager
async def lifespan(app:FastAPI):
    recover_interrupted_runs()
    recover_interrupted_ingestions()
    worker=asyncio.create_task(run_delivery_worker(),name="kavachnet-network-delivery")
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        await shutdown_measurement_runs()

app=FastAPI(title="KAVACHNET API",description="Shared foundation for cyber-resilient edge intelligence.",version="0.1.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in settings.allowed_origins.split(",")],allow_credentials=True,allow_methods=["GET","POST","PATCH","OPTIONS"],allow_headers=["Authorization","Content-Type","X-CSRF-Token"])
app.include_router(router,prefix="/api/v1")
app.include_router(network_router,prefix="/api/v1")
