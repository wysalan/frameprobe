from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from frameprobe.analysis import AnalysisError, analyze, build_record_summary, open_trace
from frameprobe.probe import DeviceInfo

NS = 1_000_000_000
T0 = 100 * NS
PKG = "com.example.game"


def present_times(fps: int, seconds: int, start: int = T0) -> list[int]:
    step = NS // fps
    return [start + i * step for i in range(fps * seconds + 1)]


class FakeQuery:
    """依 SQL 內容分派假結果。"""

    def __init__(self, *, app_frames: bool, thermal_tracks: bool) -> None:
        self.app_frames = app_frames
        self.thermal_tracks = thermal_tracks
        self.sql: list[str] = []

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.sql.append(sql)
        if "trace_bounds" in sql:
            return [{"start_ts": T0, "end_ts": T0 + 3 * NS}]
        is_app = PKG in sql
        if "total_presented_frames" in sql:
            if is_app and not self.app_frames:
                return [
                    {
                        "total_presented_frames": 0,
                        "average_fps": None,
                        "low_10_fps": None,
                        "low_1_fps": None,
                    }
                ]
            return [
                {
                    "total_presented_frames": 180,
                    "average_fps": 59.9,
                    "low_10_fps": 55.0,
                    "low_1_fps": 30.0,
                }
            ]
        if "ORDER BY present_ts" in sql:
            return [{"present_ts": ts} for ts in present_times(60, 3)]
        if "GROUP BY jank_type" in sql:
            if is_app and not self.app_frames:
                return []
            return [
                {"jank_type": "None", "cnt": 170},
                {"jank_type": "App Deadline Missed", "cnt": 10},
            ]
        if "jank_type != 'None'" in sql:
            return [
                {"ts": T0 + NS + i * 10_000_000, "jank_type": "App Deadline Missed"}
                for i in range(10)
            ]
        if "FROM counter_track" in sql:
            if not self.thermal_tracks:
                return [{"id": 1, "name": "cpufreq", "type": "cpu_frequency"}]
            return [
                {"id": 1, "name": "cpufreq", "type": "cpu_frequency"},
                {"id": 2, "name": "cpu0-silver-usr", "type": "thermal_temperature"},
                {"id": 3, "name": "skin-therm", "type": "thermal_temperature"},
            ]
        if "FROM counter WHERE track_id = 2" in sql:
            return [{"ts": T0, "value": 50000.0}, {"ts": T0 + NS, "value": 61900.0}]
        if "FROM counter WHERE track_id = 3" in sql:
            return [{"ts": T0, "value": 40000.0}]
        return []


def test_app_level_with_ftrace_thermal() -> None:
    query = FakeQuery(app_frames=True, thermal_tracks=True)
    result = analyze(query, package=PKG)
    assert result.app_level_available and result.app is not None
    assert result.jank_breakdown == {"None": 170, "App Deadline Missed": 10}
    assert result.duration_s == 3.0
    assert [s.frames for s in result.samples] == [60, 60, 60]
    assert result.samples[0].p90_fps == pytest.approx(1000 / 17)  # 16.67ms 四捨五入成 17
    assert result.samples[1].dropped == 10  # jank 落在第 2 秒
    assert result.samples[0].dropped == 0
    assert result.thermal_timeline == "exact"
    assert result.samples[0].cpu_c == 50.0 and result.samples[0].skin_c == 40.0
    assert result.samples[1].cpu_c == 61.9 and result.samples[1].skin_c == 40.0
    assert result.samples[2].thermal_stale is True
    assert result.samples[2].throttling_status is None  # ftrace 沒有 status

    summary = build_record_summary(
        result,
        session_id="sid",
        device=DeviceInfo("S"),
        package=PKG,
        started_at=datetime(2026, 1, 1),
    )
    assert summary.mode == "record" and summary.average_fps == 59.9 and summary.p90_fps == 55.0
    assert summary.dropped_frames == 10 and summary.peak_cpu_c == 61.9
    assert summary.time_to_throttle_s is None
    assert "app 層級" in summary.layer_name


def test_fallback_to_display_level_with_polled_thermal() -> None:
    query = FakeQuery(app_frames=False, thermal_tracks=False)
    poll = [
        {
            "elapsed": 0.5,
            "cpu_c": 45.0,
            "gpu_c": 40.0,
            "skin_c": 38.0,
            "battery_c": 30.0,
            "status": 0,
        },
        {
            "elapsed": 2.4,
            "cpu_c": 60.0,
            "gpu_c": 55.0,
            "skin_c": 44.0,
            "battery_c": 31.0,
            "status": 2,
        },
    ]
    result = analyze(query, package=PKG, thermal_poll=poll)
    assert result.app_level_available is False and result.app is None
    assert result.jank_breakdown is None
    assert any("回退到顯示層級" in n for n in result.notes)
    assert result.thermal_timeline == "approximate"
    s1, s2, s3 = result.samples
    assert s1.cpu_c == 45.0 and not s1.thermal_stale and s1.throttling_label == "NONE"
    assert s2.cpu_c == 45.0 and s2.thermal_stale
    assert s3.cpu_c == 60.0 and not s3.thermal_stale and s3.throttling_status == 2

    summary = build_record_summary(
        result,
        session_id="sid",
        device=DeviceInfo("S"),
        package=PKG,
        started_at=datetime(2026, 1, 1),
    )
    assert summary.app_level_available is False
    assert summary.time_to_throttle_s == 3.0
    assert summary.thermal_timeline == "approximate"
    assert "顯示層級" in summary.layer_name


def test_exact_jank_per_second() -> None:
    class Janky(FakeQuery):
        def __call__(self, sql: str) -> list[dict[str, Any]]:
            if "ORDER BY present_ts" in sql and "total_presented_frames" not in sql:
                # 第 1 秒 60 幀正常；第 2 秒開頭一幀 200ms（BigJank）、中段一幀 100ms（Jank）
                ts = present_times(60, 1)  # 61 個點
                t = ts[-1]
                seq = [t + 200_000_000]
                for _ in range(20):
                    seq.append(seq[-1] + 16_666_666)
                seq.append(seq[-1] + 100_000_000)
                for _ in range(20):
                    seq.append(seq[-1] + 16_666_666)
                return [{"present_ts": v} for v in ts + seq]
            return super().__call__(sql)

    result = analyze(Janky(app_frames=False, thermal_tracks=False), package=None)
    by_sec = {int(s.elapsed): s for s in result.samples}
    assert by_sec[1].jank == 0
    assert by_sec[2].jank == 2 and by_sec[2].big_jank == 1
    assert by_sec[2].stutter_ms == pytest.approx(300.0)
    assert by_sec[2].jank_method == "exact"
    summary = build_record_summary(
        result, session_id="s", device=DeviceInfo("S"), package="", started_at=datetime(2026, 1, 1)
    )
    assert summary.jank_total == 2 and summary.jank_method == "exact"


def test_no_package_and_no_thermal() -> None:
    result = analyze(
        FakeQuery(app_frames=True, thermal_tracks=False),
        package=None,
        thermal_timeline_hint="exact",
    )
    assert result.app is None and result.jank_breakdown is None and result.thermal_timeline is None
    assert any("找不到 thermal counter track" in n for n in result.notes)


def test_empty_trace_raises() -> None:
    class Empty(FakeQuery):
        def __call__(self, sql: str) -> list[dict[str, Any]]:
            if "total_presented_frames" in sql:
                return [{"total_presented_frames": 0}]
            return []

    with pytest.raises(AnalysisError, match="SurfaceFlinger"):
        analyze(Empty(app_frames=False, thermal_tracks=False), package=None)


def test_open_trace_missing_file() -> None:
    with pytest.raises(AnalysisError, match="找不到"), open_trace("/nonexistent/trace.pb"):
        pass


def test_open_trace_real_empty(tmp_path) -> None:
    """需要 trace_processor_shell（perfetto 套件第一次使用時會下載）。沒網路就跳過。"""
    empty = tmp_path / "empty.pb"
    empty.write_bytes(b"")
    try:
        with open_trace(empty) as query:
            assert query("SELECT COUNT(*) AS n FROM actual_frame_timeline_slice") == [{"n": 0}]
            with pytest.raises(AnalysisError):
                analyze(query, package=None)
    except AnalysisError as exc:
        pytest.skip(f"trace_processor 不可用：{exc}")


def test_display_level_uses_layer_null_not_process_name() -> None:
    from frameprobe.analysis import DISPLAY_WHERE, app_where

    assert "layer_name IS NULL" in DISPLAY_WHERE and "process" not in DISPLAY_WHERE
    assert "layer_name LIKE '%com.x%'" in app_where("com.x")


def test_sparse_app_level_falls_back_to_display_for_samples() -> None:
    class Sparse(FakeQuery):
        def __call__(self, sql: str) -> list[dict[str, Any]]:
            if "total_presented_frames" in sql and PKG in sql:
                return [
                    {
                        "total_presented_frames": 6,
                        "average_fps": 2.0,
                        "low_10_fps": 1.0,
                        "low_1_fps": 1.0,
                    }
                ]
            if "traced_buf_bytes_overwritten" in sql:
                return [{"name": "traced_buf_bytes_overwritten", "value": 614653952}]
            return super().__call__(sql)

    result = analyze(Sparse(app_frames=True, thermal_tracks=False), package=PKG)
    assert result.app_level_available and result.app is not None and result.app.total_frames == 6
    assert any("零星" in n for n in result.notes)
    assert any("buffer 溢位" in n and "615 MB" in n for n in result.notes)
    summary = build_record_summary(
        result, session_id="s", device=DeviceInfo("S"), package=PKG, started_at=datetime(2026, 1, 1)
    )
    assert summary.total_frames == 180 and "顯示層級" in summary.layer_name


def test_polls_are_end_aligned_when_trace_is_truncated() -> None:
    # trace 3 秒，poll 涵蓋 0～60 秒（buffer 覆寫掉前面）→ 只留最後 3 秒並平移
    poll = [
        {
            "elapsed": float(t),
            "cpu_c": 30.0 + t,
            "gpu_c": None,
            "skin_c": None,
            "battery_c": None,
            "status": 0,
        }
        for t in range(0, 61, 1)
    ]
    result = analyze(
        FakeQuery(app_frames=False, thermal_tracks=False), package=None, thermal_poll=poll
    )
    assert any("尾端對齊" in n for n in result.notes)
    # 第 3 秒的 sample 應對到原本第 60 秒的讀數（30+60）
    assert result.samples[-1].cpu_c == 90.0
    assert result.samples[0].cpu_c == 88.0


def test_gpu_memory_counter_is_not_a_temperature() -> None:
    """Pixel 11 Pro 實錄：trace 只有 process_gpu_memory（"GPU Memory"，bytes），沒有 thermal track。
    名稱含 gpu 不代表是溫度；要回退到 thermalservice 的輪詢，不能算出 61440 ÷ 1000 = 61.44°C。"""

    class GpuMemOnly(FakeQuery):
        def __call__(self, sql: str) -> list[dict[str, Any]]:
            if "FROM counter_track" in sql:
                return [{"id": 9, "name": "GPU Memory", "type": "process_gpu_memory"}]
            if "FROM counter WHERE track_id = 9" in sql:
                return [{"ts": T0 + i * NS, "value": 61440.0} for i in range(3)]
            return super().__call__(sql)

    poll = [
        {
            "elapsed": 0.5,
            "cpu_c": 95.0,
            "gpu_c": 63.0,
            "skin_c": 36.8,
            "battery_c": None,
            "status": 0,
        }
    ]
    result = analyze(
        GpuMemOnly(app_frames=False, thermal_tracks=False), package=PKG, thermal_poll=poll
    )
    assert result.thermal_timeline == "approximate"
    assert all(s.gpu_c == 63.0 and s.cpu_c == 95.0 for s in result.samples)
    assert not any("ftrace" in n for n in result.notes)


def test_ftrace_thermal_still_takes_throttle_status_from_poll() -> None:
    poll = [
        {"elapsed": 0.5, "cpu_c": 1.0, "gpu_c": 2.0, "skin_c": 3.0, "battery_c": None, "status": 2}
    ]
    result = analyze(
        FakeQuery(app_frames=True, thermal_tracks=True), package=PKG, thermal_poll=poll
    )
    assert result.thermal_timeline == "exact"
    s1 = result.samples[0]
    assert s1.cpu_c == 50.0 and s1.throttling_status == 2  # 溫度來自 ftrace，節流狀態來自 poll
