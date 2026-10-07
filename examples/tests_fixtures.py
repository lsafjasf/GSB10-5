"""Scenario fixtures shared by the report and tests."""
from risk_score.factors import ChangeMetrics, FileInfo


def _file(**kw):
    base = dict(path="src/a.py", churn_90d=1, test_ratio=0.8,
                ownership_frac=0.5)
    base.update(kw)
    return FileInfo(**base)


def build_scenarios():
    calm = ChangeMetrics(
        lines_added=5, lines_deleted=2, files=(_file(),),
        author_prior_changes=200, author_prior_rollbacks=1,
        reviewer_count=3, review_comment_count=12,
        is_emergency=False, is_off_hours=False, is_release_window=False)

    single_extreme = ChangeMetrics(
        **{**calm.__dict__,
           "is_emergency": True, "is_off_hours": True,
           "is_release_window": True})

    many_small = ChangeMetrics(
        lines_added=800, lines_deleted=300,
        files=(_file(churn_90d=12, test_ratio=0.25),),
        author_prior_changes=3, author_prior_rollbacks=1,
        reviewer_count=1, review_comment_count=1,
        is_emergency=False, is_off_hours=True, is_release_window=False)

    return [
        ("single factor extreme (schedule)", single_extreme),
        ("many small risks accumulating", many_small),
        ("all factors low", calm),
    ]
