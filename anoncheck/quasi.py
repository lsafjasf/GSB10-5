"""准标识符（Quasi-Identifier, QI）识别与字段组合枚举。

识别策略（两条证据，满足其一即视为 QI，均有理由可复核）：

1. 字段名匹配：对照已知的直接标识符词表、敏感属性词表与准标识符词表。
   - 直接标识符（姓名、身份证号、电话、邮箱等）逐字段即可定位个人，
     可逆性检查中必须先剔除，不作为 QI；
   - 敏感属性（疾病、薪资等）本身不用于重识别，不计入 QI；
   - 命中 QI 词表（年龄、性别、邮编、职业、婚姻、教育程度等）直接入选。

2. 统计特征兜底：字段名无法判断时，对采样数据计算基数 distinct/n：
   - distinct == 1：常量列，无识别价值，排除；
   - distinct/n >= near_unique_ratio（默认 0.9）：近乎唯一，按直接标识符处理，排除；
   - distinct < min_distinct（默认 2）：基数过低、对组合识别几乎无贡献，排除；
   - 其余视为“组合型 QI”——单看可能不起眼，但可与其他字段联合定位个人。

组合方式：对入选 QI 枚举 1..max_order 阶的全部组合，逐组计算等价类风险，
再按风险阈值筛选“被选中的高风险组合”（见 risk.evaluate_combination）。
"""

from itertools import combinations


DIRECT_IDENTIFIER_KEYWORDS = (
    "name", "id_card", "idcard", "identity", "ssn", "passport",
    "email", "mail", "phone", "mobile", "tel", "license",
    "street", "address", "bank", "account", "record_id", "user_id",
    "customer_id", "身份证", "姓名", "电话", "手机", "邮箱",
)

SENSITIVE_KEYWORDS = (
    "disease", "diagnosis", "diagnose", "medical", "salary", "income",
    "illness", "condition", "drug", "犯罪", "疾病", "诊断", "工资", "薪资",
)

QI_KEYWORDS = (
    "age", "birth", "gender", "sex", "zip", "postal", "postcode",
    "occupation", "job", "title", "industry", "education", "degree",
    "marital", "marriage", "race", "ethnic", "nationality", "citizen",
    "city", "region", "province", "state", "district", "county",
    "employer", "workclass", "religion", "language",
    "年龄", "出生", "性别", "邮编", "邮政", "职业", "教育", "婚姻",
    "民族", "国籍", "城市", "地区", "省", "雇主",
)


def classify_column(name, distinct, n,
                    min_distinct=2, near_unique_ratio=0.9):
    """对单列给出分类与理由。

    返回 (类别, 理由)。类别取值：
    "direct"（直接标识符）/ "sensitive"（敏感属性）/
    "qi_named"（词表命中的 QI）/ "qi_statistical"（统计特征选出的组合型 QI）/
    "constant" / "near_unique" / "low_cardinality"（均排除）。
    """
    low = name.lower()

    for kw in DIRECT_IDENTIFIER_KEYWORDS:
        if kw in low:
            return "direct", f"字段名命中直接标识符词表 '{kw}'，应先剔除"
    for kw in SENSITIVE_KEYWORDS:
        if kw in low:
            return "sensitive", f"字段名命中敏感属性词表 '{kw}'，不参与重识别"
    for kw in QI_KEYWORDS:
        if kw in low:
            return "qi_named", f"字段名命中准标识符词表 '{kw}'"

    if n == 0:
        return "constant", "无数据"
    if distinct <= 1:
        return "constant", "常量列，无识别价值"
    if distinct / n >= near_unique_ratio:
        return "near_unique", (
            f"基数 {distinct}/{n} >= {near_unique_ratio:.0%}，近乎唯一，"
            "按直接标识符处理")
    if distinct < min_distinct:
        return "low_cardinality", f"基数 {distinct} 过低，对组合识别无贡献"
    return "qi_statistical", (
        f"基数 {distinct}/{n} 处于可组合区间，"
        "单字段不唯一但可能与其他字段联合定位个人")


def identify_quasi_identifiers(headers, rows,
                               min_distinct=2, near_unique_ratio=0.9):
    """识别 QI。

    参数:
        headers: 列名列表
        rows: 行序列，每行是与 headers 等长的序列
    返回:
        list[dict]，每项包含
        column(列名)、index(列下标)、category、reason、distinct、ratio。
        仅包含被选为 QI 的列，按原列序排列，理由可逐条复核。
    """
    n = 0
    seen = [set() for _ in headers]
    for row in rows:
        n += 1
        for i, value in enumerate(row):
            seen[i].add(value)

    result = []
    for i, name in enumerate(headers):
        distinct = len(seen[i])
        category, reason = classify_column(
            name, distinct, n, min_distinct, near_unique_ratio)
        if category in ("qi_named", "qi_statistical"):
            result.append({
                "column": name,
                "index": i,
                "category": category,
                "reason": reason,
                "distinct": distinct,
                "ratio": (distinct / n) if n else 0.0,
            })
    return result


def enumerate_combinations(qi_columns, max_order=3):
    """枚举 1..max_order 阶的全部 QI 组合。

    qi_columns: identify_quasi_identifiers 的返回值。
    返回 [(列名元组, 列下标元组), ...]，按阶数与列序排序，保证结果可复现。
    """
    combos = []
    for order in range(1, min(max_order, len(qi_columns)) + 1):
        for chosen in combinations(qi_columns, order):
            names = tuple(c["column"] for c in chosen)
            indices = tuple(c["index"] for c in chosen)
            combos.append((names, indices))
    return combos
