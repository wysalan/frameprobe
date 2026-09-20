from __future__ import annotations

import pytest

from frameprobe.adb import FrameprobeError
from frameprobe.probe import (
    CheckStatus,
    DeviceInfo,
    check_frametimeline,
    check_perfetto_binary,
    check_screen_state,
    check_thermal,
    check_timestats,
    check_trace_processor,
    check_traced_running,
    collect_device_info,
    probe,
)

from .conftest import FakeAdb, load

PROPS = {
    "ro.build.version.sdk": "37",
    "ro.build.version.release": "17",
    "ro.product.model": "Pixel 10",
    "ro.product.manufacturer": "Google",
    "init.svc.traced": "running",
}


def healthy_adb() -> FakeAdb:
    return FakeAdb(
        props=dict(PROPS),
        responses={
            r"^id$": ("uid=2000(shell) gid=2000(shell)", 0),
            r"timestats -clear -enable": ("", 0),
            r"timestats -dump": (load("timestats_android17.txt"), 0),
            r"timestats -disable": ("", 0),
            r"dumpsys thermalservice": (load("thermalservice_pixel.txt"), 0),
            r"perfetto --version": ("Perfetto v49.0", 0),
            r"perfetto -c - --txt -o /data/misc/perfetto-traces/frameprobe-doctor.pb": (
                "frameprobe-ok",
                0,
            ),
            r"dumpsys power": ("mWakefulness=Awake", 0),
            r"#props": (load("devinfo_samsung_s23.txt"), 0),
        },
    )


def test_device_info() -> None:
    info = collect_device_info(healthy_adb())
    assert info.sdk_int == 37 and info.model == "Pixel 10" and not info.is_rooted
    assert "Android 17 / API 37" in info.label
    assert info.specs is not None and info.specs.soc == "QTI SM8550"
    assert collect_device_info(healthy_adb(), specs=False).specs is None
    rooted = healthy_adb()
    rooted.responses[r"^id$"] = ("uid=0(root) gid=0(root)", 0)
    assert collect_device_info(rooted).is_rooted
    with pytest.raises(FrameprobeError):
        collect_device_info(FakeAdb())


def test_full_probe_pass() -> None:
    report = probe(healthy_adb(), run_timestats=True)
    assert report.realtime_available
    assert report.record_available
    assert report.blocking_reason() is None
    assert report.get("timestats") is not None
    assert report.get("timestats").status is CheckStatus.PASS
    assert report.layer_candidates and "com.example.game" in report.layer_candidates[0]
    thermal = report.get("thermal")
    assert thermal is not None and thermal.status is CheckStatus.PASS
    assert "CPU=61.9°C" in thermal.summary and "MODERATE" in thermal.summary
    assert report.get("sdk_known") is None


def test_probe_skips_timestats() -> None:
    report = probe(healthy_adb(), run_timestats=False)
    assert report.get("timestats").status is CheckStatus.SKIP
    assert not report.realtime_available


def test_timestats_check_failures() -> None:
    adb = healthy_adb()
    adb.responses[r"timestats -clear -enable"] = ("", 1)
    assert check_timestats(adb, warmup_s=0)[0].status is CheckStatus.FAIL

    adb = healthy_adb()
    adb.responses[r"timestats -dump"] = ("", 1)
    assert check_timestats(adb, warmup_s=0)[0].status is CheckStatus.FAIL

    adb = healthy_adb()
    adb.responses[r"timestats -dump"] = ("not a dump", 0)
    result, layers = check_timestats(adb, warmup_s=0)
    assert result.status is CheckStatus.FAIL and "無法解析" in result.summary and layers == []

    adb = healthy_adb()
    adb.responses[r"timestats -dump"] = ("layerName = foo\ntotalFrames = 0\n", 0)
    result, layers = check_timestats(adb, warmup_s=0)
    assert result.status is CheckStatus.WARN and layers == ["foo"]

    adb = healthy_adb()
    adb.responses[r"timestats -dump"] = ("layerName = foo\ntotalFrames = 10\naverageFPS = 60\n", 0)
    result, _ = check_timestats(adb, warmup_s=0)
    assert result.status is CheckStatus.WARN and "present-to-present" in result.summary
    # 檢查結束後一定關掉 timestats
    assert any("-disable" in c for c in adb.calls)


def test_perfetto_and_traced_checks() -> None:
    assert check_perfetto_binary(healthy_adb()).summary.startswith("Perfetto v49.0；已實際錄製")
    assert check_perfetto_binary(FakeAdb()).status is CheckStatus.FAIL
    # 二進位存在但錄不了（例如 SELinux 擋寫入）→ FAIL 且附原始輸出
    adb = healthy_adb()
    adb.responses[r"perfetto -c - --txt -o /data/misc/perfetto-traces/frameprobe-doctor.pb"] = (
        "",
        1,
    )
    result = check_perfetto_binary(adb)
    assert (
        result.status is CheckStatus.FAIL
        and "無法錄製" in result.summary
        and "exit=1" in result.detail
    )

    assert check_traced_running(healthy_adb()).status is CheckStatus.PASS

    adb = healthy_adb()
    adb.props["init.svc.traced"] = "stopped"
    adb.responses[r"setprop persist.traced.enable 1"] = ("", 0)
    import frameprobe.probe as probe_mod

    probe_mod.time.sleep = lambda _s: None  # type: ignore[assignment]
    assert check_traced_running(adb).status is CheckStatus.FAIL

    def flip(cmd: str, **kw):  # 第一次 getprop 回 stopped，setprop 後回 running
        adb.props["init.svc.traced"] = "running"
        return adb.__class__.shell(adb, cmd, **kw)

    adb.props["init.svc.traced"] = "stopped"
    adb.shell = flip  # type: ignore[method-assign]
    assert check_traced_running(adb).status is CheckStatus.WARN


def test_frametimeline_and_trace_processor() -> None:
    assert check_frametimeline(DeviceInfo("s", sdk_int=31)).status is CheckStatus.PASS
    assert check_frametimeline(DeviceInfo("s", sdk_int=30)).status is CheckStatus.FAIL
    assert check_trace_processor().status is CheckStatus.PASS  # perfetto 套件已安裝


def test_thermal_check_variants() -> None:
    assert check_thermal(FakeAdb()).status is CheckStatus.WARN
    adb = FakeAdb(responses={r"dumpsys hardware_properties": (load("hardware_properties.txt"), 0)})
    result = check_thermal(adb)
    assert result.status is CheckStatus.WARN and "Thermal Status" in result.summary


def test_screen_state() -> None:
    assert check_screen_state(healthy_adb()).status is CheckStatus.PASS
    adb = FakeAdb(responses={r"dumpsys power": ("mWakefulness=Asleep", 0)})
    assert check_screen_state(adb).status is CheckStatus.FAIL


def test_blocking_reason_and_unknown_sdk() -> None:
    adb = healthy_adb()
    adb.props["ro.build.version.sdk"] = "30"
    adb.responses[r"timestats -clear -enable"] = ("", 1)
    adb.responses[r"perfetto --version"] = ("", 127)
    report = probe(adb)
    reason = report.blocking_reason()
    assert reason and "timestats" in reason and "--latency" in reason
    adb = healthy_adb()
    adb.props["ro.build.version.sdk"] = "38"
    assert probe(adb).get("sdk_known").status is CheckStatus.WARN
