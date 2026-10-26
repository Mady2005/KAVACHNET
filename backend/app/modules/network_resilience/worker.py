"""Lifecycle-managed single-process queue worker."""
import asyncio
import logging
from app.core.config import settings
from app.db.session import SessionLocal
from app.modules.network_resilience.queue_service import process_eligible

logger=logging.getLogger(__name__)

async def run_delivery_worker():
    """Poll due work while the FastAPI lifespan is active; stop on cancellation."""
    while True:
        try:
            with SessionLocal() as db:
                process_eligible(db,actor_user_id=None,batch_size=20)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The database may not yet be migrated during local setup; retry on the next tick.
            logger.exception("Network delivery worker iteration failed")
        await asyncio.sleep(settings.network_worker_poll_seconds)
