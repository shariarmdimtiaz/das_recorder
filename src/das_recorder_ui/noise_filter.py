from __future__ import annotations

import numpy as np


def _robust_noise_sigma(data: np.ndarray) -> float:
    """Estimate block noise scale with a robust median absolute estimator.

    This is intentionally lightweight for live acquisition. It estimates the
    low-amplitude background level inside the current block. It is not a full
    denoising algorithm such as FK filtering or wavelet denoising.
    """
    if data.size == 0:
        return 0.0

    arr = data.astype(np.float32, copy=False)
    abs_arr = np.abs(arr)
    median_abs = float(np.median(abs_arr))

    if not np.isfinite(median_abs) or median_abs <= 0.0:
        return 0.0

    # For zero-mean Gaussian noise, median(abs(x)) / 0.6745 estimates sigma.
    return median_abs / 0.6745


def apply_noise_reduction(
    data: np.ndarray,
    replace_by_zeros: bool = False,
    suppression_factor: float = 0.0,
) -> np.ndarray:
    """Apply simple real-time noise suppression to a DAS data block.

    Parameters
    ----------
    data:
        DAS block with shape (channels, samples). Usually int16.
    replace_by_zeros:
        If True, low-amplitude samples below the estimated noise threshold are
        replaced by 0.
        If False, low-amplitude samples are attenuated smoothly instead of being
        hard-zeroed.
    suppression_factor:
        0..100. 0 means no suppression. 100 means strongest suppression.

    Returns
    -------
    numpy.ndarray
        Same shape as input. The output dtype is preserved for integer inputs.
    """
    try:
        strength = float(suppression_factor) / 100.0
    except Exception:
        strength = 0.0

    strength = max(0.0, min(1.0, strength))
    if strength <= 0.0 or data.size == 0:
        return data

    sigma = _robust_noise_sigma(data)
    if sigma <= 0.0:
        return data

    # Low setting gives a small threshold; high setting gives stronger gating.
    # Typical threshold range: ~0.5 sigma to ~6 sigma.
    threshold = sigma * (0.5 + 5.5 * strength)

    arr = data.astype(np.float32, copy=False)
    abs_arr = np.abs(arr)
    noise_mask = abs_arr < threshold

    if replace_by_zeros:
        out = arr.copy()
        out[noise_mask] = 0.0
    else:
        # Soft attenuation below threshold. Strong samples remain unchanged.
        # Near-zero samples are strongly attenuated; samples near threshold are
        # only slightly changed. This avoids a blocky hard-gate appearance.
        ratio = np.ones_like(arr, dtype=np.float32)
        ratio[noise_mask] = abs_arr[noise_mask] / max(threshold, 1e-12)
        scale = 1.0 - strength * (1.0 - ratio)
        out = arr * scale

    if np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        out = np.clip(np.rint(out), info.min, info.max).astype(data.dtype, copy=False)
    else:
        out = out.astype(data.dtype, copy=False)

    return out
