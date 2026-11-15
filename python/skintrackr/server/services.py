"""The services request handlers share, kept on the app's state."""

import asyncio
from collections.abc import Coroutine
from typing import Any

from attrs import define, field
from litestar.datastructures import State

from skintrackr.clients.blob_store import BlobStore
from skintrackr.clients.store import Store
from skintrackr.clients.intervals import IntervalsClient
from skintrackr.config import Config
from skintrackr.logging import get_logger

logger = get_logger(__name__)


@define
class Services:
    """Everything handlers need, built once at startup.

    Attributes:
        config: Server settings.
        store: Redis storage.
        intervals: intervals.icu client without an API key; `intervals_for` derives per-athlete clients.
        blob_store: Activity archive.
        background: Tasks running after their request was answered; held so they aren't garbage collected.
    """

    config: Config
    store: Store
    intervals: IntervalsClient
    blob_store: BlobStore
    background: set[asyncio.Task[None]] = field(factory=set)

    async def intervals_for(self, athlete_id: str) -> IntervalsClient:
        """An intervals.icu client acting for an athlete, with their API key.

        Raises:
            NotFoundError: The athlete isn't connected.
        """
        return self.intervals.with_api_key(await self.store.fetch_api_key(athlete_id))

    def spawn(self, coroutine: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
        """Run a coroutine in the background, logging it if it fails."""
        task = asyncio.create_task(coroutine, name=name)
        self.background.add(task)
        task.add_done_callback(self._finished)
        return task

    def is_running(self, name: str) -> bool:
        """Whether a background task called `name` is running."""
        return any(task.get_name() == name for task in self.background)

    def cancel(self, name: str) -> None:
        """Cancel the background tasks called `name`, if any."""
        for task in self.background:
            if task.get_name() == name:
                task.cancel()

    def _finished(self, task: asyncio.Task[None]) -> None:
        self.background.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("background task failed", exc_info=error, extra={"task": task.get_name()})

    async def cancel_background(self) -> None:
        """Cancel background tasks and wait for them to finish (at shutdown)."""
        tasks = list(self.background)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def get_services(state: State) -> Services:
    """The services from the app's state (set up by the app's lifespan)."""
    return state.services
