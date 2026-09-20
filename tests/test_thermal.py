from __future__ import annotations

import pytest

from frameprobe.thermal import (
    TemperatureReading,
    ThermalCollector,
    ThermalError,
    ThrottlingStatus,
    normalize_sysfs_temp,
    parse_battery,
    parse_hardware_properties,
    parse_sysfs_zones,
    parse_thermalservice,
)

from .conftest import FakeAdb, load


def test_thermalservice_pixel() -> None:
    snap = parse_thermalservice(load("thermalservice_pixel.txt"))
    assert snap.status is ThrottlingStatus.MODERATE
    assert snap.status.is_throttling
    # HAL 區段優先於 cached（cached 的 cpu0 是 41.2）
    names = {r.name: r for r in snap.readings}
    assert names["cpu0-silver-usr"].celsius == 58.4
    # 同類型取 max：cpu1-gold 61.9 > cpu0 58.4
    assert snap.cpu_c == 61.9
    assert snap.gpu_c == 57.3
    assert snap.skin_c == 44.1
    assert snap.npu_c is None and snap.to_dict()["npu_c"] is None
    # 0.0 sentinel 不影響 battery
    assert snap.battery_c == 36.8
    # mType=6 是 BCL 電壓，不是溫度
    assert names["vbat"].is_temperature is False
    assert snap.hottest is not None and snap.hottest.name == "cpu1-gold-usr"
    # 未知型別回退成 TYPE_<n>；13 有對照表則是 SOC
    assert names["soc-therm"].type_name == "SOC"
    assert TemperatureReading("x", 42, 30.0).type_name == "TYPE_42"
    exported = snap.to_dict()
    assert exported["status_label"] == "MODERATE"
    assert all(r["celsius"] > 0 for r in exported["readings"])
    assert "vbat" not in {r["name"] for r in exported["readings"]}


def test_thermalservice_without_hal_uses_cached_or_raises() -> None:
    snap = parse_thermalservice(
        "Thermal Status: 0\nCached temperatures:\n"
        "\tTemperature{mValue=30.0, mType=3, mName=x, mStatus=0}\n"
    )
    assert snap.skin_c == 30.0 and snap.status is ThrottlingStatus.NONE
    with pytest.raises(ThermalError):
        parse_thermalservice("nothing here")
    assert ThrottlingStatus.parse(99) is None


def test_hardware_properties() -> None:
    snap = parse_hardware_properties(load("hardware_properties.txt"))
    assert snap.cpu_c == 45.25
    assert snap.gpu_c == 39.0
    assert snap.battery_c == 30.0
    assert snap.skin_c == 33.5
    assert snap.status is None
    with pytest.raises(ThermalError):
        parse_hardware_properties("")


def test_sysfs_unit_inference_order() -> None:
    assert normalize_sysfs_temp(44) == 44.0
    assert normalize_sysfs_temp(368) == 36.8
    assert normalize_sysfs_temp(58400) == 58.4
    assert normalize_sysfs_temp(0) is None
    assert normalize_sysfs_temp(999999) is None


def test_sysfs_zones() -> None:
    snap = parse_sysfs_zones(load("sysfs_zones.txt"))
    assert snap.cpu_c == 58.4
    assert snap.gpu_c == 57.3
    assert snap.battery_c == 36.8
    assert snap.skin_c == 44.0
    # thermal_zone9 raw=0 推不出單位 → 丟棄
    assert len(snap.readings) == 4
    with pytest.raises(ThermalError):
        parse_sysfs_zones("thermal_zone0 x 0\n")


def test_battery() -> None:
    snap = parse_battery(load("dumpsys_battery_samsung_s23.txt"))
    assert snap.battery_c == 42.2
    assert snap.cpu_c is None
    with pytest.raises(ThermalError):
        parse_battery("level: 50")


def test_collector_source_priority_and_stale_cache() -> None:
    adb = FakeAdb(
        responses={
            r"dumpsys thermalservice": ("garbage", 0),
            r"dumpsys hardware_properties": (load("hardware_properties.txt"), 0),
        }
    )
    collector = ThermalCollector(adb, min_interval=60.0)
    assert collector.detect_source() == "hardware_properties"
    first = collector.read()
    assert first is not None and first.source == "hardware_properties"
    # 未滿 min_interval → 回傳快取物件
    assert collector.read() is first
    assert len(collector.raw_snapshots) == 1
    # 之後失敗也不拋例外，沿用舊值
    adb.responses[r"dumpsys hardware_properties"] = ("", 1)
    assert collector.read(force=True) is first


def test_collector_no_source() -> None:
    with pytest.raises(ThermalError):
        ThermalCollector(FakeAdb()).detect_source()
