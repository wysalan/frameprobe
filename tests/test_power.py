from __future__ import annotations

import pytest

from frameprobe.power import (
    POWER_COMMAND,
    PowerCollector,
    PowerError,
    parse_dumpsys_battery,
    parse_power,
)

from .conftest import FakeAdb, load


def test_parse_samsung_s23() -> None:
    p = parse_dumpsys_battery(load("dumpsys_battery_samsung_s23.txt"))
    assert p.voltage_v == pytest.approx(3.908)
    assert p.current_ma == 559.0
    assert p.plugged is True  # USB powered: true
    assert p.level == 44 and p.charge_counter_uah == 1633905
    assert p.status == 2  # charging
    assert p.power_w == pytest.approx(3.908 * 0.559, abs=1e-3)


def test_aosp_without_current_and_unplugged() -> None:
    text = "  AC powered: false\n  USB powered: false\n  level: 80\n  voltage: 4100\n"
    p = parse_dumpsys_battery(text)
    assert p.current_ma is None and p.power_w is None and p.plugged is False
    with pytest.raises(PowerError):
        parse_dumpsys_battery("level: 50")


def test_parse_power_pixel_sysfs_preferred() -> None:
    p = parse_power(load("power_pixel11pro.txt"))
    assert p.source == "sysfs"
    assert p.level == 100 and p.status == 4
    assert p.current_ma == pytest.approx(-11.718)
    assert p.voltage_v == pytest.approx(4.446015)
    assert p.plugged is True and p.level == 100
    assert p.power_w == pytest.approx(-0.052, abs=0.001)


def test_parse_power_falls_back_to_dumpsys() -> None:
    text = (
        "#sysfs\ncat: /sys/class/power_supply/battery/current_now: Permission denied\n"
        "cat: /sys/class/power_supply/battery/voltage_now: Permission denied\n#dumpsys\n"
        + load("dumpsys_battery_samsung_s23.txt")
    )
    p = parse_power(text)
    assert p.source == "dumpsys" and p.current_ma == 559.0
    with pytest.raises(PowerError):
        parse_power("#sysfs\nnope\n#dumpsys\nlevel: 3\n")


def test_collector() -> None:
    adb = FakeAdb(responses={r"dumpsys battery": (load("dumpsys_battery_samsung_s23.txt"), 0)})
    assert "current_now" in POWER_COMMAND
    c = PowerCollector(adb, min_interval=60)
    first = c.read()
    assert first is not None and c.read() is first and len(c.raw_snapshots) == 1
    assert PowerCollector(FakeAdb()).read() is None
