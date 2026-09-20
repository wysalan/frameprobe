from __future__ import annotations

import pytest

from frameprobe.jank import JankStats, jank_flags, jank_from_frame_times, jank_from_histogram


def test_exact_definition() -> None:
    # 16,16,16 之後 100ms：> 2×16 且 > 83.3 → Jank；130ms → BigJank
    frames = [16.0, 16.0, 16.0, 100.0, 16.0, 16.0, 16.0, 130.0, 16.0, 16.0, 16.0, 50.0]
    stats = jank_from_frame_times(frames)
    assert (stats.jank, stats.big_jank) == (2, 1)
    assert stats.stutter_ms == pytest.approx(230.0)
    assert stats.method == "exact"
    assert jank_flags(frames) == [0, 0, 0, 1, 0, 0, 0, 2, 0, 0, 0, 0]
    # 前 3 幀本來就慢（各 60ms）→ 100ms 不到 2 倍，不算 Jank
    assert jank_from_frame_times([60.0, 60.0, 60.0, 100.0]).jank == 0
    # 不足 4 幀
    assert jank_from_frame_times([200.0, 200.0]).jank == 0


def test_histogram_approximation() -> None:
    stats = jank_from_histogram({16: 850, 33: 100, 86: 3, 150: 2})
    assert (stats.jank, stats.big_jank) == (5, 2)
    assert stats.stutter_ms == pytest.approx(86 * 3 + 150 * 2)
    assert stats.method == "histogram"
    assert jank_from_histogram({16: 60}).jank == 0


def test_merge() -> None:
    a = JankStats(1, 0, 90.0, "exact")
    b = JankStats(2, 1, 300.0, "histogram")
    merged = a.merge(b)
    assert (merged.jank, merged.big_jank, merged.stutter_ms, merged.method) == (
        3,
        1,
        390.0,
        "mixed",
    )
    assert a.merge(a).method == "exact"
