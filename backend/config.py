import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# 승인 전 신규 항목: 코드 상수만 사용, .env는 읽지 않는다
TOOL_TIMEOUT_S = 10.0
DB_PATH = "invest_helper.db"


def _replan_max(raw: str | None) -> int:
    try:
        return min(3, max(1, int(raw)))
    except (TypeError, ValueError):
        return 3


@dataclass(frozen=True)
class Settings:
    DATA_GO_KR_KEY: str = field(default="", repr=False)
    DART_API_KEY: str = field(default="", repr=False)
    GMS_API_KEY: str = field(default="", repr=False)
    GMS_BASE_URL: str = "https://gms.ssafy.io/gmsapi/api.anthropic.com/v1"
    GMS_MODEL: str = "claude-haiku-5-5"
    REPLAN_MAX: int = 3


def _load() -> Settings:
    g = os.getenv
    return Settings(
        DATA_GO_KR_KEY=g("DATA_GO_KR_KEY", ""),
        DART_API_KEY=g("DART_API_KEY", ""),
        GMS_API_KEY=g("GMS_API_KEY", ""),
        GMS_BASE_URL=g("GMS_BASE_URL") or Settings.GMS_BASE_URL,
        GMS_MODEL=g("GMS_MODEL") or Settings.GMS_MODEL,
        REPLAN_MAX=_replan_max(g("REPLAN_MAX")),
    )


settings = _load()
