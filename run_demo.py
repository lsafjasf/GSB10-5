"""演示：占位符校验分类报告 + 还原对拍。"""
import json

from phcheck import restore, validate

DEMO_VALIDATE = [
    ("用户 %(user)s 有 %(count)d 条消息", "%(count)d messages for %(user)s", False),
    ("用户 %(user)s 有 %(count)d 条消息", "%(user)s messages", False),
    ("用户 %(user)s", "%(user)s 还有 %(count)d 条", False),
    ("%(user)s 提醒 %(user)s", "%(user)s", False),
    ("进度 %d", "进度 %z", False),
    ("%(count)05d 条", "%(count)5d", True),
]

DEMO_RESTORE = [
    ("用户 %(user)s 有 %(count)03d 条消息", "%(count)03d messages for %(user)s",
     {"user": "alice", "count": 7}),
    ("%1$s 余 %2$.2f 元（%3$02d 笔）", "%3$02d orders, %2$.2f yuan left for %1$s",
     ["bob", 3.5, 2]),
]


def main() -> None:
    print("=" * 70)
    print("校验演示（顺序无关的多重集比对）")
    print("=" * 70)
    for source, translation, strict in DEMO_VALIDATE:
        report = validate(source, translation, strict_format=strict)
        print("原文: {}".format(source))
        print("译文: {}  (strict_format={})".format(translation, strict))
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        print()

    print("=" * 70)
    print("还原演示（取值按身份映射，宽度/精度与原生一致）")
    print("=" * 70)
    for source, translation, values in DEMO_RESTORE:
        print("原文: {}".format(source))
        print("译文: {}".format(translation))
        print("取值: {}".format(values))
        print("输出: {}".format(restore(source, translation, values)))
        print()


if __name__ == "__main__":
    main()
