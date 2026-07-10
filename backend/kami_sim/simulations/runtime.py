"""Single-process coordination for simulation commands and background runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any


class RunConflictError(RuntimeError):
    """Raised when a second command targets a busy runtime."""


class SimulationRunManager:
    """Own one exclusive simulation command at a time within this process."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._busy = False
        self._running = False
        self._paused = True
        self._operation: str | None = None
        self._simulation_id: str | None = None

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def snapshot(self) -> dict:
        return {
            "busy": self._busy,
            "running": self._running,
            "paused": self._paused,
            "operation": self._operation,
            "simulation_id": self._simulation_id,
        }

    def ensure_idle(self, action: str) -> None:
        if self._busy:
            raise RunConflictError(
                f"Cannot {action} while {self._operation or 'another command'} is running"
            )

    @asynccontextmanager
    async def command(
        self,
        action: str,
        simulation_id: str | None,
        *,
        running: bool,
    ) -> AsyncIterator[None]:
        self._claim(action, simulation_id, running=running)
        try:
            async with self._lock:
                yield
        finally:
            self._release()

    def start_background(
        self,
        action: str,
        simulation_id: str,
        ticks: int,
        tick_callback: Callable[[], Awaitable[list[dict]]],
        error_callback: Callable[[Exception], Awaitable[Any]],
        completion_callback: Callable[[], Awaitable[Any]] | None = None,
    ) -> asyncio.Task:
        self._claim(action, simulation_id, running=True)

        async def runner() -> None:
            try:
                async with self._lock:
                    for _ in range(ticks):
                        if self._paused:
                            break
                        results = await tick_callback()
                        if results and results[0].get("error"):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await error_callback(exc)
            finally:
                try:
                    if completion_callback is not None:
                        await completion_callback()
                except Exception as exc:
                    await error_callback(exc)
                finally:
                    self._release()

        try:
            self._task = asyncio.create_task(runner())
        except Exception:
            self._release()
            raise
        return self._task

    def start_operation(
        self,
        action: str,
        simulation_id: str,
        callback: Callable[[], Awaitable[Any]],
    ) -> asyncio.Task:
        """Run one exclusive non-tick operation in the background."""
        self._claim(action, simulation_id, running=False)

        async def runner() -> None:
            try:
                async with self._lock:
                    await callback()
            finally:
                self._release()

        try:
            self._task = asyncio.create_task(runner())
        except Exception:
            self._release()
            raise
        return self._task

    async def cancel_operation(self, simulation_id: str) -> bool:
        task = self._task
        if task is None or task.done() or self._simulation_id != simulation_id:
            return False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return True

    def pause(self) -> None:
        self._paused = True

    async def shutdown(self) -> None:
        self.pause()
        task = self._task
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _claim(self, action: str, simulation_id: str | None, *, running: bool) -> None:
        self.ensure_idle(action)
        self._busy = True
        self._running = running
        self._paused = not running
        self._operation = action
        self._simulation_id = simulation_id

    def _release(self) -> None:
        self._busy = False
        self._running = False
        self._paused = True
        self._operation = None
        self._simulation_id = None
        self._task = None
