"""D23: 모든 테스트를 실DB·전역 이벤트 상태로부터 격리. real_db 마커 테스트만 기본 DB_PATH 유지."""
import pytest

from backend import config as app_config
from backend.events import bus, logger_agent, stream


def pytest_configure(config):
    config.addinivalue_line("markers", "real_db: 기본(실) DB_PATH를 의도적으로 사용하는 테스트")


@pytest.fixture(autouse=True)
def _isolate_globals(tmp_path, request):
    # 전용 MonkeyPatch: 테스트가 자기 monkeypatch.undo()를 불러도 격리가 풀리지 않게 한다
    with pytest.MonkeyPatch.context() as mp:
        if request.node.get_closest_marker("real_db") is None:
            mp.setattr(app_config, "DB_PATH", str(tmp_path / "isolated.db"))
        mp.setattr(bus, "_subscribers", [])
        mp.setattr(bus, "_seq", {})
        mp.setattr(stream, "_sinks", {})
        mp.setattr(stream, "_installed", False)
        mp.setattr(logger_agent, "_installed", False)
        yield
