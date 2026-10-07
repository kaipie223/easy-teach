"""同一项目的教学蓝图生成只允许一条流水线在跑，后来者加入它。

为什么必须有这层：生成一次要十几次模型往返、几分钟；而蓝图是**全部完成并写库之后**
才能被 ``GET /plan`` 看到的。生成期间页面一刷新就是一次 404 → 前端自动重新发起一次
完整生成（见 BlueprintView 的 404 分支）。于是同一个项目会同时跑好几条流水线：
它们互相抢模型额度，每条都更慢，教师看到的就是"进度条一动不动、永远跑不完"。

这里提供一个进程内的作业注册表：项目级的扇出（fan-out）。第一条请求是 owner，
它真正跑生成；后来的请求订阅同一条作业，先补收当前进度，再收到最终结果 —— 不重复
花钱，也不会互相拖慢。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

logger = logging.getLogger(__name__)


class PlanJob:
    """一条蓝图生成作业：进度帧 + 最终结果的扇出。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self.last_progress: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None
        self.error: BaseException | None = None
        self.finished = False

    # ── 订阅方 ────────────────────────────────────────────

    def subscribe(self) -> queue.Queue:
        """拿到一个订阅队列；会先补上当前进度，已完成则补上终态。"""
        channel: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(channel)
            if self.last_progress is not None:
                channel.put(("progress", self.last_progress))
            if self.finished:
                channel.put(None)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        with self._lock:
            if channel in self._subscribers:
                self._subscribers.remove(channel)

    def wait_for_result(self) -> dict[str, Any] | None:
        """同步等待最终结果（非流式客户端用）。"""
        channel = self.subscribe()
        try:
            while True:
                item = channel.get()
                if item is None:
                    return self.result
                kind, payload = item
                if kind == "result":
                    return payload
        finally:
            self.unsubscribe(channel)

    # ── 生成方 ────────────────────────────────────────────

    def emit(self, kind: str, payload: Any) -> None:
        with self._lock:
            if kind == "progress":
                self.last_progress = payload
            elif kind == "result":
                self.result = payload
                self.finished = True
            elif kind == "error":
                self.finished = True
            channels = list(self._subscribers)
        for channel in channels:
            channel.put((kind, payload))

    def fail(self, error: BaseException) -> None:
        """记录失败：订阅方据此抛出同一个异常，而不是各自编一句文案。"""
        with self._lock:
            self.error = error
            self.finished = True
        self.emit("error", None)

    def close(self) -> None:
        with self._lock:
            self.finished = True
            channels = list(self._subscribers)
            self._subscribers.clear()
        for channel in channels:
            channel.put(None)


_JOBS: dict[str, PlanJob] = {}
_JOBS_LOCK = threading.Lock()


def acquire_plan_job(project_id: str) -> tuple[PlanJob, bool]:
    """返回 (作业, 我是不是 owner)。已有在跑的作业时不会新建。"""
    with _JOBS_LOCK:
        existing = _JOBS.get(project_id)
        if existing is not None and not existing.finished:
            logger.info("项目 %s 已有蓝图生成在跑，本次请求加入等待", project_id)
            return existing, False
        job = PlanJob()
        _JOBS[project_id] = job
        return job, True


def release_plan_job(project_id: str, job: PlanJob) -> None:
    with _JOBS_LOCK:
        if _JOBS.get(project_id) is job:
            _JOBS.pop(project_id, None)
