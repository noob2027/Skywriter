"""Platform-independent contract tests for the pinned SITL harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.sitl import pinned
from scripts.sitl.pinned import (
    ArtifactIdentity,
    HarnessError,
    PortLease,
    VerifiedArtifact,
    build_command,
    verify_artifact,
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

    command = build_command(artifact, 31_000)

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
        "quad",
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
    output_dir = tmp_path / "evidence"

    def accept_test_artifact(path: Path) -> VerifiedArtifact:
        return VerifiedArtifact(str(path.resolve()), "a" * 64, path.stat().st_size)

    monkeypatch.setattr(pinned, "verify_artifact", accept_test_artifact)

    with pytest.raises(OSError):
        with pinned.pinned_sitl_session(artifact, output_dir):
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
