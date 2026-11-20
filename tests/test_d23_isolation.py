"""D23 B: 테스트 격리 가드."""
from pathlib import Path

import pytest

from backend import config
from backend.events import bus, logger_agent

ROOT = Path(__file__).resolve().parent.parent


def test_db_path_is_tmp():
    p = Path(config.DB_PATH)
    assert p.is_absolute() and ROOT not in p.resolve().parents


@pytest.mark.real_db
def test_real_db_marker_keeps_default():
    assert config.DB_PATH == "invest_helper.db"


class TestNoLeak:
    def test_a_install_leaks_nothing(self):
        logger_agent.install()
        assert bus._subscribers  # 이 테스트 안에서는 구독됨

    def test_b_clean_state(self):
        assert bus._subscribers == [] and logger_agent._installed is False
