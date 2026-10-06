"""对外整合接口：主体判据 + 裁剪框 + 越界校验。"""

from dataclasses import dataclass

from .saliency import analyze_subject, CriterionConfig
from .cropper import crop, FitPolicy, validate_box


@dataclass(frozen=True)
class AutocropConfig:
    target_ratio: float = 1.0
    criterion: CriterionConfig = CriterionConfig()
    fit: FitPolicy = FitPolicy()
    reject_when_no_subject: bool = False   # 无主体时的退路：False=居中, True=拒绝


def autocrop(image, config=None):
    cfg = config or AutocropConfig()
    analysis = analyze_subject(image, cfg.criterion)

    if not analysis["has_subject"]:
        if cfg.reject_when_no_subject:
            return {"analysis": analysis, "crop": None,
                    "decision": "rejected_no_subject",
                    "validation": None}
        result = crop(image.width, image.height, cfg.target_ratio,
                      None, None, cfg.fit)
    else:
        result = crop(image.width, image.height, cfg.target_ratio,
                      analysis["subject_bbox"], analysis["subject_centroid"],
                      cfg.fit)

    if result.box is None:
        validation = None
        decision = "rejected_subject_too_large"
    else:
        validation = validate_box(result.box, image.width, image.height,
                                  cfg.target_ratio, cfg.fit.ratio_tolerance)
        decision = "cropped"
    return {"analysis": analysis, "crop": result,
            "decision": decision, "validation": validation}
