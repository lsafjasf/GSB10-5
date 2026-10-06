"""Thumbnail pipeline: size policy -> (optional sharpen) -> resample ->
(optional sharpen) -> encode. Order is explicit and switchable."""

from . import metrics
from .policy import fit_size
from .resize import resize
from .sharpen import unsharp_mask

ORDERS = ("sharpen_first", "sharpen_last", "none")


def make_thumbnail(img, max_w, max_h, order="sharpen_last", resample="lanczos",
                   sharpen_sigma=0.8, sharpen_amount=0.5,
                   pre_sharpen_sigma=1.0, pre_sharpen_amount=0.6,
                   min_dim=4, max_long=8192):
    """Return (thumbnail Img, info dict).

    order:
      sharpen_first -- unsharp-mask the original, then low-pass downscale
      sharpen_last  -- low-pass downscale, then unsharp-mask the thumbnail
      none          -- low-pass downscale only
    """
    if order not in ORDERS:
        raise ValueError("order must be one of %r" % (ORDERS,))
    ow, oh = fit_size(img.width, img.height, max_w, max_h,
                      min_dim=min_dim, max_long=max_long)
    work = img
    if order == "sharpen_first" and (ow, oh) != (img.width, img.height):
        work = unsharp_mask(work, sigma=pre_sharpen_sigma,
                            amount=pre_sharpen_amount)
    thumb = resize(work, ow, oh, method=resample)
    if order == "sharpen_last" and (ow, oh) != (img.width, img.height):
        thumb = unsharp_mask(thumb, sigma=sharpen_sigma,
                             amount=sharpen_amount)
    info = {"out_w": ow, "out_h": oh, "order": order, "resample": resample}
    return thumb, info


def reference_thumbnail(img, ow, oh):
    """Ground-truth thumbnail: Lanczos-3 low-pass downscale of the original
    with no sharpening. Fidelity metrics are computed against this, i.e.
    against the best information-preserving reduction of the original."""
    return resize(img, ow, oh, method="lanczos")


def score_against_reference(thumb, reference):
    return {
        "ssim": metrics.ssim(thumb, reference),
        "psnr": metrics.psnr(thumb, reference),
        "rmse": metrics.rmse(thumb, reference),
        "mae": metrics.mae(thumb, reference),
        "grad_energy": metrics.gradient_energy(thumb),
        "clip_frac": metrics.clip_fraction(thumb),
    }
