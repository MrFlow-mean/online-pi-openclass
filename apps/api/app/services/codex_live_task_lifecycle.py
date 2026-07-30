from __future__ import annotations

import asyncio
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from app.models import SelectionRef


TaskAction = Literal["auto", "queue", "supplement", "replace", "chat", "dismiss"]
TaskStatus = Literal["pending", "queued", "running", "completed", "failed", "cancelled", "dismissed"]

_RECENT_TASK_TTL_SECONDS = 5 * 60
_SIMILARITY_THRESHOLD = 0.94


def normalize_task_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)
    return normalized


def task_texts_match(first: str, second: str) -> bool:
    left = normalize_task_text(first)
    right = normalize_task_text(second)
    if not left or not right:
        return False
    if left == right:
        return True
    if min(len(left), len(right)) < 8:
        return False
    return SequenceMatcher(a=left, b=right, autojunk=False).ratio() >= _SIMILARITY_THRESHOLD


@dataclass
class CodexLiveTask:
    delegation_id: str
    prompt: str
    provider_delegation: bool
    selection: SelectionRef | None = None
    action: TaskAction = "auto"
    status: TaskStatus = "pending"
    created_at: float = 0
    cancel_event: threading.Event | None = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = time.monotonic()


@dataclass(frozen=True)
class TaskDecision:
    kind: Literal["queued", "pending", "duplicate", "chat", "dismissed"]
    task: CodexLiveTask
    duplicate_of: str | None = None
    queue_position: int | None = None


class CodexLiveTaskCoordinator:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[CodexLiveTask] = asyncio.Queue()
        self.active: CodexLiveTask | None = None
        self.pending: dict[str, CodexLiveTask] = {}
        self._tasks: dict[str, CodexLiveTask] = {}
        self._lock = asyncio.Lock()

    def snapshot(self) -> dict[str, int | str | None]:
        return {
            "running_count": 1 if self.active is not None else 0,
            "queued_count": self.queue.qsize(),
            "pending_count": len(self.pending),
            "active_delegation_id": self.active.delegation_id if self.active else None,
        }

    async def submit(self, task: CodexLiveTask) -> TaskDecision:
        async with self._lock:
            self._prune_recent()
            duplicate = self._duplicate_for(task)
            if duplicate is not None:
                task.status = "dismissed"
                self._tasks[task.delegation_id] = task
                return TaskDecision("duplicate", task, duplicate_of=duplicate.delegation_id)
            self._tasks[task.delegation_id] = task
            if task.action == "chat":
                task.status = "dismissed"
                return TaskDecision("chat", task)
            if task.action == "dismiss":
                task.status = "dismissed"
                return TaskDecision("dismissed", task)
            if self.active is None and self.queue.empty():
                return await self._enqueue(task)
            if task.action == "queue":
                return await self._enqueue(task)
            if task.action in {"supplement", "replace"}:
                return await self._replace_active(task, supplement=task.action == "supplement")
            task.status = "pending"
            self.pending[task.delegation_id] = task
            return TaskDecision("pending", task)

    async def resolve(self, delegation_id: str, action: TaskAction) -> TaskDecision | None:
        async with self._lock:
            task = self.pending.pop(delegation_id, None)
            if task is None:
                return None
            task.action = action
            if action == "chat":
                task.status = "dismissed"
                return TaskDecision("chat", task)
            if action == "dismiss":
                task.status = "dismissed"
                return TaskDecision("dismissed", task)
            if action in {"supplement", "replace"} and self.active is not None:
                return await self._replace_active(task, supplement=action == "supplement")
            return await self._enqueue(task)

    async def begin(self, task: CodexLiveTask) -> threading.Event:
        async with self._lock:
            task.status = "running"
            task.cancel_event = threading.Event()
            self.active = task
            return task.cancel_event

    async def finish(self, task: CodexLiveTask, status: TaskStatus) -> None:
        async with self._lock:
            task.status = status
            if self.active is task:
                self.active = None

    async def _enqueue(self, task: CodexLiveTask) -> TaskDecision:
        task.status = "queued"
        await self.queue.put(task)
        return TaskDecision("queued", task, queue_position=self.queue.qsize())

    async def _replace_active(self, task: CodexLiveTask, *, supplement: bool) -> TaskDecision:
        active = self.active
        if active is not None and active.cancel_event is not None:
            active.cancel_event.set()
        if supplement and active is not None:
            task.prompt = f"{active.prompt}\n\n用户补充要求：\n{task.prompt}"
        if not supplement:
            self._dismiss_queued_and_pending(except_id=task.delegation_id)
        task.status = "queued"
        await self._prepend(task)
        return TaskDecision("queued", task, queue_position=1)

    async def _prepend(self, task: CodexLiveTask) -> None:
        existing: list[CodexLiveTask] = []
        while not self.queue.empty():
            existing.append(self.queue.get_nowait())
            self.queue.task_done()
        await self.queue.put(task)
        for queued_task in existing:
            await self.queue.put(queued_task)

    def _dismiss_queued_and_pending(self, *, except_id: str) -> None:
        while not self.queue.empty():
            queued = self.queue.get_nowait()
            queued.status = "dismissed"
            self.queue.task_done()
        for pending_id, pending_task in list(self.pending.items()):
            if pending_id == except_id:
                continue
            pending_task.status = "dismissed"
            self.pending.pop(pending_id, None)

    def _duplicate_for(self, task: CodexLiveTask) -> CodexLiveTask | None:
        for existing in self._tasks.values():
            if existing.delegation_id == task.delegation_id:
                continue
            if existing.status not in {"pending", "queued", "running"}:
                continue
            if task_texts_match(task.prompt, existing.prompt):
                return existing
        return None

    def _prune_recent(self) -> None:
        cutoff = time.monotonic() - _RECENT_TASK_TTL_SECONDS
        for task_id, task in list(self._tasks.items()):
            if task.status in {"completed", "failed", "cancelled", "dismissed"} and task.created_at < cutoff:
                self._tasks.pop(task_id, None)
