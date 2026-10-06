"""End-to-end demo: build records, reverse lookups, tamper detection."""
import json
import os
import subprocess
import tempfile

import provenance as pv

DEPENDENCIES = {
    "lockfile_sha256": "9f2c1a5e7b84d60f3a1c2e4d5b6a7f80912837465abcdef0123456789abcdef0",
    "resolved": [
        {"name": "requests", "version": "2.31.0",
         "digest": "1111111111111111111111111111111111111111111111111111111111111111"},
        {"name": "urllib3", "version": "2.2.1",
         "digest": "2222222222222222222222222222222222222222222222222222222222222222"},
    ],
}


def title(text):
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return "0" * 40


def main():
    tmp = tempfile.mkdtemp(prefix="prov-demo-")
    src = os.path.join(tmp, "src")
    os.makedirs(os.path.join(src, "pkg"))
    with open(os.path.join(src, "pkg", "main.py"), "w") as fh:
        fh.write("print('hello')\n")
    artifact = os.path.join(tmp, "app.tar.gz")
    with open(artifact, "wb") as fh:
        fh.write(b"DETERMINISTIC-ARTIFACT-BYTES")

    commit = git_commit()
    source = {
        "vcs": "git",
        "repo": "https://example.invalid/team/app.git",
        "commit": commit,
        "tree_sha256": pv.hash_tree(src),
        "dirty": False,
    }
    ledger_path = os.path.join(tmp, "ledger.jsonl")
    ledger = pv.Ledger(ledger_path)

    title("Scenario 1: single build -> full reverse lookup chain")
    r1 = pv.create_record(artifact_path=artifact, name="app.tar.gz",
                          source=source,
                          build={"command": "make", "params": {"OPT_LEVEL": "2"}},
                          dependencies=DEPENDENCIES)
    ledger.append(r1)
    print(f"artifact_sha256 = {r1['artifact']['sha256']}")
    idx = pv.Index.build(ledger_path)
    chain = idx.trace(r1["artifact"]["sha256"])[0]
    print("reverse lookup result:")
    print(json.dumps(chain, indent=2, ensure_ascii=False))

    title("Scenario 2: same source rebuilt twice (distinguishable, same origin)")
    r2 = pv.create_record(artifact_path=artifact, name="app.tar.gz",
                          source=source, dependencies=DEPENDENCIES)
    ledger.append(r2)
    idx = pv.Index.build(ledger_path)
    builds = idx.trace(r1["artifact"]["sha256"])
    print(f"build #1 build_id = {r1['build_id']}")
    print(f"build #1 finished_at = {r1['build']['finished_at']}  nonce={r1['build']['nonce']}")
    print(f"build #2 build_id = {r2['build_id']}")
    print(f"build #2 finished_at = {r2['build']['finished_at']}  nonce={r2['build']['nonce']}")
    same_origin = pv.canonical_json(r1["source"]) == pv.canonical_json(r2["source"])
    print(json.dumps({
        "same_artifact_sha256": r1["artifact"]["sha256"] == r2["artifact"]["sha256"],
        "different_build_ids": r1["build_id"] != r2["build_id"],
        "same_commit": r1["source"]["commit"] == r2["source"]["commit"],
        "same_source_tree_sha256": r1["source"]["tree_sha256"] == r2["source"]["tree_sha256"],
        "source_sections_identical": same_origin,
        "records_resolved_for_one_artifact_id": len(builds),
    }, indent=2))

    title("Scenario 3: same source, different build environment")
    tc310 = {"python": "3.10.13", "os": "Linux", "os_release": "5.15.0",
             "platform": "Linux-5.15.0-x86_64", "machine": "x86_64"}
    r3 = pv.create_record(artifact_path=artifact, name="app.tar.gz",
                          source=source, toolchain=tc310, dependencies=DEPENDENCIES)
    ledger.append(r3)
    print(json.dumps({
        "build_with_python312": {"build_id": r2["build_id"],
                                 "toolchain": r2["toolchain"]},
        "build_with_python310": {"build_id": r3["build_id"],
                                 "toolchain": r3["toolchain"]},
        "same_commit": r2["source"]["commit"] == r3["source"]["commit"],
        "same_source_tree_sha256": r2["source"]["tree_sha256"] == r3["source"]["tree_sha256"],
        "different_build_ids": r2["build_id"] != r3["build_id"],
    }, indent=2))

    title("Scenario 4: tampered record must be detected")
    tampered = json.loads(json.dumps(r1))
    tampered["source"]["commit"] = "deadbeef" * 5
    stored = r1["record_sha256"]
    recomputed = pv.record_digest(tampered)
    print(json.dumps({
        "tamper_action": "rewrite source.commit -> deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        "stored_record_sha256": stored,
        "recomputed_record_sha256": recomputed,
        "digests_match": stored == recomputed,
        "verify_record_problems": pv.verify_record(tampered),
    }, indent=2))

    # write the tampered record into the ledger and run whole-ledger verify
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(pv.canonical_json(tampered).decode("ascii") + "\n")
    ok, bad = pv.Ledger(ledger_path).verify_all()
    print("whole-ledger verification:")
    print(json.dumps({"ok_records": ok,
                      "tampered_records": [{"line": n, "problems": p}
                                           for n, p in bad]}, indent=2))

    # also demonstrate tampered artifact file detection
    art2 = os.path.join(tmp, "app2.tar.gz")
    with open(art2, "wb") as fh:
        fh.write(b"ORIGINAL")
    rec4 = pv.create_record(artifact_path=art2, name="app2.tar.gz",
                            source=source)
    with open(art2, "wb") as fh:
        fh.write(b"TAMPERED")
    print("artifact-file tamper check:")
    print(json.dumps({"problems": pv.verify_artifact_file(rec4, art2)}, indent=2))

    print(f"\nledger file: {ledger_path}")


if __name__ == "__main__":
    main()
