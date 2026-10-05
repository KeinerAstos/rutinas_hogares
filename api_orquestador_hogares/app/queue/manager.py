# -*- coding: utf-8 -*-
from __future__ import annotations
import itertools, queue, threading, time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

class QueueJobTimeout(TimeoutError):
    pass

@dataclass(order=True)
class _QueueItem:
    priority: int
    sequence: int
    job_id: str = field(compare=False)
    case_id: str = field(compare=False)
    source: str = field(compare=False)
    fn: Callable[[], Any] = field(compare=False)
    created_at: float = field(default_factory=time.time, compare=False)
    started_at: float | None = field(default=None, compare=False)
    finished_at: float | None = field(default=None, compare=False)
    status: str = field(default="QUEUED", compare=False)
    result: Any = field(default=None, compare=False)
    error: BaseException | None = field(default=None, compare=False)
    done_event: threading.Event = field(default_factory=threading.Event, compare=False)
    started_event: threading.Event = field(default_factory=threading.Event, compare=False)

class SmccPriorityQueueManager:
    def __init__(self, *, max_workers: int = 3, history_limit: int = 5000, name: str = "smcc") -> None:
        self.name = str(name)
        self.max_workers = max(1, int(max_workers))
        self.history_limit = max(100, int(history_limit))
        self._queue: queue.PriorityQueue[_QueueItem] = queue.PriorityQueue()
        self._lock = threading.RLock()
        self._sequence = itertools.count()
        self._jobs: OrderedDict[str, _QueueItem] = OrderedDict()
        self._threads: list[threading.Thread] = []
        self._started = False
        self._completed = 0
        self._errors = 0
        self._timeouts = 0
        self._cancelled = 0

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            for index in range(self.max_workers):
                t = threading.Thread(target=self._worker_loop, name=f"atlas-{self.name}-queue-{index+1}", daemon=True)
                t.start()
                self._threads.append(t)

    def submit_and_wait(self, *, job_id: str, case_id: str, source: str, priority: int, queue_timeout_seconds: float, fn: Callable[[], Any]) -> Any:
        self.start()
        safe_job_id = str(job_id or "").strip()
        if not safe_job_id:
            raise ValueError("job_id es obligatorio")
        item = _QueueItem(priority=int(priority), sequence=next(self._sequence), job_id=safe_job_id, case_id=str(case_id or "").strip(), source=str(source or "UNKNOWN").strip().upper(), fn=fn)
        with self._lock:
            existing = self._jobs.get(safe_job_id)
            if existing and existing.status in {"QUEUED","RUNNING"}:
                raise RuntimeError("QUEUE_JOB_DUPLICADO_ACTIVO")
            self._jobs[safe_job_id] = item
            self._jobs.move_to_end(safe_job_id)
            self._trim_history_locked()
        self._queue.put(item)
        deadline = time.monotonic() + max(0.1, float(queue_timeout_seconds))
        while True:
            if item.started_event.wait(timeout=0.05) or item.done_event.is_set():
                break
            if time.monotonic() >= deadline:
                with self._lock:
                    if item.status == "QUEUED":
                        item.status = "TIMEOUT"
                        item.finished_at = time.time()
                        self._timeouts += 1
                        item.done_event.set()
                        raise QueueJobTimeout("QUEUE_WAIT_TIMEOUT")
                break
        item.done_event.wait()
        if item.status == "SUCCESS": return item.result
        if item.status == "TIMEOUT": raise QueueJobTimeout("QUEUE_WAIT_TIMEOUT")
        if item.status == "CANCELLED": raise QueueJobTimeout("QUEUE_JOB_CANCELLED")
        if item.status == "ERROR" and item.error is not None: raise item.error
        raise RuntimeError(f"QUEUE_JOB_ESTADO_INVALIDO={item.status}")

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                with self._lock:
                    if item.status != "QUEUED":
                        if item.status != "TIMEOUT":
                            item.status = "CANCELLED"
                            self._cancelled += 1
                        item.done_event.set()
                        continue
                    item.status = "RUNNING"
                    item.started_at = time.time()
                    item.started_event.set()
                try:
                    result = item.fn()
                except BaseException as exc:
                    with self._lock:
                        item.error = exc
                        item.status = "ERROR"
                        item.finished_at = time.time()
                        self._errors += 1
                    item.done_event.set()
                else:
                    with self._lock:
                        item.result = result
                        item.status = "SUCCESS"
                        item.finished_at = time.time()
                        self._completed += 1
                    item.done_event.set()
            finally:
                self._queue.task_done()

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            queued = [x for x in self._jobs.values() if x.status == "QUEUED"]
            running = [x for x in self._jobs.values() if x.status == "RUNNING"]
            oldest = 0.0 if not queued else max(0.0, now - min(x.created_at for x in queued))
            by_source: dict[str, dict[str, int]] = {}
            for item in self._jobs.values():
                b = by_source.setdefault(item.source or "UNKNOWN", {"queued":0,"running":0,"success":0,"error":0,"timeout":0,"cancelled":0})
                key = item.status.lower()
                if key in b: b[key] += 1
            return {"ok":True,"enabled":True,"manager":"ATLAS_PRIORITY_QUEUE_V1_1","mode":"THREAD_PRIORITY_QUEUE","workers":self.max_workers,"active":len(running),"queued":len(queued),"available":max(0,self.max_workers-len(running)),"completed":self._completed,"errors":self._errors,"timeouts":self._timeouts,"cancelled":self._cancelled,"oldest_wait_seconds":round(oldest,3),"history_size":len(self._jobs),"by_source":by_source}

    def health(self) -> dict[str, Any]:
        data = self.status()
        alive = len([t for t in self._threads if t.is_alive()])
        data["worker_threads_alive"] = alive
        data["healthy"] = bool(self._started and alive == self.max_workers)
        return data

    def _trim_history_locked(self) -> None:
        if len(self._jobs) <= self.history_limit: return
        for job_id in [k for k,v in self._jobs.items() if v.status not in {"QUEUED","RUNNING"}]:
            if len(self._jobs) <= self.history_limit: break
            self._jobs.pop(job_id, None)

smcc_queue_manager = SmccPriorityQueueManager(max_workers=3, history_limit=5000, name="smcc")
smcc_queue_manager.start()
