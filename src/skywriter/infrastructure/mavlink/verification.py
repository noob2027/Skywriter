"""Exact normalized readback verification and reproducible evidence digests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from skywriter.compatibility.arducopter_4_6_3 import (
    FieldMismatch,
    NativeMissionItem,
    NativeMissionPackage,
    NativeReadbackVerification,
    canonicalize_downloaded,
    canonicalize_expected,
    item_to_document,
    verification_to_document,
    verify_native_readback,
)


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """Stable hashes covering the package, raw readback, and exact comparison."""

    expected_digest: str
    downloaded_digest: str
    evidence_digest: str


@dataclass(frozen=True, slots=True)
class VerifiedUpload:
    """The sole successful transaction result; construction requires exact verification."""

    vehicle_identity: str
    item_count: int
    opaque_id: int
    used_legacy_requests: bool
    evidence: VerificationEvidence


@dataclass(frozen=True, slots=True)
class FailedVerification:
    """Field-level evidence retained when an accepted upload does not read back exactly."""

    acknowledged: bool
    opaque_id: int
    mismatches: tuple[FieldMismatch, ...]
    comparison: NativeReadbackVerification
    evidence: VerificationEvidence


class MissionVerificationError(RuntimeError):
    def __init__(self, failure: FailedVerification) -> None:
        super().__init__(
            "vehicle acknowledged the upload, but normalized mission readback did not match"
        )
        self.failure = failure


def verify_acknowledged_upload(
    package: NativeMissionPackage,
    downloaded: tuple[NativeMissionItem, ...],
    *,
    opaque_id: int,
    used_legacy_requests: bool,
) -> VerifiedUpload:
    """Return success only after the compatibility envelope verifies every field."""

    comparison = verify_native_readback(package, downloaded)
    evidence = _build_evidence(package, downloaded, comparison, opaque_id=opaque_id)
    if not comparison.verified:
        raise MissionVerificationError(
            FailedVerification(
                acknowledged=True,
                opaque_id=opaque_id,
                mismatches=comparison.home.mismatches + comparison.mission.mismatches,
                comparison=comparison,
                evidence=evidence,
            )
        )
    return VerifiedUpload(
        vehicle_identity=package.vehicle.value,
        item_count=len(package.items),
        opaque_id=opaque_id,
        used_legacy_requests=used_legacy_requests,
        evidence=evidence,
    )


def _build_evidence(
    package: NativeMissionPackage,
    downloaded: tuple[NativeMissionItem, ...],
    comparison: NativeReadbackVerification,
    *,
    opaque_id: int,
) -> VerificationEvidence:
    expected_document = [item_to_document(item) for item in canonicalize_expected(package)]
    downloaded_document = [item_to_document(item) for item in downloaded]
    canonical_downloaded = [item_to_document(item) for item in canonicalize_downloaded(downloaded)]
    comparison_document = verification_to_document(comparison)
    expected_digest = _digest(expected_document)
    downloaded_digest = _digest(downloaded_document)
    evidence_digest = _digest(
        {
            "schema": "skywriter-mavlink-verification-v1",
            "vehicle_identity": package.vehicle.value,
            "mission_type": package.items[0].mission_type,
            "opaque_id": opaque_id,
            "expected_canonical": expected_document,
            "downloaded_raw": downloaded_document,
            "downloaded_canonical": canonical_downloaded,
            "comparison": comparison_document,
        }
    )
    return VerificationEvidence(
        expected_digest=expected_digest,
        downloaded_digest=downloaded_digest,
        evidence_digest=evidence_digest,
    )


def _digest(document: object) -> str:
    encoded = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
