"""演示脚本：按时间线逐步打印各阶段的签名密钥与旧密钥验签结果。"""

from keyring import KeyRing, NoActiveKeyError

T0 = 1_700_000_000
DATA = b"payload-to-be-signed"


def row(t, label, ring, clock, old_sig):
    clock.t = t
    try:
        sign_kid, sig = ring.sign(DATA)
        sign_kid = "->%s" % sign_kid
    except NoActiveKeyError:
        sign_kid = "无可用密钥"
    verify_old = ring.verify("k1", DATA, old_sig) if old_sig is not None else "-"
    off = t - T0
    off_s = "T0%+d" % off if off else "T0"
    print("%-18s t=%-7s  签名密钥=%-8s  k1旧签名验签=%s" % (label, off_s, sign_kid, verify_old))


def main():
    clock = type("C", (), {"t": 0})()
    ring = KeyRing(now_fn=lambda: clock.t)
    ring.add_key("k1", b"secret-old", not_before=T0, not_after=T0 + 100)
    ring.add_key("k2", b"secret-new", not_before=T0 + 80, not_after=T0 + 180)

    # k1 在 T0+50 签出一份内容，之后在不同时刻重复验它
    clock.t = T0 + 50
    old_kid, old_sig = ring.sign(DATA)
    assert old_kid == "k1"

    print("旧签名: kid=k1, 在 t=T0+50 签出, sig=%s..." % old_sig[:16])
    print("-" * 78)
    row(T0 - 1, "首次生效前", ring, clock, None)
    row(T0, "首次生效", ring, clock, old_sig)
    row(T0 + 50, "轮换前", ring, clock, old_sig)
    row(T0 + 80, "轮换刚结束", ring, clock, old_sig)
    row(T0 + 90, "轮换中(重叠期)", ring, clock, old_sig)
    row(T0 + 99, "k1过期前1秒", ring, clock, old_sig)
    row(T0 + 100, "k1刚好过期", ring, clock, old_sig)
    row(T0 + 101, "k1过期后", ring, clock, old_sig)
    print("-" * 78)
    clock.t = T0 + 99
    before = ring.verify("k1", DATA, old_sig)
    clock.t = T0 + 100
    after = ring.verify("k1", DATA, old_sig)
    print("过期对照: k1 在 T0+99 验签=%s, 在 T0+100 验签=%s (not_after 开区间)" % (before, after))


if __name__ == "__main__":
    main()
