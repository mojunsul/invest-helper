import asyncio
import threading
from contextlib import contextmanager
from typing import AsyncIterator, Callable, Iterator

from backend.events import bus
from backend.events.bus import Event

_sinks: dict[str, list[Callable[[Event], None]]] = {}
_lock = threading.Lock()
_installed = False


def _dispatch(ev: Event) -> None:
    with _lock:
        sinks = list(_sinks.get(ev.session_id, ()))  # 순회는 복사본
    for s in sinks:
        try:
            s(ev)
        except Exception:
            pass  # 싱크별 격리 (닫힌 루프 등)


def install() -> None:
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True
    bus.subscribe(_dispatch)


def _add(sid: str, sink) -> None:
    with _lock:
        _sinks.setdefault(sid, []).append(sink)


def _remove(sid: str, sink) -> None:
    with _lock:
        lst = _sinks.get(sid, [])
        if sink in lst:
            lst.remove(sink)
        if not lst:
            _sinks.pop(sid, None)


@contextmanager
def collect(session_id: str) -> Iterator[list[Event]]:
    evs: list[Event] = []
    _add(session_id, evs.append)
    try:
        yield evs
    finally:
        _remove(session_id, evs.append)


class EventStream:
    def __init__(self, session_id: str):
        self._sid = session_id
        self._loop = asyncio.get_running_loop()
        self._q: asyncio.Queue[Event] = asyncio.Queue()
        self._closed = False
        self._sink = lambda ev: self._loop.call_soon_threadsafe(self._q.put_nowait, ev)
        _add(session_id, self._sink)

    async def __aiter__(self) -> AsyncIterator[Event]:
        try:
            while True:
                ev = await self._q.get()
                yield ev
                if ev.type == "final":
                    return
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            _remove(self._sid, self._sink)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_stream(session_id: str) -> EventStream:
    return EventStream(session_id)


def sse_format(ev: Event) -> str:
    return f"id: {ev.seq}\nevent: {ev.type}\ndata: {ev.model_dump_json()}\n\n"
