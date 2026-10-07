"""Self-tests for the catch-up compensation library. Run: python3 selftest.py

Covers: no missed window, exactly one missed window, very long downtime,
second downtime during compensation (resume), idempotent replay, rate and
concurrency bounds, and plan laziness (memory shape for large fleets).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone

import scenarios as sc
from catchup import (
    EffectLog,
    Journal,
    Task,
    build_curve,
    build_initial,
    build_resume_summary,
    iter_resume,
    missed_count,
    missed_fires,
    run_compensation,
)


def key(task_id: str, offset: int) -> str:
    return f"{task_id}@{sc.BASE + offset}"


class TestWindowMath(unittest.TestCase):
    def test_base_epoch(self):
        self.assertEqual(
            datetime.fromtimestamp(sc.BASE, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "2026-03-08T00:00:00Z",
        )

    def test_half_open_window(self):
        task = Task("t", period=300, anchor=sc.BASE)
        # fire exactly at down_at is missed; fire exactly at up_at is NOT.
        self.assertEqual(missed_count(task, sc.BASE + 300, sc.BASE + 600), 1)
        fires = [f.fire_at for f in missed_fires(task, sc.BASE + 300, sc.BASE + 600)]
        self.assertEqual(fires, [sc.BASE + 300])

    def test_count_matches_enumeration(self):
        task = Task("t", period=3600, anchor=sc.BASE + 17)
        down, up = sc.BASE + 5, sc.BASE + 30 * sc.DAY
        self.assertEqual(missed_count(task, down, up),
                         len(list(missed_fires(task, down, up))))

    def test_anchor_offset(self):
        task = Task("t", period=100, anchor=sc.BASE + 30)
        fires = [f.fire_at - sc.BASE for f in missed_fires(task, sc.BASE, sc.BASE + 250)]
        self.assertEqual(fires, [30, 130, 230])


class TestNoMiss(unittest.TestCase):
    def test_no_missed_window(self):
        plan = build_initial(sc.S1_TASKS, *sc.S1_DOWN)
        self.assertEqual(plan.total, 0)
        self.assertEqual(plan.per_task(), [])
        self.assertEqual(list(plan.iter_plan()), [])


class TestSingleWindow(unittest.TestCase):
    def test_exactly_one_fire_missed(self):
        plan = build_initial(sc.S2_TASKS, *sc.S2_DOWN)
        self.assertEqual(plan.total, 1)
        self.assertEqual(plan.per_task(),
                         [("s2-sync", 1, sc.BASE + 300, sc.BASE + 300)])

    def test_run_and_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(os.path.join(tmp, "journal.log"))
            effects = EffectLog()
            plan = build_initial(sc.S2_TASKS, *sc.S2_DOWN)

            first = run_compensation(plan.iter_plan(), journal, effects,
                                     rate_per_sec=2, max_concurrency=2, now=sc.S2_DOWN[1])
            self.assertEqual((first.done, first.skipped), (1, 0))

            # Duplicate compensation: identical plan, same journal.
            replay = run_compensation(build_initial(sc.S2_TASKS, *sc.S2_DOWN).iter_plan(),
                                      journal, effects,
                                      rate_per_sec=2, max_concurrency=2, now=sc.S2_DOWN[1] + 10)
            self.assertEqual((replay.done, replay.skipped), (0, 1))
            self.assertEqual(len(effects), 1)          # side effect happened once
            self.assertEqual(effects.apply_calls, 1)   # and was never re-applied
            self.assertEqual(len(journal), 1)


class TestLongDowntime(unittest.TestCase):
    def test_thirty_days(self):
        plan = build_initial(sc.S3_TASKS, *sc.S3_DOWN)
        self.assertEqual(plan.total, 720 + 30)
        self.assertEqual(
            plan.per_task(),
            [("s3-hourly", 720, sc.BASE, sc.BASE + 719 * 3600),
             ("s3-daily", 30, sc.BASE, sc.BASE + 29 * sc.DAY)],
        )

    def test_plan_is_lazy(self):
        import types
        plan = build_initial(sc.S3_TASKS, *sc.S3_DOWN)
        self.assertIsInstance(plan.iter_plan(), types.GeneratorType)


class TestRateAndConcurrencyBounds(unittest.TestCase):
    def test_rate_cap_and_concurrency_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(os.path.join(tmp, "j.log"))
            effects = EffectLog()
            plan = build_initial(sc.S5_TASKS, *sc.S5_DOWN)
            self.assertEqual(plan.total, 90)
            result = run_compensation(
                plan.iter_plan(), journal, effects,
                rate_per_sec=sc.S5_RATE, max_concurrency=sc.S5_CONC,
                duration=lambda fire: sc.S5_DURATION, now=sc.S5_DOWN[1],
            )
            self.assertEqual(result.done, 90)
            self.assertLessEqual(result.peak_inflight, sc.S5_CONC)
            self.assertEqual(result.peak_inflight, sc.S5_CONC)  # bound is reached

            curve = build_curve(result.traces, start_at=sc.S5_DOWN[1])
            for point in curve:
                self.assertLessEqual(point.started, sc.S5_RATE)     # rate bound /s
                self.assertLessEqual(point.inflight_peak, sc.S5_CONC)
            self.assertEqual(sum(p.started for p in curve), 90)
            self.assertEqual(sum(p.completed for p in curve), 90)

    def test_token_bucket_invariant_rate_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(os.path.join(tmp, "j.log"))
            effects = EffectLog()
            plan = build_initial(sc.S3_TASKS, *sc.S3_DOWN)
            result = run_compensation(
                plan.iter_plan(), journal, effects,
                rate_per_sec=sc.CURVE_RATE, max_concurrency=sc.CURVE_CONC,
                duration=lambda fire: sc.CURVE_DURATION, now=sc.S3_DOWN[1],
            )
            starts = sorted(t.start - sc.S3_DOWN[1]
                            for t in result.traces if t.status == "done")
            # Invariant: any window of length 1s starts at most burst + rate
            # (bucket holds one second's worth), and the sustained average is
            # at most rate.
            lo = 0
            for hi, t in enumerate(starts):
                while starts[lo] < t - 1.0 + 1e-9:
                    lo += 1
                self.assertLessEqual(hi - lo + 1, sc.CURVE_RATE + sc.CURVE_RATE)
            drain = result.end_time - sc.S3_DOWN[1]
            self.assertLessEqual(len(starts) / drain, sc.CURVE_RATE * 1.02)
            self.assertLessEqual(result.peak_inflight, sc.CURVE_CONC)
            curve = build_curve(result.traces, start_at=sc.S3_DOWN[1])
            self.assertTrue(all(p.inflight_peak <= sc.CURVE_CONC for p in curve))
            self.assertEqual(max(p.inflight_peak for p in curve), sc.CURVE_CONC)


class TestSecondDowntime(unittest.TestCase):
    def test_resume_after_redown(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(os.path.join(tmp, "j.log"))
            effects = EffectLog()
            down1, up1 = sc.S4_DOWN1
            down2, up2 = sc.S4_DOWN2

            plan1 = build_initial(sc.S4_TASKS, down1, up1)
            self.assertEqual(plan1.total, 6)  # A: 900/1200/1500/1800, B: 1200/1800

            phase1 = run_compensation(
                plan1.iter_plan(), journal, effects,
                rate_per_sec=sc.S4_RATE1, max_concurrency=sc.S4_CONC,
                duration=sc.s4_duration, now=up1, stop_at=sc.S4_STOP_AT,
            )
            self.assertEqual(phase1.done, 4)
            self.assertEqual(phase1.aborted, 1)
            self.assertEqual(
                journal.keys(),
                {key("s4-a", 900), key("s4-a", 1200), key("s4-a", 1500), key("s4-a", 1800)},
            )
            # The aborted job's effect was applied but never journaled.
            self.assertIn(key("s4-b", 1200), effects)
            self.assertNotIn(key("s4-b", 1200), journal)

            owed = list(iter_resume(sc.S4_TASKS, down1, up1, down2, up2, journal.is_done))
            owed_keys = [f.key for f in owed]
            self.assertEqual(owed_keys, [key("s4-b", 1200), key("s4-b", 1800), key("s4-a", 2100)])
            self.assertEqual(len(owed_keys), len(set(owed_keys)))  # union, no duplicates

            # Journal survives the "restart".
            journal2 = Journal(os.path.join(tmp, "j.log"))
            phase2 = run_compensation(
                owed, journal2, effects,
                rate_per_sec=sc.S4_RATE2, max_concurrency=sc.S4_CONC,
                duration=sc.s4_duration, now=up2,
            )
            self.assertEqual(phase2.done, 3)
            self.assertEqual(len(journal2), 7)   # 6 from downtime 1 + 1 from downtime 2
            self.assertEqual(len(effects), 7)    # every owed fire effected exactly once
            self.assertEqual(effects.duplicates(), {key("s4-b", 1200): 2})  # aborted retry
            self.assertEqual(effects.apply_calls, 5 + 3)

    def test_resume_summary_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(os.path.join(tmp, "j.log"))
            for off in (900, 1200, 1500, 1800):
                journal.mark_done(key("s4-a", off))
            rows = build_resume_summary(sc.S4_TASKS, *sc.S4_DOWN1, *sc.S4_DOWN2,
                                        journal.is_done)
            self.assertEqual(rows, [("s4-a", 1), ("s4-b", 2)])


class TestIdempotentReplay(unittest.TestCase):
    def test_full_replay_produces_no_new_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(os.path.join(tmp, "j.log"))
            effects = EffectLog()
            plan = build_initial(sc.S5_TASKS, *sc.S5_DOWN)
            run_compensation(plan.iter_plan(), journal, effects,
                             rate_per_sec=sc.S5_RATE, max_concurrency=sc.S5_CONC,
                             duration=lambda fire: sc.S5_DURATION, now=sc.S5_DOWN[1])
            self.assertEqual(len(effects), 90)

            replay = run_compensation(
                build_initial(sc.S5_TASKS, *sc.S5_DOWN).iter_plan(), journal, effects,
                rate_per_sec=sc.S5_RATE, max_concurrency=sc.S5_CONC,
                duration=lambda fire: sc.S5_DURATION, now=sc.S5_DOWN[1] + 100,
            )
            self.assertEqual((replay.done, replay.skipped), (0, 90))
            self.assertEqual(len(effects), 90)
            self.assertEqual(effects.apply_calls, 90)
            self.assertEqual(effects.duplicates(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
