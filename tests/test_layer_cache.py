"""分层缓存分析库的自测：覆盖完全相同 / 仅顺序调整 / 中间一层变化 /
时间戳与所有者变化，以及“不得错误复用”的反向用例。
"""
from __future__ import annotations

import unittest

from imglayer_cache import (
    BuildContext,
    FileRecord,
    LayerCache,
    analyze,
    build,
    parse_script,
)
from imglayer_cache.hashing import node_digest, snapshot_digest


BASE_SCRIPT = """\
FROM base:1.0
WORKDIR /app
COPY app.py /app/app.py
RUN write /app/config.txt mode=prod
RUN mkdir /app/data && write /app/data/seed.txt v1
ENV APP_ENV production
RUN chmod 755 /app/app.py
"""


def make_context(**overrides) -> BuildContext:
    files = {
        "app.py": FileRecord(content=b"print('hello')", mode=0o644, uid=1000, gid=1000, mtime_ns=111),
        "README.md": FileRecord(content=b"# demo", mode=0o644, uid=1000, gid=1000, mtime_ns=222),
    }
    for key, record in overrides.items():
        files[key] = record
    return BuildContext(files)


def analyze_pair(script_a_text, ctx_a, script_b_text, ctx_b):
    cache = LayerCache()
    return analyze(
        parse_script(script_b_text),
        ctx_b,
        cache,
        script_a=parse_script(script_a_text),
        context_a=ctx_a,
    )


class TestIdenticalScripts(unittest.TestCase):
    def test_identical_scripts_fully_reused(self):
        report = analyze_pair(BASE_SCRIPT, make_context(), BASE_SCRIPT, make_context())
        self.assertEqual(report.reused_layers, report.total_layers)
        self.assertEqual(report.rebuilt_layers, 0)
        self.assertEqual(report.layer_reuse_ratio, 1.0)
        self.assertIsNone(report.first_rebuilt_index)
        self.assertTrue(report.final_snapshot_match)
        for verdict in report.verdicts:
            self.assertEqual(verdict.decision, "reused")
            self.assertTrue(verdict.digest_match)
            self.assertTrue(verdict.byte_for_byte_match)


class TestReorder(unittest.TestCase):
    def test_reordered_instructions_invalidate_from_first_change(self):
        reordered = """\
FROM base:1.0
WORKDIR /app
RUN write /app/config.txt mode=prod
COPY app.py /app/app.py
RUN mkdir /app/data && write /app/data/seed.txt v1
ENV APP_ENV production
RUN chmod 755 /app/app.py
"""
        report = analyze_pair(BASE_SCRIPT, make_context(), reordered, make_context())
        self.assertEqual(report.reused_layers, 1)
        self.assertEqual(report.first_rebuilt_index, 2)
        self.assertEqual(report.rebuilt_layers, report.total_layers - 1)
        self.assertTrue(report.final_snapshot_match)

    def test_trailing_swap_still_invalidates(self):
        swapped = """\
FROM base:1.0
WORKDIR /app
COPY app.py /app/app.py
RUN write /app/config.txt mode=prod
RUN mkdir /app/data && write /app/data/seed.txt v1
RUN chmod 755 /app/app.py
ENV APP_ENV production
"""
        report = analyze_pair(BASE_SCRIPT, make_context(), swapped, make_context())
        self.assertEqual(report.reused_layers, 4)
        self.assertEqual(report.first_rebuilt_index, 5)
        self.assertTrue(report.final_snapshot_match)


class TestMiddleLayerChange(unittest.TestCase):
    def test_middle_run_change_rebuilds_suffix(self):
        changed = BASE_SCRIPT.replace("mode=prod", "mode=debug")
        report = analyze_pair(BASE_SCRIPT, make_context(), changed, make_context())
        self.assertEqual(report.reused_layers, 2)
        self.assertEqual(report.first_rebuilt_index, 3)
        self.assertEqual(report.rebuilt_layers, 4)
        self.assertTrue(report.final_snapshot_match)

    def test_copy_source_content_change_rebuilds_suffix(self):
        ctx_b = make_context()
        ctx_b.files["app.py"] = FileRecord(content=b"print('v2')", mode=0o644)
        report = analyze_pair(BASE_SCRIPT, make_context(), BASE_SCRIPT, ctx_b)
        self.assertEqual(report.reused_layers, 1)
        self.assertEqual(report.first_rebuilt_index, 2)
        self.assertTrue(report.final_snapshot_match)


class TestTimestampAndOwner(unittest.TestCase):
    def test_context_mtime_and_owner_changes_do_not_invalidate(self):
        ctx_b = make_context()
        ctx_b.files["app.py"] = FileRecord(
            content=b"print('hello')", mode=0o644, uid=4242, gid=777, mtime_ns=999_999_999
        )
        report = analyze_pair(BASE_SCRIPT, make_context(), BASE_SCRIPT, ctx_b)
        self.assertEqual(report.reused_layers, report.total_layers)
        self.assertEqual(report.layer_reuse_ratio, 1.0)
        self.assertTrue(report.final_snapshot_match)

    def test_touch_and_chown_only_changes_do_not_invalidate(self):
        script_b = BASE_SCRIPT.replace(
            "RUN chmod 755 /app/app.py",
            "RUN chmod 755 /app/app.py && touch /app/config.txt && chown www-data /app/config.txt",
        )
        report = analyze_pair(BASE_SCRIPT, make_context(), script_b, make_context())
        self.assertEqual(report.reused_layers, report.total_layers - 1)
        self.assertEqual(report.first_rebuilt_index, 6)
        self.assertTrue(report.final_snapshot_match)

    def test_mode_change_must_not_be_reused(self):
        ctx_b = make_context()
        ctx_b.files["app.py"] = FileRecord(content=b"print('hello')", mode=0o755)
        report = analyze_pair(BASE_SCRIPT, make_context(), BASE_SCRIPT, ctx_b)
        self.assertLess(report.reused_layers, report.total_layers)
        self.assertEqual(report.first_rebuilt_index, 2)
        self.assertTrue(report.final_snapshot_match)

    def test_content_change_must_not_be_reused(self):
        ctx_b = make_context()
        ctx_b.files["app.py"] = FileRecord(content=b"print('tampered')", mode=0o644)
        report = analyze_pair(BASE_SCRIPT, make_context(), BASE_SCRIPT, ctx_b)
        self.assertEqual(report.first_rebuilt_index, 2)
        self.assertTrue(report.final_snapshot_match)


class TestNoFalseReuse(unittest.TestCase):
    def test_env_change_invalidates_dependent_run(self):
        script_b = BASE_SCRIPT.replace("ENV APP_ENV production", "ENV APP_ENV staging")
        report = analyze_pair(BASE_SCRIPT, make_context(), script_b, make_context())
        self.assertEqual(report.first_rebuilt_index, 5)
        self.assertEqual(report.reused_layers, 4)
        self.assertTrue(report.final_snapshot_match)

    def test_base_image_change_invalidates_everything(self):
        script_b = BASE_SCRIPT.replace("FROM base:1.0", "FROM base:2.0")
        report = analyze_pair(BASE_SCRIPT, make_context(), script_b, make_context())
        self.assertEqual(report.reused_layers, 0)
        self.assertEqual(report.first_rebuilt_index, 1)
        self.assertTrue(report.final_snapshot_match)

    def test_corrupted_cache_blob_is_detected(self):
        cache = LayerCache()
        script = parse_script(BASE_SCRIPT)
        ctx = make_context()
        warm = build(script, ctx)
        wd, env = "/", {}
        for layer in warm.layers:
            if layer.op.kind == "workdir":
                wd = layer.op.args[0]
            elif layer.op.kind == "env":
                env = dict(env)
                env[layer.op.args[0]] = layer.op.args[1]
            cache.populate(layer, wd, env)
        cache.debug_replace_blob(warm.layers[1].key, b'{"tampered": true}')
        with self.assertRaises(RuntimeError):
            analyze(script, ctx, cache)


class TestHashing(unittest.TestCase):
    def test_node_digest_ignores_metadata(self):
        a = FileRecord(content=b"x", mode=0o644, uid=1, gid=2, mtime_ns=3)
        b = FileRecord(content=b"x", mode=0o644, uid=9, gid=9, mtime_ns=9)
        self.assertEqual(node_digest(a), node_digest(b))

    def test_node_digest_tracks_semantics(self):
        base = FileRecord(content=b"x", mode=0o644)
        self.assertNotEqual(node_digest(base), node_digest(FileRecord(content=b"y", mode=0o644)))
        self.assertNotEqual(node_digest(base), node_digest(FileRecord(content=b"x", mode=0o755)))
        self.assertNotEqual(
            node_digest(base),
            node_digest(FileRecord(content=b"", mode=0o777, kind="symlink", target=b"x")),
        )

    def test_snapshot_digest_deterministic(self):
        files = {
            "/a": FileRecord(content=b"1"),
            "/b": FileRecord(content=b"2"),
        }
        reversed_view = dict(reversed(list(files.items())))
        self.assertEqual(snapshot_digest(files), snapshot_digest(reversed_view))


if __name__ == "__main__":
    unittest.main()
