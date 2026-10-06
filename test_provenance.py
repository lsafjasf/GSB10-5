"""Self-tests for the provenance library (stdlib unittest)."""
import json
import os
import subprocess
import tempfile
import unittest

import provenance as pv

DEPS = {
    "lockfile_sha256": "a" * 64,
    "resolved": [
        {"name": "requests", "version": "2.31.0", "digest": "r" * 64},
        {"name": "urllib3", "version": "2.2.1", "digest": "u" * 64},
    ],
}


def write(path, content: bytes):
    with open(path, "wb") as fh:
        fh.write(content)


def source_info(src_dir, commit="abc1234" * 6, dirty=False):
    return {
        "vcs": "git",
        "repo": "https://example.invalid/team/app.git",
        "commit": commit,
        "tree_sha256": pv.hash_tree(src_dir),
        "dirty": dirty,
    }


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _artifact(self, name="app.tar.gz", content=b"PAYLOAD-v1"):
        path = os.path.join(self.dir, name)
        write(path, content)
        return path

    def _source_tree(self):
        src = os.path.join(self.dir, "src")
        os.makedirs(os.path.join(src, "pkg"))
        write(os.path.join(src, "pkg", "main.py"), b"print('hi')\n")
        return src

    # 1) single build: artifact id traces all the way to the commit
    def test_single_build_full_chain(self):
        src = self._source_tree()
        art = self._artifact()
        rec = pv.create_record(
            artifact_path=art, name="app.tar.gz",
            source=source_info(src),
            build={"command": "make", "params": {"OPT": "1"}},
            dependencies=DEPS)
        self.assertEqual([], pv.verify_record(rec))

        ledger = os.path.join(self.dir, "ledger.jsonl")
        pv.Ledger(ledger).append(rec)
        self.assertEqual((1, []), pv.Ledger(ledger).verify_all())

        idx = pv.Index.build(ledger)
        chain = idx.trace(rec["artifact"]["sha256"])
        self.assertEqual(1, len(chain))
        node = chain[0]
        self.assertTrue(node["integrity_ok"])
        self.assertEqual("abc1234" * 6, node["source_commit"])
        self.assertEqual(rec["build_id"], node["build_id"])
        self.assertIn("OPT", node["build"]["params"])
        self.assertEqual("2.31.0", node["dependencies"]["resolved"][0]["version"])
        self.assertIn("python", node["toolchain"])

    # 2) repeated builds of same source: distinct artifacts, same origin
    def test_repeated_builds_distinguishable_same_origin(self):
        src = self._source_tree()
        art = self._artifact(content=b"DETERMINISTIC-PAYLOAD")
        src_info = source_info(src)

        r1 = pv.create_record(artifact_path=art, name="app.tar.gz",
                              source=src_info, dependencies=DEPS)
        r2 = pv.create_record(artifact_path=art, name="app.tar.gz",
                              source=src_info, dependencies=DEPS)

        self.assertNotEqual(r1["build_id"], r2["build_id"],
                            "two builds must have distinct build ids")
        self.assertNotEqual(r1["build"]["finished_at"], r2["build"]["finished_at"])
        self.assertNotEqual(r1["build"]["nonce"], r2["build"]["nonce"])

        # proof of identical origin: source sections are byte-identical
        self.assertEqual(
            pv.canonical_json(r1["source"]), pv.canonical_json(r2["source"]))
        self.assertEqual(r1["artifact"]["sha256"], r2["artifact"]["sha256"])
        self.assertEqual(r1["source"]["commit"], r2["source"]["commit"])

        ledger = os.path.join(self.dir, "ledger.jsonl")
        lg = pv.Ledger(ledger)
        lg.append(r1)
        lg.append(r2)
        idx = pv.Index.build(ledger)
        found = idx.trace(r1["artifact"]["sha256"])
        self.assertEqual(2, len(found), "artifact id resolves to both builds")
        self.assertEqual({r1["build_id"], r2["build_id"]},
                         {n["build_id"] for n in found})

    # 3) different environment: same source, toolchain difference is captured
    def test_environment_difference_recorded(self):
        src = self._source_tree()
        art = self._artifact(content=b"DETERMINISTIC-PAYLOAD")
        src_info = source_info(src)

        tc_old = {"python": "3.10.13", "os": "Linux", "machine": "x86_64"}
        tc_new = {"python": "3.12.3", "os": "Linux", "machine": "x86_64"}
        r_old = pv.create_record(artifact_path=art, name="app.tar.gz",
                                 source=src_info, toolchain=tc_old)
        r_new = pv.create_record(artifact_path=art, name="app.tar.gz",
                                 source=src_info, toolchain=tc_new)

        self.assertEqual(r_old["source"]["commit"], r_new["source"]["commit"])
        self.assertNotEqual(r_old["build_id"], r_new["build_id"])
        self.assertNotEqual(r_old["toolchain"], r_new["toolchain"])
        # diff is reproducible / explainable: only toolchain+build fields differ
        self.assertEqual(pv.canonical_json(r_old["source"]),
                         pv.canonical_json(r_new["source"]))

        ledger = os.path.join(self.dir, "ledger.jsonl")
        lg = pv.Ledger(ledger)
        lg.append(r_old)
        lg.append(r_new)
        rows = pv.Index.build(ledger).lookup_commit(src_info["commit"])
        self.assertEqual({"3.10.13", "3.12.3"},
                         {r["toolchain"]["python"] for r in rows})

    # 4a) tampered record (commit rewritten) is detected
    def test_tampered_record_detected(self):
        src = self._source_tree()
        art = self._artifact()
        rec = pv.create_record(artifact_path=art, name="app.tar.gz",
                               source=source_info(src), dependencies=DEPS)

        tampered = json.loads(json.dumps(rec))
        tampered["source"]["commit"] = "f" * 40
        problems = pv.verify_record(tampered)
        self.assertTrue(any("record_sha256 mismatch" in p for p in problems))

        ledger = os.path.join(self.dir, "ledger.jsonl")
        pv.Ledger(ledger).append(rec)
        good, bad = pv.Ledger(ledger).verify_all()
        self.assertEqual(1, good)
        self.assertEqual([], bad)

        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(pv.canonical_json(tampered).decode("ascii") + "\n")
        good, bad = pv.Ledger(ledger).verify_all()
        self.assertEqual(1, good)
        self.assertEqual(1, len(bad))
        self.assertTrue(any("record_sha256 mismatch" in p for p in bad[0][1]))

        idx = pv.Index.build(ledger)
        chain = idx.trace(rec["artifact"]["sha256"])
        # tampered copy kept the original build_id, so both records surface
        # under the same artifact id; integrity check flags the bad one.
        self.assertEqual(2, len(chain))
        verdicts = sorted(n["integrity_ok"] for n in chain)
        self.assertEqual([False, True], verdicts)

    # 4b) tampered artifact file is detected
    def test_tampered_artifact_file_detected(self):
        src = self._source_tree()
        art = self._artifact(content=b"original bytes")
        rec = pv.create_record(artifact_path=art, name="app.tar.gz",
                               source=source_info(src))
        self.assertEqual([], pv.verify_artifact_file(rec, art))
        write(art, b"changed bytes!!")
        problems = pv.verify_artifact_file(rec, art)
        self.assertTrue(any("artifact digest mismatch" in p for p in problems))

    # 5) garbage line in ledger is reported, not silently accepted
    def test_corrupt_ledger_line_detected(self):
        ledger = os.path.join(self.dir, "ledger.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write("{not json\n")
        good, bad = pv.Ledger(ledger).verify_all()
        self.assertEqual(0, good)
        self.assertEqual(1, len(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
