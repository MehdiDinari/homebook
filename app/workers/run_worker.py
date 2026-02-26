from __future__ import annotations

import asyncio

from arq.worker import run_worker

from app.workers.arq_worker import WorkerSettings


def main() -> None:
    # Python 3.14 no longer auto-creates an event loop for the main thread.
    # ARQ still expects one via asyncio.get_event_loop() during worker init.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
