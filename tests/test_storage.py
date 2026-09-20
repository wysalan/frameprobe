from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from frameprobe.probe import DeviceInfo
from frameprobe.sampler import Sample
from frameprobe.storage import (
    SessionNotFoundError,
    SessionStore,
    SessionSummary,
    new_session_id,
    slugify,
)


def make_sample(elapsed: float, fps: float = 60.0) -> Sample:
    return Sample(
        ts=1_700_000_000 + elapsed,
        elapsed=elapsed,
        frames=60,
        fps=fps,
        p90_fps=58.0,
        p99_fps=40.0,
        dropped=1,
        dropped_ratio=0.016,
        histogram={16: 55, 33: 5},
        cpu_c=50.0,
        throttling_status=2,
        throttling_label="MODERATE",
    )


def test_slugify_and_session_id() -> None:
    assert (
        slugify("SurfaceView[com.example.game/x.Y]@0(BLAST)#1")
        == "SurfaceView_com.example.game_x.Y_0_BLAST_1"
    )
    sid = new_session_id("com.example.game", datetime(2026, 9, 13, 14, 5, 6))
    assert sid.startswith("20260913-140506-com.example.game-") and len(sid.split("-")[-1]) == 6


def test_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    sid = "20260913-000000-pkg-abc123"
    store.create(sid)
    store.write_raw(sid, "timestats_0001.txt", "layerName = x\n")
    store.append_sample(sid, make_sample(1.0))
    store.append_sample(sid, make_sample(2.0, fps=59.0))
    summary = SessionSummary(
        session_id=sid,
        device=DeviceInfo("S", "Google", "Pixel 10", "17", 37),
        package="pkg",
        layer_name="SurfaceView[pkg/x]#1",
        mode="realtime",
        started_at=datetime(2026, 9, 13, 0, 0, 0),
        duration_s=2.0,
        total_frames=120,
        average_fps=59.5,
        throttle_timeline=[(0.0, 0), (1.0, 2)],
        notes=["thermal source: sysfs"],
    )
    store.write_summary(sid, summary)

    assert (tmp_path / sid / "raw" / "timestats_0001.txt").read_text() == "layerName = x\n"
    loaded = store.load_session(sid)
    assert loaded.device.model == "Pixel 10"
    assert loaded.started_at == summary.started_at
    assert loaded.throttle_timeline == [(0.0, 0), (1.0, 2)]
    assert len(loaded.samples) == 2
    assert loaded.samples[0].histogram == {16: 55, 33: 5}  # JSON 字串鍵還原成 int
    assert loaded.samples[1].fps == 59.0
    assert loaded.samples[0].throttling_label == "MODERATE"

    listed = store.list_sessions()
    assert [s.session_id for s in listed] == [sid]
    assert listed[0].samples == []


def test_missing_and_invalid(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    with pytest.raises(SessionNotFoundError):
        store.load_summary("nope")
    with pytest.raises(SessionNotFoundError):
        store.path("../etc")
    assert store.list_sessions() == []
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "summary.json").write_text("{not json")
    assert store.list_sessions() == []


def test_export(tmp_path: Path) -> None:
    from frameprobe.storage import export_session

    summary = SessionSummary(
        session_id="s",
        device=DeviceInfo("S"),
        package="p",
        layer_name="l",
        mode="realtime",
        started_at=datetime(2026, 1, 1),
        samples=[make_sample(1.0), make_sample(2.0)],
    )
    csv_text = export_session(summary, "csv")
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("ts,elapsed,frames,fps") and len(lines) == 3
    assert "MODERATE" in lines[1]
    assert json.loads(export_session(summary, "json"))["samples"][1]["elapsed"] == 2.0
    with pytest.raises(ValueError):
        export_session(summary, "xml")


def test_summary_with_specs_roundtrip(tmp_path: Path) -> None:
    from frameprobe.devinfo import parse_devinfo
    from frameprobe.storage import summary_to_dict

    from .conftest import load

    specs = parse_devinfo(load("devinfo_samsung_s23.txt"))
    store = SessionStore(tmp_path)
    store.create("s")
    summary = SessionSummary(
        session_id="s",
        device=DeviceInfo("S", specs=specs),
        package="p",
        layer_name="l",
        mode="realtime",
        started_at=datetime(2026, 1, 1),
    )
    store.write_summary("s", summary)
    data = json.loads((tmp_path / "s" / "summary.json").read_text())
    assert (
        data["device"]["specs"]["gpu_summary"].startswith("Adreno")
        and "raw" not in data["device"]["specs"]
    )
    loaded = store.load_summary("s")
    assert loaded.device.specs is not None and loaded.device.specs.refresh_rate_hz == 60
    assert summary_to_dict(loaded)["device"]["specs"]["cpu_summary"].startswith("8 核")
