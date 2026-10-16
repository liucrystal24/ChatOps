"""单 Worker 入口；领取、租约和发布仍支持多个进程竞争。"""

import asyncio
import logging
import signal
import time

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import load_settings
from app.core.logging import configure_logging
from app.infrastructure.database import create_engine
from app.infrastructure.embedding import Embedding
from app.infrastructure.files import Files
from app.infrastructure.repositories import Database
from app.infrastructure.vectors import Vectors
from app.modules.indexing.service import IndexingService


async def main():
    settings = load_settings()
    configure_logging()
    engine = create_engine(settings)
    embedding, vectors = Embedding(settings), Vectors(settings)
    service = IndexingService(
        Database(async_sessionmaker(engine, expire_on_commit=False)),
        Files(settings),
        embedding,
        vectors,
        settings,
    )
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    last_maintenance = 0
    try:
        while not stopped.is_set():
            try:
                if time.monotonic() - last_maintenance >= 60:
                    await service.maintain()
                    last_maintenance = time.monotonic()
                task = asyncio.create_task(service.run_once())
                stop = asyncio.create_task(stopped.wait())
                done, _ = await asyncio.wait([task, stop], return_when=asyncio.FIRST_COMPLETED)
                if stop in done:
                    task.cancel()
                stop.cancel()
                result = await asyncio.gather(task, stop, return_exceptions=True)
                if isinstance(result[0], Exception):
                    raise result[0]
                if not result[0] and not stopped.is_set():
                    try:
                        await asyncio.wait_for(stopped.wait(), 2)
                    except TimeoutError:
                        pass
            except Exception:
                logging.getLogger("chat_rag.indexing").warning("worker_dependency_unavailable")
                try:
                    await asyncio.wait_for(stopped.wait(), 5)
                except TimeoutError:
                    pass
    finally:
        await embedding.close()
        await vectors.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
