import math

import pytest

from backend.agent.constraints import (compute_metric_row, evaluate, failure_verdict,
                                       industry_avg_per, is_sample_short)

R0 = dict(revenue=1000, op_profit=100, net_income=80, assets=2000, equity=800, debt_ratio=150, mkt_cap=1600)
NOAVG = {"PER": {"value": None, "n": 0}}


def row(**kw):
    return compute_metric_row({**R0, **kw}, fiscal_period="2025", actual_basis="연결", estimated=False)


def C(metric, op, value=None, ref=None):
    return {"metric": metric, "op": op, "value": value, "ref": ref}


def V(r, m):
    return r["values"][m]


def test_1_base():
    r = compute_metric_row(R0, fiscal_period="2025", actual_basis="연결", estimated=True)
    exp = dict(PER=20, PBR=2, PSR=1.6, ROE=10, ROA=4, OPM=10, DEBT_RATIO=150)
    for k, v in exp.items():
        assert V(r, k) == pytest.approx(v)
    assert r["reasons"] == {}
    assert (r["fiscal_period"], r["actual_basis"], r["estimated"]) == ("2025", "연결", True)


def test_2_boundary_pass():
    assert evaluate(row(), [C("ROE", ">=", 10)], {})["status"] == "PASS"


def test_3_fail():
    assert evaluate(row(), [C("ROE", ">=", 10.0001)], {}) == {
        "status": "FAIL", "reasons": ["THRESHOLD_NOT_MET"], "failed": ["ROE"]}


def test_4_pass_two():
    assert evaluate(row(), [C("PER", "<=", 20), C("PBR", "<=", 2)], {})["status"] == "PASS"


def test_5_zero_income():
    r = row(net_income=0)
    assert V(r, "PER") is None and r["reasons"]["PER"] == "NEGATIVE_EARNINGS"
    assert V(r, "ROE") == 0 and V(r, "ROA") == 0


def test_6_negative_income():
    r = row(net_income=-50)
    assert V(r, "PER") is None and r["reasons"]["PER"] == "NEGATIVE_EARNINGS"
    assert V(r, "ROE") == pytest.approx(-6.25) and V(r, "ROA") == pytest.approx(-2.5)


@pytest.mark.parametrize("eq", [0, -100])
def test_7_equity(eq):
    r = row(equity=eq)
    for m in ("PBR", "ROE", "DEBT_RATIO"):
        assert V(r, m) is None and r["reasons"][m] == "NEGATIVE_EQUITY"


def test_8_revenue_zero():
    r = row(revenue=0)
    for m in ("OPM", "PSR"):
        assert V(r, m) is None and r["reasons"][m] == "NONPOSITIVE_REVENUE"


def test_9_assets_zero():
    r = row(assets=0)
    assert V(r, "ROA") is None and r["reasons"]["ROA"] == "NONPOSITIVE_ASSETS"


def test_10_no_mkt_cap():
    r = row(mkt_cap=None)
    for m in ("PER", "PBR", "PSR"):
        assert V(r, m) is None and r["reasons"][m] == "NO_MARKET_CAP"
    assert [V(r, m) for m in ("ROE", "ROA", "OPM", "DEBT_RATIO")] == [10, 4, 10, 150]


def test_11_no_net_income():
    r = row(net_income=None)
    for m in ("PER", "ROE", "ROA"):
        assert V(r, m) is None and r["reasons"][m] == "NO_FINANCIALS"


def test_12_negative_opm():
    r = row(op_profit=-100)
    assert V(r, "OPM") == pytest.approx(-10)
    assert evaluate(r, [C("OPM", ">=", 5)], {})["status"] == "FAIL"
    assert evaluate(r, [C("OPM", ">=", -10)], {})["status"] == "PASS"


def rows(**per):
    return {k: {"values": {"PER": v}} for k, v in per.items()}


def test_13_median():
    assert industry_avg_per(rows(A=10, B=20, C=30, D=None)) == {"value": 20, "n": 3}
    assert is_sample_short(3) is False


def test_14_short_and_even():
    assert industry_avg_per(rows(A=10, B=20)) == {"value": 15, "n": 2}
    assert is_sample_short(2) is True
    assert industry_avg_per(rows(A=10, B=20, C=30, D=40)) == {"value": 25, "n": 4}


def test_15_all_none():
    assert industry_avg_per(rows(A=None, B=None)) == {"value": None, "n": 0}


def test_16_17_18_ref():
    c = [C("PER", "<=", ref="industry_avg")]
    assert evaluate(row(), c, {"PER": {"value": 20, "n": 3}})["status"] == "PASS"
    assert evaluate(row(), c, {"PER": {"value": 15, "n": 2}})["status"] == "FAIL"
    assert evaluate(row(), c, NOAVG) == {"status": "UNDECIDABLE",
                                         "reasons": ["INDUSTRY_AVG_UNAVAILABLE"], "failed": []}


def test_19_empty():
    assert evaluate(row(), [], {}) is None


def test_20_fail_plus_undecidable():
    v = evaluate(row(net_income=-50), [C("ROE", ">=", 10), C("PER", "<=", 15)], {})
    assert v == {"status": "FAIL", "reasons": ["THRESHOLD_NOT_MET", "NEGATIVE_EARNINGS"], "failed": ["ROE"]}


def test_21_two_reasons():
    v = evaluate(row(net_income=-50), [C("PER", "<=", ref="industry_avg")], NOAVG)
    assert v == {"status": "UNDECIDABLE",
                 "reasons": ["NEGATIVE_EARNINGS", "INDUSTRY_AVG_UNAVAILABLE"], "failed": []}


def test_22_partial_undecidable():
    v = evaluate(row(mkt_cap=None), [C("ROE", ">=", 10), C("PBR", "<=", 3)], {})
    assert v == {"status": "UNDECIDABLE", "reasons": ["NO_MARKET_CAP"], "failed": []}


def test_23_failure_verdict():
    assert failure_verdict("FETCH_FAILED") == {"status": "UNDECIDABLE",
                                               "reasons": ["FETCH_FAILED"], "failed": []}


def test_24_nan_income():
    r = row(net_income=math.nan)
    for m in ("PER", "ROE", "ROA"):
        assert V(r, m) is None and r["reasons"][m] == "NO_FINANCIALS"


def test_25_nan_mkt_cap():
    r = row(mkt_cap=math.nan)
    for m in ("PER", "PBR", "PSR"):
        assert V(r, m) is None and r["reasons"][m] == "NO_MARKET_CAP"


def test_26_bad_constraint():
    v = evaluate(row(), [C("ROE", ">=")], {})
    assert v == {"status": "UNDECIDABLE", "reasons": ["NO_FINANCIALS"], "failed": []}


def test_27_fail_only_pbr():
    v = evaluate(row(), [C("ROE", ">=", 5), C("PBR", "<=", 1)], {})
    assert v == {"status": "FAIL", "reasons": ["THRESHOLD_NOT_MET"], "failed": ["PBR"]}
