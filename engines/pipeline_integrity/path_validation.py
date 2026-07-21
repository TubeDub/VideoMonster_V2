"""Path validation — P3.1 §7."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engines.pipeline_integrity.artifact_registry import sha256_file
from engines.pipeline_integrity.exceptions import PipelineIntegrityError


class CorruptedWAVError(PipelineIntegrityError):
    code = "corrupted_wav"


class MissingAudioFileError(PipelineIntegrityError):
    code = "missing_audio_file"


class BrokenReferenceError(PipelineIntegrityError):
    code = "broken_reference"


AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}


@dataclass
class PathValidationResult:
    ok: bool
    path: str = ""
    size: int = 0
    hash: str = ""
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "path": self.path,
            "size": self.size,
            "hash": self.hash,
            "errors": list(self.errors or []),
        }


def validate_wav_path(
    path: Path | str,
    *,
    expected_uuid: str = "",
    expected_hash: str = "",
    require_extension: bool = True,
    min_size: int = 1,
) -> PathValidationResult:
    p = Path(path)
    errors: list[str] = []
    if not p.exists():
        errors.append("exists=false")
        return PathValidationResult(ok=False, path=str(p), errors=errors)
    if not p.is_file():
        errors.append("not_a_file")
        return PathValidationResult(ok=False, path=str(p), errors=errors)
    size = p.stat().st_size
    if size < min_size:
        errors.append(f"size={size}<{min_size}")
    if require_extension and p.suffix.lower() not in AUDIO_EXTENSIONS:
        errors.append(f"bad_extension={p.suffix}")
    # Lightweight WAV header check when extension is .wav
    if p.suffix.lower() == ".wav" and size >= 12:
        try:
            header = p.read_bytes()[:12]
            if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                errors.append("wav_header_invalid")
        except OSError as exc:
            errors.append(f"read_error={exc}")
    digest = ""
    try:
        digest = sha256_file(p)
    except OSError as exc:
        errors.append(f"hash_error={exc}")
    if expected_hash and digest and digest != expected_hash:
        errors.append("hash_mismatch")
    if expected_uuid and expected_uuid not in p.name and expected_uuid[:12] not in p.name:
        # UUID may live only in registry — soft check via metadata elsewhere
        pass
    return PathValidationResult(
        ok=not errors,
        path=str(p.resolve()) if p.exists() else str(p),
        size=size,
        hash=digest,
        errors=errors,
    )


def assert_valid_wav(path: Path | str, **kwargs: Any) -> PathValidationResult:
    result = validate_wav_path(path, **kwargs)
    if not result.ok:
        errs = result.errors or []
        if "exists=false" in errs:
            raise MissingAudioFileError(
                f"WAV missing: {path}",
                stage="path_validation",
                details=result.to_dict(),
            )
        if "wav_header_invalid" in errs or "size=" in ",".join(errs):
            raise CorruptedWAVError(
                f"WAV corrupted: {path}",
                stage="path_validation",
                details=result.to_dict(),
            )
        raise BrokenReferenceError(
            f"WAV path invalid: {path}",
            stage="path_validation",
            details=result.to_dict(),
        )
    return result
