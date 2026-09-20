from __future__ import annotations

import pytest

from frameprobe.sysstats import (
    SysStatsCollector,
    SysStatsError,
    build_command,
    cpu_usage,
    parse_sysstats,
)

from .conftest import FakeAdb, load


def test_parse_a() -> None:
    snap = parse_sysstats(load("sysstats_a.txt"))
    assert snap.pid == 4321
    assert snap.cores["cpu"] == (100000 + 2000 + 50000 + 1000 + 2000, 960000)
    assert len(snap.cores) == 5
    assert snap.app_ticks == 4000
    assert snap.freq_khz["cpu2"] == (2400000, 3000000)
    assert (snap.pss_kb, snap.rss_kb, snap.swap_kb) == (180000, 240000, 12000)
    assert snap.pss_error is None
    assert snap.mem_available_kb == 2500000 and snap.mem_total_kb == 7800000
    assert snap.gpu_busy_pct == pytest.approx(1234 / 5000 * 100)
    assert snap.gpu_freq_mhz == 680 and snap.gpu_error is None
    assert snap.gpu_mem_kb == 333565952 // 1024
    assert (snap.voluntary_ctxt, snap.nonvoluntary_ctxt) == (1500, 250)  # 兩個 thread 相加
    assert (snap.net_rx_bytes, snap.net_tx_bytes) == (1_000_000, 200_000)  # lo 排除
    assert snap.gpu_temp_c == 60.9


def test_parse_b_falls_back_to_statm_rss_visibly() -> None:
    snap = parse_sysstats(load("sysstats_b.txt"))
    assert snap.pss_kb is None
    assert snap.pss_error is not None and "Permission denied" in snap.pss_error
    assert snap.rss_kb == 61000 * 4  # statm 第 2 欄 resident pages × 4 KB
    # gpubusy "0 0"：整段 power collapse → 0%；頻率全部不可讀 → None
    assert snap.gpu_busy_pct == 0.0 and snap.gpu_freq_mhz is None


def test_gpu_missing_or_denied() -> None:
    text = load("sysstats_a.txt").replace(
        "   1234    5000", "cat: /sys/class/kgsl/kgsl-3d0/gpubusy: No such file or directory"
    )
    snap = parse_sysstats(text)
    assert (
        snap.gpu_busy_pct is None
        and snap.gpu_error is not None
        and "No such file" in snap.gpu_error
    )
    # kgsl 的 clock_mhz 可讀時直接是 MHz
    text = load("sysstats_a.txt").replace(
        "clock_mhz cat: /sys/class/kgsl/kgsl-3d0/clock_mhz: Permission denied",
        "clock_mhz 545",
    )
    assert parse_sysstats(text).gpu_freq_mhz == 545


def test_cpu_usage_delta() -> None:
    a = parse_sysstats(load("sysstats_a.txt"), ts=100.0)
    b = parse_sysstats(load("sysstats_b.txt"), ts=101.0)
    usage = cpu_usage(a, b)
    # 總 tick 增加 900，busy 增加 400
    assert usage.total_pct == pytest.approx(400 / 900 * 100)
    # 程序 +160 tick × 4 核 / 900
    assert usage.app_pct == pytest.approx(160 * 4 / 900 * 100)
    # 頻率 (1.8+1.8+3.0+3.0)/(2+2+3+3)
    assert usage.app_norm_pct == pytest.approx(usage.app_pct * 9.6 / 10)
    assert usage.freq_mhz == [1800, 1800, 3000, 3000]
    assert (usage.wakeups, usage.cswitch) == (400, 470)
    dt = b.ts - a.ts
    assert usage.net_rx_kbps == pytest.approx(512_000 / 1024 / dt)
    assert usage.net_tx_kbps == pytest.approx(51_200 / 1024 / dt)
    assert b.gpu_temp_c is None
    # 反向（計數器重置）→ 無法計算
    assert cpu_usage(b, a).total_pct is None


def test_missing_pid_and_garbage() -> None:
    text = load("sysstats_a.txt").replace("#pid 4321", "#pid ").replace("#pstat\n4321", "#pstat\n")
    snap = parse_sysstats(text)
    assert snap.pid is None and snap.app_ticks is None and snap.pss_error is None
    with pytest.raises(SysStatsError):
        parse_sysstats("nothing")


def test_collector_baseline_then_delta_and_failures() -> None:
    # 沒有 kgsl 的裝置最後一段 cat 會失敗 → rc=1，但輸出完整，必須照常解析
    adb = FakeAdb(responses={r"/proc/stat": (load("sysstats_a.txt"), 1)})
    collector = SysStatsCollector(adb, "com.example.game")
    assert "pidof -s 'com.example.game'" in build_command("com.example.game")
    first = collector.read()
    assert first is not None and first[0] is None and first[1].pid == 4321
    adb.responses[r"/proc/stat"] = (load("sysstats_b.txt"), 0)
    second = collector.read()
    assert second is not None and second[0] is not None
    assert second[0].total_pct == pytest.approx(400 / 900 * 100)
    assert len(collector.raw_snapshots) == 2
    adb.responses[r"/proc/stat"] = ("", 1)
    assert collector.read() is None and collector.failures == 1


def test_gpu_devfreq_and_gpu_mem() -> None:
    text = (
        load("sysstats_a.txt")
        .replace(
            "/sys/class/kgsl/kgsl-3d0/gpuclk 680000000",
            "/sys/class/kgsl/kgsl-3d0/gpuclk cat: no",
        )
        .replace(
            "/sys/class/kgsl/kgsl-3d0/devfreq/cur_freq 680000000",
            "/sys/class/kgsl/kgsl-3d0/devfreq/cur_freq cat: no\n"
            "/sys/class/devfreq/34f00000.gpu0/cur_freq 460000000",
        )
    )
    text += "#gpumem\nProc 4321 total: 333565952\n"
    snap = parse_sysstats(text)
    assert snap.gpu_freq_mhz == 460
    assert snap.gpu_mem_kb == 333565952 // 1024
    assert "devfreq/*gpu*/cur_freq" in build_command("x")
