from __future__ import annotations

import pytest

from frameprobe.meminfo import MemDetailCollector, MeminfoError, parse_meminfo

from .conftest import FakeAdb, load


def test_parse_samsung_s23() -> None:
    d = parse_meminfo(load("meminfo_samsung_s23.txt"))
    assert d.pid == 18345
    assert d.total_pss_mb == pytest.approx(3229008 / 1024)
    assert d.total_rss_mb == pytest.approx(1683588 / 1024)
    assert d.total_swap_pss_mb == pytest.approx(1601537 / 1024)
    assert d.summary_mb["java_heap"] == pytest.approx(9584 / 1024)
    assert d.summary_mb["graphics"] == pytest.approx(449124 / 1024)
    assert d.summary_mb["system"] == pytest.approx(1605920 / 1024)
    assert "unknown" not in d.summary_mb  # App Summary 的 Unknown 只有 Rss 欄，不當 PSS
    assert d.table_mb["gl_mtrack"] == pytest.approx(422132 / 1024)
    assert d.table_mb["egl_mtrack"] == pytest.approx(26992 / 1024)
    assert d.table_mb["native_heap"] == pytest.approx(468807 / 1024)
    assert d.table_mb["unknown"] == pytest.approx(452130 / 1024)
    exported = d.to_dict()
    assert {
        "java_heap",
        "graphics",
        "gl_mtrack",
        "egl_mtrack",
        "total_pss",
        "total_swap_pss",
    } <= set(exported)


def test_parse_errors() -> None:
    with pytest.raises(MeminfoError):
        parse_meminfo("No process found for: com.x\n")
    with pytest.raises(MeminfoError):
        parse_meminfo("")


def test_collector_caching_and_failure() -> None:
    adb = FakeAdb(
        responses={r"dumpsys meminfo 'com.example.game'": (load("meminfo_samsung_s23.txt"), 0)}
    )
    c = MemDetailCollector(adb, "com.example.game", min_interval=60)
    first = c.read()
    assert first is not None and c.read() is first
    assert len(c.raw_snapshots) == 1
    adb.responses[r"dumpsys meminfo 'com.example.game'"] = ("No process found", 0)
    assert c.read(force=True) is first and c.failures == 1
    assert MemDetailCollector(FakeAdb(), "x").read() is None
