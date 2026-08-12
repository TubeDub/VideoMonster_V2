"""Speaker verification via cosine similarity (spec v3).

Uses SpeechBrain's ECAPA-TDNN embedding when installed; otherwise falls back
to a librosa MFCC-mean pseudo-embedding. Never raises — callers get a numeric
similarity in ``[-1, 1]`` and a ``method`` string.

Public API:
    ``embed_wav(path) -> np.ndarray | None``
    ``cosine_similarity(a, b) -> float``
    ``verify(reference_wav, candidate_wav, *, threshold=0.75) -> dict``
    ``retry_until_verified(synth_fn, reference_wav, *, threshold, max_attempts=3) -> dict``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.speaker_verification")

DEFAULT_COSINE_THRESHOLD = 0.75  # spec v3 recommendation for voice cloning

_embedder_singleton: Any = None
_embedder_method: str = "none"


def _get_ecapa():
    global _embedder_singleton, _embedder_method
    if _embedder_singleton is not None or _embedder_method == "unavailable":
        return _embedder_singleton
    try:
        from speechbrain.inference.speaker import EncoderClassifier  # type: ignore

        _embedder_singleton = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            run_opts={"device": "cpu"},
        )
        _embedder_method = "speechbrain_ecapa"
        return _embedder_singleton
    except Exception as exc:
        logger.info("[speaker_verify] speechbrain not available: %s", exc)
        _embedder_method = "unavailable"
        return None


def _fallback_embed_librosa(path: str) -> Any:
    try:
        import numpy as np  # type: ignore
        import librosa  # type: ignore

        y, sr = librosa.load(path, sr=16000, mono=True)
        if y is None or len(y) < 800:  # <50 ms — nothing to embed
            return None
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=40)
        vec = np.concatenate([mfcc.mean(axis=1), mfcc.std(axis=1)])
        n = float(np.linalg.norm(vec) + 1e-9)
        return (vec / n).astype("float32")
    except Exception as exc:
        logger.debug("[speaker_verify] librosa fallback failed: %s", exc)
        return None


def embed_wav(path: str) -> Any:
    """Return a 1-D unit vector embedding or None on failure."""
    if not Path(path).is_file():
        return None
    model = _get_ecapa()
    if model is not None:
        try:
            import numpy as np  # type: ignore
            import torchaudio  # type: ignore

            signal, fs = torchaudio.load(path)
            if fs != 16000:
                signal = torchaudio.functional.resample(signal, fs, 16000)
            emb = model.encode_batch(signal)
            vec = emb.squeeze().detach().cpu().numpy().astype("float32")
            n = float(np.linalg.norm(vec) + 1e-9)
            return vec / n
        except Exception as exc:
            logger.debug("[speaker_verify] ecapa embed failed for %s: %s", path, exc)
    return _fallback_embed_librosa(path)


def cosine_similarity(a: Any, b: Any) -> float:
    if a is None or b is None:
        return 0.0
    try:
        import numpy as np  # type: ignore

        va = np.asarray(a).flatten()
        vb = np.asarray(b).flatten()
        if va.shape != vb.shape:
            # dimensionality mismatch (e.g. ecapa vs mfcc fallback)
            n = min(va.shape[0], vb.shape[0])
            va, vb = va[:n], vb[:n]
        num = float((va * vb).sum())
        den = float((np.linalg.norm(va) * np.linalg.norm(vb)) + 1e-9)
        return max(-1.0, min(1.0, num / den))
    except Exception:
        return 0.0


def get_method() -> str:
    """Return the currently active embedding method label."""
    _get_ecapa()
    return _embedder_method


def verify(
    reference_wav: str,
    candidate_wav: str,
    *,
    threshold: float = DEFAULT_COSINE_THRESHOLD,
) -> dict[str, Any]:
    """Compute cosine similarity between two clips.

    Returns dict with ``similarity``, ``threshold``, ``ok``, ``method``.
    """
    ref = embed_wav(reference_wav)
    cand = embed_wav(candidate_wav)
    sim = cosine_similarity(ref, cand)
    return {
        "similarity": round(sim, 4),
        "threshold": float(threshold),
        "ok": bool(ref is not None and cand is not None and sim >= threshold),
        "method": get_method(),
        "reference": str(Path(reference_wav).resolve()) if Path(reference_wav).is_file() else reference_wav,
        "candidate": str(Path(candidate_wav).resolve()) if Path(candidate_wav).is_file() else candidate_wav,
    }


def retry_until_verified(
    synth_fn: Callable[[int], str | None],
    reference_wav: str,
    *,
    threshold: float = DEFAULT_COSINE_THRESHOLD,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Call ``synth_fn(attempt)`` up to ``max_attempts`` times until cosine >= threshold.

    ``synth_fn`` receives 1-based attempt number and returns the path to a
    freshly synthesized WAV (or None). Returns the best attempt's diagnostics
    plus ``attempts_used`` and ``all_similarities``.
    """
    best: dict[str, Any] = {
        "ok": False,
        "similarity": -1.0,
        "attempts_used": 0,
        "all_similarities": [],
        "candidate": None,
        "threshold": float(threshold),
        "method": get_method(),
    }
    ref_emb = embed_wav(reference_wav)
    if ref_emb is None:
        best["error"] = "reference_embed_failed"
        return best

    for i in range(1, max(1, int(max_attempts)) + 1):
        try:
            cand_path = synth_fn(i)
        except Exception as exc:
            logger.debug("[speaker_verify] synth_fn attempt %d raised: %s", i, exc)
            cand_path = None
        if not cand_path or not Path(cand_path).is_file():
            best["all_similarities"].append(None)
            continue
        cand_emb = embed_wav(cand_path)
        sim = cosine_similarity(ref_emb, cand_emb)
        best["all_similarities"].append(round(sim, 4))
        best["attempts_used"] = i
        if sim > best["similarity"]:
            best["similarity"] = round(sim, 4)
            best["candidate"] = str(Path(cand_path).resolve())
        if sim >= threshold:
            best["ok"] = True
            break
    return best
