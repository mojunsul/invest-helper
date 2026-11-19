import importlib
import pkgutil

from backend.tools import base
from backend.tools.base import Tool, ToolResult, cache_key, fail

_SKIP = {"base", "registry"}


def load_all() -> None:
    import backend.tools as pkg
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name not in _SKIP:
            importlib.import_module(f"backend.tools.{m.name}")


def describe() -> list[dict]:
    return [{"name": t.name, "kind": t.kind, "description": t.description,
             "input_schema": t.input_model.model_json_schema()} for t in base._TOOLS.values()]


def get(name: str) -> Tool | None:
    return base._TOOLS.get(name)


async def call(name: str, raw: dict) -> ToolResult:
    t = get(name)
    if t is None:
        return fail("INVALID_INPUT", "알 수 없는 도구입니다.")
    return await t.run(raw)


def cache_key_for(name: str, raw: dict) -> str | None:
    t = get(name)
    if t is None:
        return None
    try:
        return cache_key(name, t.normalize(raw))
    except Exception:  # ValidationError
        return None
