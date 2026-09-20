from __future__ import annotations

from datetime import datetime

import pytest

from frameprobe.probe import DeviceInfo
from frameprobe.sampler import Sample
from frameprobe.summary import merge_histograms, summarize_realtime

DEVICE = DeviceInfo("S", sdk_int=37)


def sample(
    elapsed: float, frames: int, hist: dict[int, int], status: int | None, cpu: float | None = None
) -> Sample:
    return Sample(
        ts=elapsed,
        elapsed=elapsed,
        frames=frames,
        fps=frames / 1.0,
        p90_fps=None,
        p99_fps=None,
        dropped=1,
        dropped_ratio=0.0,
        histogram=hist,
        cpu_c=cpu,
        skin_c=None,
        throttling_status=status,
    )


def summarize(samples: list[Sample]):
    return summarize_realtime(
        samples,
        session_id="sid",
        device=DEVICE,
        package="pkg",
        layer_name="layer",
        started_at=datetime(2026, 1, 1),
    )


def test_average_is_frames_over_time_not_mean_of_fps() -> None:
    # 1 秒 120 幀 + 3 秒 90 幀：總幀數 ÷ 總時間 = 52.5；錯誤地平均 sample.fps 會得到 105
    samples = [
        sample(1.0, 120, {8: 120}, 0, cpu=40.0),
        sample(4.0, 90, {33: 90}, 2, cpu=55.0),  # 3 秒區間
    ]
    s = summarize(samples)
    assert s.total_frames == 210
    assert s.duration_s == 4.0
    assert s.average_fps == pytest.approx(52.5)
    assert s.dropped_frames == 2
    assert merge_histograms(samples).buckets == {8: 120, 33: 90}
    assert s.p90_fps == pytest.approx(1000 / 33)
    assert s.p99_fps == pytest.approx(1000 / 33)
    assert s.peak_cpu_c == 55.0 and s.peak_gpu_c is None
    assert s.time_to_throttle_s == 4.0
    assert s.throttle_duration_s == 3.0
    assert s.throttle_timeline == [(1.0, 0), (4.0, 2)]


def test_never_throttled_and_empty() -> None:
    s = summarize([sample(1.0, 60, {16: 60}, 0), sample(2.0, 60, {16: 60}, 1)])
    assert s.time_to_throttle_s is None
    assert s.throttle_duration_s == 0.0
    assert s.throttle_timeline == [(1.0, 0), (2.0, 1)]
    assert s.average_fps == pytest.approx(60.0)

    empty = summarize([])
    assert empty.total_frames == 0 and empty.average_fps is None and empty.p90_fps is None


def test_no_thermal_data() -> None:
    s = summarize([sample(1.0, 60, {16: 60}, None)])
    assert s.throttle_timeline == [] and s.time_to_throttle_s is None and s.peak_cpu_c is None


def test_zero_duration_falls_back_to_histogram_mean() -> None:
    s = summarize([sample(0.0, 10, {20: 10}, None)])
    assert s.average_fps == pytest.approx(50.0)


def test_jank_summary() -> None:
    a = sample(1.0, 60, {16: 58, 86: 1, 150: 1}, None)
    a.jank, a.big_jank, a.stutter_ms = 2, 1, 236.0
    b = sample(2.0, 60, {16: 60}, None)
    s = summarize([a, b])
    assert (s.jank_total, s.big_jank_total) == (2, 1)
    assert s.jank_per_10min == pytest.approx(2 / 2 * 600)
    assert s.stutter_ratio == pytest.approx(236 / 2000)
    assert s.jank_method == "histogram"


def test_stability_summary() -> None:
    samples = [
        sample(1.0, 60, {16: 60}, None),
        sample(2.0, 50, {20: 50}, None),  # 掉 10 → Drop
        sample(3.0, 45, {22: 44, 120: 1}, None),  # 掉 5 → 不算；一幀 >100ms
    ]
    s = summarize(samples)
    assert s.drop_fps == 1
    assert s.delta_ftime == 1
    # 155 幀的 1% = 1.55 幀 → 1 幀 120ms + 0.55 幀 22ms
    assert s.low_1_fps == pytest.approx(1000 / ((120 + 22 * 0.55) / 1.55))
    fps = [60, 50, 45]
    mean = sum(fps) / 3
    assert s.fps_std == pytest.approx((sum((f - mean) ** 2 for f in fps) / 3) ** 0.5)
    assert s.ftime_avg_ms == pytest.approx((16 * 60 + 20 * 50 + 22 * 44 + 120) / 155)
    assert s.ftime_std_ms is not None and s.ftime_std_ms > 0
    single = summarize([sample(1.0, 60, {16: 60}, None)])
    assert single.fps_std is None and single.drop_fps == 0


def test_battery_summary() -> None:
    a = sample(1.0, 60, {16: 60}, None)
    a.battery_level_pct, a.battery_charge_mah, a.battery_status = 95, 3538.3, 3
    b = sample(2.0, 60, {16: 60}, None)
    b.battery_level_pct, b.battery_charge_mah, b.battery_status = 94, 3500.0, 3
    c = sample(3.0, 60, {16: 60}, None)  # 沒讀到電池的取樣不影響
    s = summarize([a, b, c])
    assert s.battery_level_start == 95 and s.battery_level_end == 94
    assert s.battery_consumed_mah == pytest.approx(38.3)
    assert s.battery_status == 3
    assert summarize([c]).battery_level_start is None


def test_power_summary_unplugged() -> None:
    a = sample(1.0, 60, {16: 60}, None)
    a.battery_plugged, a.battery_power_w, a.fpower_mw = False, -3.0, 50.0
    b = sample(2.0, 60, {16: 60}, None)
    b.battery_plugged, b.battery_power_w = True, 2.0
    s = summarize([a, b])
    assert s.plugged_ratio == 0.5
    assert s.avg_power_w == 3.0 and s.avg_fpower_mw == 50.0
    assert any("只統計未接電源" in n for n in s.notes)
