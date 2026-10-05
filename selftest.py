"""Self-tests for the stitching library.  Run:  python3 selftest.py"""

from seamstitch.image import Image
from seamstitch.stitch import stitch
from seamstitch import synth


def test_perfect_reconstruction():
    scene = synth.gradient_scene(210, 60)  # == offset(90) + view_w(120)
    synth.add_noise(scene, 3, seed=1)
    left, right = synth.make_pair(scene, 120, 90)  # overlap 30
    res = stitch(left, right, 90)
    assert res.status == "seam"
    # stitched canvas must reconstruct the original scene almost exactly
    err = 0
    for y in range(scene.height):
        for x in range(scene.width):
            a = res.image.get(x, y)
            b = scene.get(x, y)
            err = max(err, max(abs(a[i] - b[i]) for i in range(3)))
    assert err <= 6, f"reconstruction error {err}"
    print(f"  perfect_reconstruction: max err {err} OK")


def test_seam_avoids_subject():
    scene = synth.gradient_scene(240, 80)
    # dark subject (high contrast disc) inside the overlap, off-centre
    synth.add_disc(scene, 110, 40, 14, (10, 10, 10))
    left, right = synth.make_pair(scene, 140, 70)  # overlap cols 70..139
    res = stitch(left, right, 70)
    assert res.status == "seam"
    assert res.crossings_pixels == 0, f"seam hit subject {res.crossings_pixels}px"
    print(f"  seam_avoids_subject: crossings={res.crossings_pixels} OK")


def test_forced_subject_crossing():
    scene = synth.gradient_scene(240, 80)
    # striped high-contrast bar spans the WHOLE overlap: any seam must cross
    synth.add_striped_bar(scene, 60, 160)
    left, right = synth.make_pair(scene, 160, 60)  # overlap cols 60..159
    res = stitch(left, right, 60)
    assert res.status == "seam"
    assert res.crossings_pixels > 0
    assert res.crossings_segments >= 1
    print(f"  forced_subject_crossing: crossings={res.crossings_pixels}px "
          f"segments={res.crossings_segments} OK")


def test_exposure_compensation():
    scene = synth.gradient_scene(220, 60)
    left, right = synth.make_pair(scene, 120, 80, gain_right=1.5)
    res = stitch(left, right, 80)
    assert res.status == "seam"
    assert res.lum_diff_after < res.lum_diff_before * 0.4, (
        res.lum_diff_before, res.lum_diff_after)
    print(f"  exposure_compensation: diff {res.lum_diff_before:.2f} -> "
          f"{res.lum_diff_after:.2f} OK")


def test_small_overlap_fallback_and_reject():
    scene = synth.gradient_scene(240, 60)
    left, right = synth.make_pair(scene, 120, 116)  # overlap = 4 < min_overlap
    res = stitch(left, right, 116, on_too_small="blend")
    assert res.status == "fallback_blend" and res.image is not None
    assert res.image.width == 116 + 120
    res2 = stitch(left, right, 116, on_too_small="reject")
    assert res2.status == "rejected" and res2.image is None
    print("  small_overlap_fallback_and_reject: OK")


def test_large_overlap():
    scene = synth.gradient_scene(300, 70)
    synth.add_disc(scene, 120, 35, 12, (20, 20, 20))
    left, right = synth.make_pair(scene, 260, 40)  # overlap 220 (73%)
    res = stitch(left, right, 40)
    assert res.status == "seam" and res.overlap == 220
    assert res.crossings_pixels == 0
    print(f"  large_overlap: overlap={res.overlap} crossings="
          f"{res.crossings_pixels} OK")


def test_ppm_roundtrip(tmp="/tmp/_seamstitch_roundtrip.ppm"):
    img = synth.gradient_scene(37, 23)
    img.save_ppm(tmp)
    back = Image.load_ppm(tmp)
    assert back.width == 37 and back.height == 23 and back.data == img.data
    print("  ppm_roundtrip: OK")


if __name__ == "__main__":
    print("selftest:")
    test_perfect_reconstruction()
    test_seam_avoids_subject()
    test_forced_subject_crossing()
    test_exposure_compensation()
    test_small_overlap_fallback_and_reject()
    test_large_overlap()
    test_ppm_roundtrip()
    print("all self-tests passed")
