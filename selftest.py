"""selftest -- 改写样例命中自测。

构造一篇受保护文档和一篇受保护短文档，分别验证：
完全相同 / 少量改字 / 句子换序 / 拆散拼接 / 完全无关 / 无关短文本。
运行：python3 selftest.py
"""

import random

from leakfp import LeakDetector, normalize
from textgen import gen_doc

PROTECTED = (
    "本合同由甲方（华云数据科技有限公司）与乙方（恒信建筑工程集团）于"
    "2024年3月15日在杭州市滨江区签订。项目名称为滨江智慧园区二期建设工程，"
    "合同总金额为人民币叁仟肆佰万元整。工期自2024年4月1日起至2025年6月30日"
    "止。乙方须按照国家现行施工验收规范组织施工，确保工程质量达到合格标准。"
    "付款方式：合同签订后七个工作日内支付预付款百分之二十，主体封顶后支付"
    "百分之四十，竣工验收合格后支付百分之三十五，剩余百分之五作为质保金，"
    "质保期满后无息退还。任何一方违约，应向守约方支付合同总金额百分之五的"
    "违约金。"
)

PROTECTED_SHORT = "机密：新版本将于下月十五日凌晨两点灰度发布，代号蓝鲸，首批覆盖百分之五的在线用户。"

# 改字：金额、地点、违约金比例等约 12 处改动
EDITED = (
    "本合同由甲方（华云数据科技有限公司）与乙方（恒信建筑工程集团）于"
    "2024年3月15日在杭州市西湖区签订。项目名称为滨江智慧园区二期建设工程，"
    "合同总金额为人民币叁仟贰佰万元整。工期自2024年4月1日起至2025年7月30日"
    "止。乙方须按照国家现行施工验收规范组织施工，确保工程质量达到合格标准。"
    "付款方式：合同签订后十个工作日内支付预付款百分之二十，主体封顶后支付"
    "百分之四十，竣工验收合格后支付百分之三十五，剩余百分之五作为质保金，"
    "质保期满后无息退还。任何一方违约，应向守约方支付合同总金额百分之八的"
    "违约金。"
)

UNRELATED = (
    "昨天的发布会上，主办方展示了新款智能手表的续航能力和健康监测功能，"
    "并宣布将于下季度在海外市场同步开售，现场还演示了语音助手的新特性。"
)

SHORT_UNRELATED = "今天天气不错，我们一起去湖边散步吧。"


def make_reordered(text: str, seed: int = 7) -> str:
    sents = [s for s in text.split("。") if s]
    random.Random(seed).shuffle(sents)
    return "。".join(sents) + "。"


def make_spliced(rng: random.Random) -> str:
    frag1 = PROTECTED[60:115]    # 约 55 字片段
    frag2 = PROTECTED[190:245]   # 约 55 字片段
    pad = gen_doc(rng, 6)
    return pad + frag1 + "。" + gen_doc(rng, 6) + frag2 + "。" + pad


def main() -> None:
    rng = random.Random(42)
    det = LeakDetector()
    id_main = det.add(PROTECTED)          # 0
    id_short = det.add(PROTECTED_SHORT)   # 1
    for _ in range(20):                   # 干扰文档
        det.add(gen_doc(rng, 20))
    det.finalize()

    cases = [
        ("完全相同", PROTECTED, id_main),
        ("少量改字(约12处)", EDITED, id_main),
        ("句子换序", make_reordered(PROTECTED), id_main),
        ("拆散拼接(两段约55字)", make_spliced(rng), id_main),
        ("完全无关文本", UNRELATED, None),
        ("无关短文本(20字)", SHORT_UNRELATED, None),
        ("短文档完全相同", PROTECTED_SHORT, id_short),
        ("短文档改1字", PROTECTED_SHORT.replace("两点", "三点"), id_short),
    ]

    print(f"受保护长文档 {len(normalize(PROTECTED))} 字（归一化后），"
          f"短文档 {len(normalize(PROTECTED_SHORT))} 字")
    print(f"{'样例':<18}{'期望':<6}{'结果':<6}{'命中文档':<10}"
          f"{'重合度':<8}{'命中指纹数'}")
    ok = True
    for name, text, expect in cases:
        res = det.query(text)
        hit_id, score, matches = (res[0] if res else (None, 0.0, 0))
        got = hit_id if res else None
        passed = (got == expect) if expect is not None else (not res)
        ok = ok and passed
        print(f"{name:<20}{'命中' if expect is not None else '不命中':<6}"
              f"{'命中' if res else '不命中':<6}{str(hit_id):<10}"
              f"{score:<8.2f}{matches}")
    print("\n自测", "全部通过" if ok else "存在失败用例")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
