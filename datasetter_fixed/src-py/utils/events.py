"""
utils/events.py

Server-Sent Events (SSE) emitter.
Every agent calls emit() to broadcast state changes to the frontend.
The FastAPI /events/{job_id} endpoint streams these to the UI.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Dict, List
from uuid import UUID

from core.models import (
    AgentID,
    AgentLogEvent,
    AgentStatus,
    AgentStatusEvent,
    AnyEvent,
    DatasetRow,
    NotificationEvent,
    NotificationLevel,
    PipelineState,
    PipelineStatusEvent,
    RowsBatchEvent,
    RowUpdateEvent,
)

log = logging.getLogger(__name__)


class EventBus:
    """
    In-process pub/sub bus for SSE events.
    One EventBus per running job.
    The FastAPI route subscribes and streams to the client.
    """

    def __init__(self, job_id: UUID):
        self.job_id    = job_id
        self._queues: List[asyncio.Queue] = []
        self._closed   = False

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def _publish(self, event: AnyEvent) -> None:
        if self._closed:
            return
        payload = event.model_dump_json()
        for q in list(self._queues):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning(f"[events] queue full for job {self.job_id}, dropping event")

    def close(self) -> None:
        self._closed = True
        for q in self._queues:
            try:
                q.put_nowait(None)  # sentinel → client knows stream is done
            except asyncio.QueueFull:
                pass


# ─── SSE stream generator ─────────────────────────────────────────────────────

async def sse_generator(bus: EventBus) -> AsyncIterator[str]:
    """
    Yields SSE-formatted strings for a FastAPI StreamingResponse.
    Cleans up queue subscription properly when client disconnects or stream ends.
    """
    q = bus.subscribe()
    try:
        while True:
            try:
                # Use wait_for so we can detect client disconnects via GeneratorExit
                payload = await asyncio.wait_for(q.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send a keep-alive comment to detect dead connections
                yield ": keep-alive\n\n"
                continue
            if payload is None:
                # Sentinel — stream is intentionally closed
                yield "event: close\ndata: {}\n\n"
                break
            yield f"data: {payload}\n\n"
    except GeneratorExit:
        # Client disconnected — clean up silently
        pass
    finally:
        bus.unsubscribe(q)
        # Drain any remaining items so the queue doesn't block the bus
        while not q.empty():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                break


# ─── Emitter — used by agents ─────────────────────────────────────────────────

class Emitter:
    """
    Thin wrapper around EventBus that agents use.
    Provides typed helper methods so agents don't build events manually.
    """

    def __init__(self, bus: EventBus):
        self.bus    = bus
        self.job_id = bus.job_id

    def log(self, agent_id: AgentID, message: str) -> None:
        """Emit a log line from an agent. Shows in the pipeline log panel."""
        log.info(f"[{agent_id.value}] {message}")
        self.bus._publish(AgentLogEvent(
            job_id=self.job_id,
            agent_id=agent_id,
            message=message,
            timestamp=time.time(),
        ))

    def agent_status(
        self,
        agent_id: AgentID,
        status: AgentStatus,
        model_used: str | None = None,
        current_task: str = "",
    ) -> None:
        """Emit an agent status change."""
        self.bus._publish(AgentStatusEvent(
            job_id=self.job_id,
            agent_id=agent_id,
            status=status,
            model_used=model_used,
            current_task=current_task,
        ))

    def pipeline_state(self, state: PipelineState) -> None:
        """Emit full pipeline state snapshot."""
        self.bus._publish(PipelineStatusEvent(state=state))

    def row_update(self, row: DatasetRow) -> None:
        """Emit a single row update (status change, edit, fix)."""
        self.bus._publish(RowUpdateEvent(job_id=self.job_id, row=row))

    def rows_batch(self, rows: List[DatasetRow]) -> None:
        """Emit a batch of new rows from Scripter."""
        self.bus._publish(RowsBatchEvent(job_id=self.job_id, rows=rows))

    def notify(self, level: NotificationLevel, title: str, message: str) -> None:
        """Emit a user-facing notification (toast)."""
        self.bus._publish(NotificationEvent(level=level, title=title, message=message))

    # ── Convenience shortcuts ─────────────────────────────────────────────────

    def info(self, title: str, message: str) -> None:
        self.notify(NotificationLevel.INFO, title, message)

    def success(self, title: str, message: str) -> None:
        self.notify(NotificationLevel.SUCCESS, title, message)

    def warning(self, title: str, message: str) -> None:
        self.notify(NotificationLevel.WARNING, title, message)

    def error(self, title: str, message: str) -> None:
        self.notify(NotificationLevel.ERROR, title, message)


# ─── Global registry ──────────────────────────────────────────────────────────

class EventRegistry:
    """
    App-wide registry of active EventBus instances, keyed by job_id.
    FastAPI routes look up the bus here to attach SSE streams.
    """

    def __init__(self):
        self._buses: Dict[UUID, EventBus] = {}

    def create(self, job_id: UUID) -> EventBus:
        bus = EventBus(job_id)
        self._buses[job_id] = bus
        return bus

    def get(self, job_id: UUID) -> EventBus | None:
        return self._buses.get(job_id)

    def close(self, job_id: UUID) -> None:
        bus = self._buses.pop(job_id, None)
        if bus:
            bus.close()

    def close_all(self) -> None:
        for bus in list(self._buses.values()):
            bus.close()
        self._buses.clear()


# Singleton used by the whole app
event_registry = EventRegistry()
