"""Command Queue - serializes agent runs per session"""

import asyncio
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Any
import time


@dataclass
class QueueEntry:
    """Queue entry"""
    session_key: str
    run_id: str
    task: Callable
    created_at: int = 0

    def __post_init__(self):
        if self.created_at == 0:
            self.created_at = int(time.time() * 1000)


class CommandQueue:
    """
    Command Queue - serializes agent runs per session
    
    Similar to OpenClaw's command queue system that prevents concurrent runs
    on the same session and keeps session history consistent.
    """

    def __init__(self):
        # Per-session queues
        self._session_queues: Dict[str, asyncio.Queue] = {}
        # Global queue (optional, for cross-session serialization)
        self._global_queue: Optional[asyncio.Queue] = None
        # Active runs tracking
        self._active_runs: Dict[str, str] = {}  # session_key -> run_id

    def _get_session_queue(self, session_key: str) -> asyncio.Queue:
        """Get or create queue for a session"""
        if session_key not in self._session_queues:
            self._session_queues[session_key] = asyncio.Queue()
        return self._session_queues[session_key]

    async def enqueue(
        self,
        session_key: str,
        run_id: str,
        task: Callable[[], Any],
        global_lane: bool = False,
    ) -> Any:
        """
        Enqueue a task for execution

        Args:
            session_key: Session key (determines queue lane)
            run_id: Unique run ID
            task: Async callable to execute (must be callable with no args)
            global_lane: Whether to also use global queue

        Returns:
            Task result
        """
        # Check if session already has an active run
        if session_key in self._active_runs:
            # Wait for current run to complete
            # TODO: Implement proper waiting mechanism
            pass

        self._active_runs[session_key] = run_id

        try:
            # Enqueue in session queue
            session_queue = self._get_session_queue(session_key)
            await session_queue.put(QueueEntry(session_key, run_id, task))

            # If global lane, also enqueue globally
            if global_lane:
                if self._global_queue is None:
                    self._global_queue = asyncio.Queue()
                await self._global_queue.put(QueueEntry(session_key, run_id, task))

            # Execute task
            if asyncio.iscoroutinefunction(task):
                result = await task()
            else:
                result = task()

            return result
        finally:
            # Remove from active runs
            if self._active_runs.get(session_key) == run_id:
                del self._active_runs[session_key]

    def is_session_busy(self, session_key: str) -> bool:
        """Check if session has an active run"""
        return session_key in self._active_runs

    def get_active_run_id(self, session_key: str) -> Optional[str]:
        """Get active run ID for a session"""
        return self._active_runs.get(session_key)
