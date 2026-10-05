"""leakfp 自测：python3 -m unittest -v"""
import random
import unittest

from leakfp import LeakIndex, fingerprints, tokenize

ARTICLE = (
    "数据安全法明确规定，任何组织和个人收集数据，应当采取合法、正当的方式，"
    "不得窃取或者以其他非法方式获取数据。网络运营者应当对其收集的用户信息严格保密，"
    "并建立健全用户信息保护制度。发生或者可能发生个人信息泄露、毁损、丢失的情况时，"
    "应当立即采取补救措施，按照规定及时告知用户并向有关主管部门报告。"
    "违反上述规定的，由有关主管部门责令改正，可以根据情节单处或者并处警告、"
    "没收违法所得、处以罚款，情节严重的可以责令暂停相关业务、停业整顿。"
)


def _edit(text, rate, seed=7):
    """随机替换约 rate 比例的 token。"""
    rng = random.Random(seed)
    toks = tokenize(text)
    pool = "abcdefghijklmnopqrstuvwxyz"
    out = []
    for t in toks:
        if rng.random() < rate:
            out.append("".join(rng.choice(pool) for _ in range(4)))
        else:
            out.append(t)
    return " ".join(out)


class TestFingerprint(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(fingerprints(ARTICLE), fingerprints(ARTICLE))

    def test_punctuation_and_case_insensitive(self):
        messy = ARTICLE.replace("，", " , ").replace("。", "。\n\n")
        self.assertEqual(set(fingerprints(ARTICLE)), set(fingerprints(messy)))

    def test_winnowing_guarantee(self):
        """任意 >= k+w-1 token 的连续相同片段必产生共同指纹。"""
        k, w = 8, 6
        toks = tokenize(ARTICLE)
        frag = " ".join(toks[10 : 10 + k + w - 1])
        noise = "zzz qqq xxy " * 30
        mixed = noise + frag + noise
        self.assertTrue(
            set(fingerprints(ARTICLE, k, w)) & set(fingerprints(mixed, k, w))
        )


class TestLeakIndex(unittest.TestCase):
    def setUp(self):
        self.idx = LeakIndex()
        self.idx.add("law-1", ARTICLE)

    def test_exact_copy(self):
        hits = self.idx.query(ARTICLE)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["doc_id"], "law-1")
        self.assertAlmostEqual(hits[0]["query_containment"], 1.0)

    def test_light_edit(self):
        hits = self.idx.query(_edit(ARTICLE, 0.05))
        self.assertTrue(hits)
        self.assertEqual(hits[0]["doc_id"], "law-1")

    def test_reorder(self):
        paras = ARTICLE.split("。")
        paras = [p for p in paras if p]
        reordered = "。".join(paras[::-1]) + "。"
        hits = self.idx.query(reordered)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["doc_id"], "law-1")

    def test_split_and_splice(self):
        """拆成碎片，分别拼进无关文本，仍能命中。"""
        toks = tokenize(ARTICLE)
        frag_a = " ".join(toks[0:60])
        frag_b = " ".join(toks[80:140])
        noise = " ".join(f"n{i}" for i in range(300))
        suspect = noise[:400] + frag_a + noise[400:900] + frag_b + noise[900:]
        hits = self.idx.query(suspect)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["doc_id"], "law-1")

    def test_unrelated(self):
        other = (
            "今天的天气非常不错，适合出门运动和野餐。市场上苹果的价格有所上涨，"
            "不少消费者表示会改买橙子和香蕉。本地足球队昨晚赢得了关键比赛，"
            "主教练在赛后采访中表扬了年轻球员的拼搏精神，并透露下一场将轮换阵容。"
        )
        self.assertEqual(self.idx.query(other), [])

    def test_short_doc_rejected(self):
        self.assertEqual(self.idx.query("数据安全法明确规定"), [])

    def test_shared_boilerplate_not_flagged(self):
        """只共享一句套话（不足 min_hits 个指纹）不应误报。"""
        boiler = "转载请注明出处，违者必究。"
        other = boiler + " " + " ".join(f"u{i}" for i in range(200))
        idx = LeakIndex()
        idx.add("a", boiler + " " + " ".join(f"x{i}" for i in range(200)))
        self.assertEqual(idx.query(other), [])

    def test_readd_replaces(self):
        idx = LeakIndex()
        idx.add("d", ARTICLE)
        idx.add("d", " ".join(f"y{i}" for i in range(100)))
        self.assertEqual(idx.query(ARTICLE), [])


if __name__ == "__main__":
    unittest.main()


class TestDfFilter(unittest.TestCase):
    def test_common_boilerplate_ignored(self):
        rng = random.Random(1)
        boiler = " ".join(f"common{i}" for i in range(80))
        idx = LeakIndex(max_df=8)
        for i in range(10):
            unique = " ".join(f"uniq{i}_{j}" for j in range(150))
            idx.add(f"base-{i}", boiler + " " + unique)

        # 只带公共套话的新文档 -> 不报警
        negative = boiler + " " + " ".join(f"other{j}" for j in range(150))
        self.assertEqual(idx.query(negative), [])

        # 抄走某篇独有内容 -> 仍能定位
        stolen = " ".join(f"uniq3_{j}" for j in range(150))
        hits = idx.query(" ".join(f"pad{j}" for j in range(100)) + " " + stolen)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["doc_id"], "base-3")
