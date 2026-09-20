from __future__ import annotations

from frameprobe.devinfo import DEVINFO_COMMAND, collect_device_specs, parse_devinfo

from .conftest import FakeAdb, load


def test_samsung_s23() -> None:
    d = parse_devinfo(load("devinfo_samsung_s23.txt"))
    assert d.display_name == "samsung SM-S9110"
    assert d.soc == "QTI SM8550" and d.platform == "kalama"
    assert d.cpu_cores == 8 and d.cpu_summary == "8 核：1×3.36 + 4×2.80 + 3×2.02 GHz"
    assert d.gpu_name == "Adreno (TM) 740" and d.gles_version == "OpenGL ES 3.2"
    assert d.gpu_driver_version == "V@0676.77.1"
    assert d.gpu_summary == "Adreno (TM) 740（OpenGL ES 3.2, V@0676.77.1）"
    assert d.ram_mb == 7072
    assert (d.storage_total_gb, d.storage_free_gb) == (224.1, 72.6)
    assert d.screen_px == "1080x2340" and d.screen_dpi == 480
    assert d.refresh_rate_hz == 60 and d.refresh_rates_hz == [120, 96, 60, 48, 30, 24, 10]
    assert d.arr is False
    assert d.kernel.startswith("5.15.189") and d.selinux == "Enforcing" and d.is_rooted is False
    assert d.battery_design_mah is None and d.battery_cycles is None and d.battery_health_pct == 91
    assert d.os_summary == "Android 16 · API 36 · 安全性更新 2026-07-05"
    assert d.abi == "arm64-v8a" and d.build_type == "user"
    exported = d.to_dict()
    assert "raw" not in exported and exported["cpu_summary"].startswith("8 核")


def test_pixel11pro() -> None:
    d = parse_devinfo(load("devinfo_pixel11pro.txt"))
    assert d.display_name == "google Pixel 11 Pro"
    assert d.soc == "Google Tensor G6"
    assert d.cpu_summary == "7 核：1×4.11 + 4×3.38 + 2×2.65 GHz"
    # Vulkan RenderEngine 沒有 GLES 行 → 只有驅動名，不猜型號
    assert d.gpu_name == "" and d.gpu_summary == "powervr"
    assert d.ram_mb == 15655
    assert d.refresh_rate_hz == 120 and d.arr is True
    assert d.refresh_rates_hz[0] == 120 and d.refresh_rates_hz[-1] == 1
    assert d.battery_design_mah == 4960 and d.battery_cycles == 2 and d.battery_health_pct is None


def test_collect_and_missing() -> None:
    # Pixel 沒有 Samsung 的 Asoc 行 → grep 回 1 → 整條指令 rc=1，仍須解析成功
    adb = FakeAdb(responses={r"#props": (load("devinfo_pixel11pro.txt"), 1)})
    specs = collect_device_specs(adb)
    assert specs is not None and specs.model == "Pixel 11 Pro"
    assert collect_device_specs(FakeAdb()) is None
    assert "ro.soc.model" in DEVINFO_COMMAND and "dumpsys display" in DEVINFO_COMMAND
    assert DEVINFO_COMMAND.endswith("true")
    empty = parse_devinfo("#props\n")
    assert empty.cpu_summary == "" and empty.gpu_summary == "" and empty.refresh_rate_hz is None
