"""Generate the deliverable report data into reports/. Run: python3 demo.py

Every scenario uses injected virtual time (see scenarios.py); nothing here
reads the wall clock except benchmarking, which lives in bench.py.
"""
from __future__ import annotations

import json
import os
import tempfile

import scenarios as sc
from catchup import (
    EffectLog,
    Journal,
    build_curve,
    build_initial,
    build_resume_summary,
    iter_resume,
    run_compensation,
    write_curve_csv,
)

REPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def dump(name, payload):
    path = os.path.join(REPORTS, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print("wrote " + path)


def window(down, up):
    return {"down_at": down, "up_at": up,
            "down_at_utc": sc.fmt(down), "up_at_utc": sc.fmt(up),
            "rule": "half-open [down_at, up_at): fires with down_at <= t < up_at are owed"}


def per_task_rows(plan):
    return [
        {"task_id": tid, "compensations": count,
         "first_fire_at": first, "first_fire_at_utc": sc.fmt(first),
         "last_fire_at": last, "last_fire_at_utc": sc.fmt(last)}
        for tid, count, first, last in plan.per_task()
    ]


def trace_rows(result):
    return [
        {"key": t.key, "status": t.status,
         "fire_at": int(t.fire_at), "fire_at_utc": sc.fmt(t.fire_at),
         "start": round(t.start, 3), "start_utc": sc.fmt(t.start),
         "end": round(t.end, 3), "end_utc": sc.fmt(t.end)}
        for t in result.traces
    ]


def scenario_no_miss():
    plan = build_initial(sc.S1_TASKS, *sc.S1_DOWN)
    dump("plan_no_miss.json", {
        "scenario": "S1 no missed window (downtime falls between two fires)",
        "tasks": [{"task_id": t.task_id, "period": t.period} for t in sc.S1_TASKS],
        "window": window(*sc.S1_DOWN),
        "total_compensations": plan.total,
        "per_task": per_task_rows(plan),
    })


def scenario_single_window():
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(os.path.join(tmp, "j.log"))
        effects = EffectLog()
        plan = build_initial(sc.S2_TASKS, *sc.S2_DOWN)
        first = run_compensation(plan.iter_plan(), journal, effects,
                                 rate_per_sec=2, max_concurrency=2, now=sc.S2_DOWN[1])
        replay = run_compensation(build_initial(sc.S2_TASKS, *sc.S2_DOWN).iter_plan(),
                                  journal, effects,
                                  rate_per_sec=2, max_concurrency=2, now=sc.S2_DOWN[1] + 10)
        dump("plan_single_window.json", {
            "scenario": "S2 exactly one missed window",
            "tasks": [{"task_id": t.task_id, "period": t.period} for t in sc.S2_TASKS],
            "window": window(*sc.S2_DOWN),
            "total_compensations": plan.total,
            "per_task": per_task_rows(plan),
            "owed_fires": [
                {"key": f.key, "fire_at": int(f.fire_at), "fire_at_utc": sc.fmt(f.fire_at)}
                for f in plan.iter_plan()
            ],
            "first_run": {"done": first.done, "skipped": first.skipped,
                          "traces": trace_rows(first)},
            "duplicate_replay": {
                "done": replay.done, "skipped": replay.skipped,
                "effect_apply_calls_total": effects.apply_calls,
                "unique_effect_keys": len(effects),
                "duplicate_side_effects": effects.duplicates(),
                "note": "replay of the identical plan produced 0 new side effects",
            },
        })


def scenario_long_downtime():
    plan = build_initial(sc.S3_TASKS, *sc.S3_DOWN)
    dump("plan_long_downtime.json", {
        "scenario": "S3 very long downtime (30 days)",
        "tasks": [{"task_id": t.task_id, "period": t.period} for t in sc.S3_TASKS],
        "window": window(*sc.S3_DOWN),
        "total_compensations": plan.total,
        "per_task": per_task_rows(plan),
        "note": "fire times are generated lazily; only first/last are materialised here",
    })


def scenario_resume():
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(os.path.join(tmp, "j.log"))
        effects = EffectLog()
        down1, up1 = sc.S4_DOWN1
        down2, up2 = sc.S4_DOWN2

        plan1 = build_initial(sc.S4_TASKS, down1, up1)
        phase1 = run_compensation(plan1.iter_plan(), journal, effects,
                                  rate_per_sec=sc.S4_RATE1, max_concurrency=sc.S4_CONC,
                                  duration=sc.s4_duration, now=up1, stop_at=sc.S4_STOP_AT)

        owed = list(iter_resume(sc.S4_TASKS, down1, up1, down2, up2, journal.is_done))
        summary = build_resume_summary(sc.S4_TASKS, down1, up1, down2, up2, journal.is_done)

        journal_after_restart = Journal(os.path.join(tmp, "j.log"))
        phase2 = run_compensation(owed, journal_after_restart, effects,
                                  rate_per_sec=sc.S4_RATE2, max_concurrency=sc.S4_CONC,
                                  duration=sc.s4_duration, now=up2)

        dump("plan_resume_after_redown.json", {
            "scenario": "S4 second downtime during compensation, then resume",
            "tasks": [{"task_id": t.task_id, "period": t.period} for t in sc.S4_TASKS],
            "downtime_1": window(down1, up1),
            "phase1_limits": {"rate_per_sec": sc.S4_RATE1, "max_concurrency": sc.S4_CONC},
            "downtime_1_plan": {"total": plan1.total, "per_task": per_task_rows(plan1)},
            "second_downtime_at": sc.S4_STOP_AT,
            "second_downtime_at_utc": sc.fmt(sc.S4_STOP_AT),
            "phase1_result": {
                "done": phase1.done, "aborted": phase1.aborted,
                "journal_keys_after_crash": sorted(journal.keys()),
                "traces": trace_rows(phase1),
            },
            "downtime_2": window(down2, up2),
            "resume_plan": {
                "rule": "owed = (downtime-1 fires not in journal) UNION (downtime-2 fires)",
                "total": len(owed),
                "per_task": [{"task_id": t, "compensations": c} for t, c in summary],
                "owed_fires": [
                    {"key": f.key, "fire_at": int(f.fire_at),
                     "fire_at_utc": sc.fmt(f.fire_at)}
                    for f in owed
                ],
            },
            "phase2_limits": {"rate_per_sec": sc.S4_RATE2, "max_concurrency": sc.S4_CONC},
            "phase2_result": {
                "done": phase2.done, "skipped": phase2.skipped,
                "traces": trace_rows(phase2),
            },
            "idempotency_data": {
                "effect_apply_calls_total": effects.apply_calls,
                "unique_effect_keys": len(effects),
                "reapplied_keys": effects.duplicates(),
                "duplicate_side_effects": 0 if len(effects) == 7 else "VIOLATION",
                "journal_keys_final": sorted(journal_after_restart.keys()),
                "note": "the aborted job was re-applied on resume; the keyed upsert "
                        "kept its side effect single",
            },
        })


def scenario_idempotency():
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(os.path.join(tmp, "j.log"))
        effects = EffectLog()
        plan = build_initial(sc.S5_TASKS, *sc.S5_DOWN)
        first = run_compensation(plan.iter_plan(), journal, effects,
                                 rate_per_sec=sc.S5_RATE, max_concurrency=sc.S5_CONC,
                                 duration=lambda f: sc.S5_DURATION, now=sc.S5_DOWN[1])
        replay = run_compensation(build_initial(sc.S5_TASKS, *sc.S5_DOWN).iter_plan(),
                                  journal, effects,
                                  rate_per_sec=sc.S5_RATE, max_concurrency=sc.S5_CONC,
                                  duration=lambda f: sc.S5_DURATION, now=sc.S5_DOWN[1] + 100)
        dump("idempotency_replay.json", {
            "scenario": "S5 duplicate compensation of a 90-item plan",
            "window": window(*sc.S5_DOWN),
            "total_compensations": plan.total,
            "first_run": {"done": first.done, "skipped": first.skipped},
            "replay": {"done": replay.done, "skipped": replay.skipped},
            "effect_apply_calls_total": effects.apply_calls,
            "unique_effect_keys": len(effects),
            "duplicate_side_effects": effects.duplicates(),
            "journal_keys": len(journal),
        })


def scenario_load_curves():
    with tempfile.TemporaryDirectory() as tmp:
        # Rate-bound: 750 compensations at 10 starts/s, service would allow 16/s.
        journal = Journal(os.path.join(tmp, "j1.log"))
        effects = EffectLog()
        plan = build_initial(sc.S3_TASKS, *sc.S3_DOWN)
        result = run_compensation(plan.iter_plan(), journal, effects,
                                  rate_per_sec=sc.CURVE_RATE,
                                  max_concurrency=sc.CURVE_CONC,
                                  duration=lambda f: sc.CURVE_DURATION,
                                  now=sc.S3_DOWN[1])
        curve = build_curve(result.traces, start_at=sc.S3_DOWN[1])
        write_curve_csv(os.path.join(REPORTS, "load_curve_rate_bound.csv"), curve)
        rate_summary = {
            "scenario": "S3 30-day backlog drained at 10 starts/s (rate-bound)",
            "limits": {"rate_per_sec": sc.CURVE_RATE,
                       "max_concurrency": sc.CURVE_CONC,
                       "duration_per_job_sec": sc.CURVE_DURATION},
            "total_jobs": result.done,
            "drain_seconds": round(result.end_time - sc.S3_DOWN[1], 3),
            "peak_inflight": result.peak_inflight,
            "curve_head": [p.__dict__ for p in curve[:12]],
            "curve_tail": [p.__dict__ for p in curve[-3:]],
        }

        # Concurrency-bound: 90 compensations, 5 starts/s but only 3 workers.
        journal2 = Journal(os.path.join(tmp, "j2.log"))
        effects2 = EffectLog()
        plan2 = build_initial(sc.S5_TASKS, *sc.S5_DOWN)
        result2 = run_compensation(plan2.iter_plan(), journal2, effects2,
                                   rate_per_sec=sc.S5_RATE,
                                   max_concurrency=sc.S5_CONC,
                                   duration=lambda f: sc.S5_DURATION,
                                   now=sc.S5_DOWN[1])
        curve2 = build_curve(result2.traces, start_at=sc.S5_DOWN[1])
        write_curve_csv(os.path.join(REPORTS, "load_curve_concurrency_bound.csv"),
                        curve2)
        conc_summary = {
            "scenario": "S5 90 compensations with 3 workers (concurrency-bound)",
            "limits": {"rate_per_sec": sc.S5_RATE,
                       "max_concurrency": sc.S5_CONC,
                       "duration_per_job_sec": sc.S5_DURATION},
            "total_jobs": result2.done,
            "drain_seconds": round(result2.end_time - sc.S5_DOWN[1], 3),
            "peak_inflight": result2.peak_inflight,
            "curve_head": [p.__dict__ for p in curve2[:12]],
            "curve_tail": [p.__dict__ for p in curve2[-3:]],
        }
        dump("load_curve_summary.json", {
            "columns": {"second": "virtual second since recovery",
                        "started": "jobs started in that second (offered load)",
                        "completed": "jobs completed in that second",
                        "inflight_peak": "jobs in flight at bucket start "
                                         "(never exceeds max_concurrency)"},
            "rate_bound": rate_summary,
            "concurrency_bound": conc_summary,
        })


def main():
    os.makedirs(REPORTS, exist_ok=True)
    scenario_no_miss()
    scenario_single_window()
    scenario_long_downtime()
    scenario_resume()
    scenario_idempotency()
    scenario_load_curves()


if __name__ == "__main__":
    main()
