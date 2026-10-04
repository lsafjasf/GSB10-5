"""生成拆分对拍数据 testdata/<name>.{stdout,stderr,merged}。

stdout/stderr 是原始两个流的字节；merged 是按默认参数合并后的帧流。
split(merged) 必须逐字节还原 stdout/stderr。
"""

import pathlib

import stream_merge

HERE = pathlib.Path(__file__).parent
DATA = HERE / "testdata"


def make_case(name: str, out: bytes, err: bytes) -> None:
    merged = stream_merge.merge_bytes(out, err)
    (DATA / f"{name}.stdout").write_bytes(out)
    (DATA / f"{name}.stderr").write_bytes(err)
    (DATA / f"{name}.merged").write_bytes(merged)
    print(f"{name:20s} stdout={len(out):7d} stderr={len(err):7d} "
          f"merged={len(merged):7d}")


def main() -> None:
    DATA.mkdir(exist_ok=True)

    make_case("case1_only_stdout",
              b"stdout line 1\nstdout line 2\nstdout line 3\n",
              b"")

    make_case("case2_only_stderr",
              b"",
              b"warning one\nwarning two\n")

    alt_out = b"".join(f"stdout line {i}\n".encode() for i in range(1, 7))
    alt_err = b"".join(f"stderr line {i}\n".encode() for i in range(1, 7))
    make_case("case3_alternate", alt_out, alt_err)

    make_case("case4_no_trailing_nl",
              b"stdout complete line\nstdout tail without newline",
              b"stderr tail also without newline")

    make_case("case5_long_line",
              b"A" * (200 * 1024) + b"\n",
              b"small stderr\n")

    make_case("case6_both_empty", b"", b"")


if __name__ == "__main__":
    main()
