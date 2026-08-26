"""Platform-independent contract tests for the pinned SITL harness."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.sitl import pinned
from scripts.sitl.pinned import (
    ArtifactIdentity,
    HarnessError,
    PortLease,
    StartupDefaultsIdentity,
    VerifiedArtifact,
    VerifiedStartupDefaults,
    build_command,
    prearm_health_from_bitmaps,
    verify_artifact,
    verify_startup_defaults,
)


def test_artifact_verification_accepts_only_exact_size_and_hash(tmp_path: Path) -> None:
    artifact = tmp_path / "arducopter"
    artifact.write_bytes(b"stock-test-artifact")
    identity = ArtifactIdentity(
        url="https://example.invalid/arducopter",
        sha256="eb1a1839d091c5cb7a1375bfa1ab1eb49e25c91340673e0c18a3177675468aa6",
        size_bytes=19,
        release_tag_commit="a" * 40,
        published_sitl_commit="b" * 40,
    )

    verified = verify_artifact(artifact, identity)

    assert verified.size_bytes == 19
    assert verified.sha256 == identity.sha256


@pytest.mark.parametrize(
    ("identity", "message"),
    [
        (
            ArtifactIdentity("url", "0" * 64, 18, "a" * 40, "b" * 40),
            "size mismatch",
        ),
        (
            ArtifactIdentity("url", "0" * 64, 19, "a" * 40, "b" * 40),
            "SHA-256 mismatch",
        ),
    ],
)
def test_artifact_verification_fails_closed(
    tmp_path: Path, identity: ArtifactIdentity, message: str
) -> None:
    artifact = tmp_path / "arducopter"
    artifact.write_bytes(b"stock-test-artifact")

    with pytest.raises(HarnessError, match=message):
        verify_artifact(artifact, identity)


def test_command_uses_explicit_isolated_ports_and_deterministic_defaults() -> None:
    artifact = VerifiedArtifact("/tmp/arducopter", "a" * 64, 7_023_152)
    startup_defaults = VerifiedStartupDefaults("/tmp/copter.parm", "b" * 64, 1_957, 1, 0)

    command = build_command(artifact, startup_defaults, 31_000)

    assert command == (
        "/tmp/arducopter",
        "--base-port",
        "31000",
        "--rc-in-port",
        "31010",
        "--sim-port-out",
        "31011",
        "--sim-port-in",
        "31012",
        "--irlock-port",
        "31013",
        "--model",
        "+",
        "--defaults",
        "/tmp/copter.parm",
        "--home",
        "51.5007292,-0.1246254,15,0",
        "--speedup",
        "1",
        "--sysid",
        "1",
        "--start-time",
        "1700000000",
        "--wipe",
    )


def test_startup_defaults_require_exact_frame_configuration(tmp_path: Path) -> None:
    defaults = tmp_path / "copter.parm"
    defaults.write_text("FRAME_CLASS 0\nFRAME_TYPE 0\n", encoding="utf-8")
    identity = StartupDefaultsIdentity(
        url="https://example.invalid/copter.parm",
        sha256=hashlib.sha256(defaults.read_bytes()).hexdigest(),
        size_bytes=defaults.stat().st_size,
        published_sitl_commit="b" * 40,
        git_blob_sha="c" * 40,
        frame_class=1,
        frame_type=0,
    )

    with pytest.raises(HarnessError, match="startup frame mismatch"):
        verify_startup_defaults(defaults, identity)


def test_startup_defaults_are_required(tmp_path: Path) -> None:
    with pytest.raises(HarnessError, match="startup defaults are missing"):
        verify_startup_defaults(tmp_path / "missing.parm")


@pytest.mark.parametrize(
    ("present", "enabled", "health", "ready"),
    [
        (0, 0, 0, False),
        (pinned.PREARM_CHECK_BIT, pinned.PREARM_CHECK_BIT, 0, False),
        (
            pinned.PREARM_CHECK_BIT,
            pinned.PREARM_CHECK_BIT,
            pinned.PREARM_CHECK_BIT,
            True,
        ),
    ],
)
def test_prearm_health_requires_present_enabled_and_healthy_bits(
    present: int, enabled: int, health: int, ready: bool
) -> None:
    assert prearm_health_from_bitmaps(present, enabled, health).ready is ready


def test_port_lease_prevents_shared_block_and_releases_it() -> None:
    first = PortLease.acquire()
    try:
        with pytest.raises(HarnessError, match="no isolated SITL port block"):
            PortLease.acquire(first.base_port)
        base_port = first.base_port
    finally:
        first.release()

    replacement = PortLease.acquire(base_port)
    replacement.release()


def test_launch_failure_preserves_hashed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "arducopter"
    artifact.write_bytes(b"unused")
    startup_defaults = tmp_path / "copter.parm"
    startup_defaults.write_text("unused", encoding="utf-8")
    output_dir = tmp_path / "evidence"

    def accept_test_artifact(path: Path) -> VerifiedArtifact:
        return VerifiedArtifact(str(path.resolve()), "a" * 64, path.stat().st_size)

    monkeypatch.setattr(pinned, "verify_artifact", accept_test_artifact)

    def accept_test_defaults(path: Path) -> VerifiedStartupDefaults:
        return VerifiedStartupDefaults(str(path.resolve()), "b" * 64, path.stat().st_size, 1, 0)

    monkeypatch.setattr(pinned, "verify_startup_defaults", accept_test_defaults)

    with pytest.raises(OSError):
        with pinned.pinned_sitl_session(artifact, startup_defaults, output_dir):
            pytest.fail("a failed launch must not yield a fixture")

    result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    hashes = (output_dir / "SHA256SUMS").read_text(encoding="utf-8")
    assert result["status"] == "failed"
    assert isinstance(result["error"], str)
    assert result["error"]
    assert result["process_pid"] is None
    assert result["ports_released"] is True
    assert "result.json" in hashes
    assert "sitl-stdout.log" in hashes
    assert "sitl-stderr.log" in hashes
    assert "protocol.jsonl" in hashes
