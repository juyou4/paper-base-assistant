"""记录一次对话请求中实际执行的步骤，供同一浏览器会话实时查看。"""

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar


_current = ContextVar("paper_activity", default=None)


def empty_snapshot(request_id):
    return {"request_id": request_id, "events": [], "finished": False}


class Run:
    def __init__(self, request_id):
        self.request_id = request_id
        self.finished = False
        self._events = []
        self._lock = threading.Lock()

    @contextmanager
    def recording(self):
        token = _current.set(self)
        try:
            yield
        finally:
            _current.reset(token)
            with self._lock:
                self.finished = True

    def start(self, label, tool):
        with self._lock:
            event = {"id": len(self._events) + 1, "label": label, "tool": tool, "status": "running",
                     "start": time.monotonic(), "end": None}
            self._events.append(event)
            return event

    def end(self, event, status):
        with self._lock:
            event.update(status=status, end=time.monotonic())

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            events = [{"id": e["id"], "label": e["label"], "status": e["status"],
                       **({"tool": e["tool"]} if e["tool"] else {}),
                       "elapsed_ms": int(((e["end"] if e["end"] is not None else now) - e["start"]) * 1000)}
                      for e in self._events]
            return {"request_id": self.request_id, "events": events, "finished": self.finished}


class History:
    def __init__(self):
        self._runs = {}
        self._lock = threading.Lock()

    def begin(self, request_id):
        with self._lock:
            prior = self._runs.get(request_id)
            if prior and not prior.finished:
                raise ValueError("这条请求正在处理，请稍候。")
            self._runs.pop(request_id, None)
            if len(self._runs) >= 32:
                expired = next((key for key, run in self._runs.items() if run.finished), None)
                if expired is None:
                    raise ValueError("当前对话仍有多条请求正在处理，请稍后再发送。")
                del self._runs[expired]
            run = Run(request_id)
            self._runs[request_id] = run
            return run

    def snapshot(self, request_id):
        with self._lock:
            run = self._runs.get(request_id)
        return run.snapshot() if run else empty_snapshot(request_id)


@contextmanager
def step(label, tool=None):
    """label 由调用处提供固定的操作名，不含用户输入、命令参数或底层响应。"""
    run = _current.get()
    if run is None:
        yield
        return
    event = run.start(label, tool)
    try:
        yield
    except BaseException:
        run.end(event, "failed")
        raise
    else:
        run.end(event, "done")
