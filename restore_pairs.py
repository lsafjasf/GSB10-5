"""还原对拍数据：每组给出原文、取值、译文、期望还原结果与实际结果对比。"""

from placeholder_lib import restore

# (编号, 原文, 位置取值, 命名取值, 译文, 期望还原结果)
PAIRS = [
    ("P1 顺序调整",
     "Hello %s, you have %d messages", ("Alice", 5), None,
     "您有 %d 条消息，%s", "您有 5 条消息，Alice"),
    ("P2 命名参数乱序",
     "%(user)s 在 %(city)s 消费 %(amount).2f 元", (), {"user": "张三", "city": "上海", "amount": 99.5},
     "%(user)s paid %(amount).2f in %(city)s", "张三 paid 99.50 in 上海"),
    ("P3 宽度与精度",
     "比例 %08.3f，名称 %-6s|", (3.14159, "ab"), None,
     "name=[%-6s] ratio=%08.3f", "name=[ab    ] ratio=0003.142"),
    ("P4 重复占位符",
     "%s 和 %s 都得了 %d 分", ("甲", "乙", 90), None,
     "%d 分：%s、%s", "90 分：甲、乙"),
    ("P5 字面量百分号",
     "进度 %d%% 完成", (90,), None,
     "%d%% done", "90% done"),
    ("P6 混合位置+命名",
     "%(name)s 得分 %d，比率 %.2f", (88, 0.756), {"name": "李四"},
     "ratio=%.2f score=%d player=%(name)s", "ratio=0.76 score=88 player=李四"),
    ("P7 标志位 + 与 0",
     "温度 %+d℃，误差 %+.1f", (-3, 0.25), None,
     "err=%+.1f temp=%+d", "err=+0.2 temp=-3"),
    ("P8 科学计数与十六进制",
     "值 %e 十六进制 %#x", (12345.678, 255), None,
     "hex=%#x val=%e", "hex=0xff val=1.234568e+04"),
]


def main():
    rows = []
    all_pass = True
    for name, src, values, named, tgt, expected in PAIRS:
        actual = restore(src, tgt, values, named)
        ok = actual == expected
        all_pass &= ok
        rows.append((name, src, values, named, tgt, expected, actual, ok))
    for name, src, values, named, tgt, expected, actual, ok in rows:
        print(f"[{name}]")
        print(f"  原文:   {src}")
        print(f"  取值:   values={values} named={named}")
        print(f"  译文:   {tgt}")
        print(f"  期望:   {expected}")
        print(f"  实际:   {actual}")
        print(f"  结果:   {'PASS' if ok else 'FAIL'}")
    print(f"\n总计 {len(rows)} 组，{'全部 PASS' if all_pass else '存在 FAIL'}")
    raise SystemExit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
