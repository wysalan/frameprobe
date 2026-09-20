from __future__ import annotations

import pytest

from frameprobe.launch import LaunchError, measure_launch, parse_am_start

from .conftest import FakeAdb

AM_OUT = """Starting: Intent { act=android.intent.action.MAIN cmp=com.example.game/.MainActivity }
Status: ok
LaunchState: COLD
Activity: com.example.game/.MainActivity
TotalTime: 812
WaitTime: 830
Complete
"""


def make_adb() -> FakeAdb:
    return FakeAdb(
        responses={
            r"cmd package resolve-activity": (
                "priority=0 preferredOrder=0 match=0x108000\ncom.example.game/.MainActivity\n",
                0,
            ),
            r"^am force-stop": ("", 0),
            r"^am start -W -n 'com.example.game/.MainActivity'": (AM_OUT, 0),
        }
    )


def test_parse() -> None:
    assert parse_am_start(AM_OUT)["TotalTime"] == "812"
    assert parse_am_start("garbage") == {}


def test_measure_cold_and_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    import frameprobe.launch as mod

    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    adb = make_adb()
    r = measure_launch(adb, "com.example.game", cold=True, repeat=3)
    assert r.component == "com.example.game/.MainActivity"
    assert r.total_ms == 812 and r.wait_ms == 830 and r.launch_state == "COLD"
    assert len(r.runs) == 3 and sum(1 for c in adb.calls if c.startswith("am force-stop")) == 3
    assert "TotalTime: 812" in r.raw
    assert r.to_dict()["total_ms"] == 812

    warm = measure_launch(make_adb(), "com.example.game", cold=False)
    assert warm.cold is False


def test_measure_errors() -> None:
    with pytest.raises(LaunchError, match="啟動 Activity"):
        measure_launch(FakeAdb(), "com.nothere")
    adb = make_adb()
    adb.responses[r"^am start -W -n 'com.example.game/.MainActivity'"] = ("Status: timeout\n", 0)
    with pytest.raises(LaunchError, match="無法解析"):
        measure_launch(adb, "com.example.game", cold=False)
