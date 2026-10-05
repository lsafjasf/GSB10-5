"""合成数据生成器：构造一张典型的人口统计表。

字段设计覆盖四类必须检查的情形：
- 唯一组合：记录量较大时，年龄×性别×邮编×职业的细粒度组合自然产生大小为 1 的等价类；
  另植入若干确定性“针尖”记录，保证小规模演示也可稳定复现唯一组合。
- 大等价类：性别列（2 个取值）和高频邮编产生数万条记录的大等价类。
- 稀有取值：邮编 99999、职业 astronaut 等低概率取值构成稀有组合。
- 敏感字段：disease（疾病）为敏感属性，不参与重识别；
  name/record_id 为直接标识符，检查时必须剔除。
"""

import random

HEADERS = ["record_id", "name", "age", "gender", "zip_code",
           "occupation", "disease"]

GENDERS = ["M", "F"]

ZIPS = (
    [f"100{str(i).zfill(3)}" for i in range(40)]
    + [f"200{str(i).zfill(3)}" for i in range(40)]
    + [f"300{str(i).zfill(3)}" for i in range(40)]
    + [f"400{str(i).zfill(3)}" for i in range(30)]
    + [f"500{str(i).zfill(3)}" for i in range(30)]
    + [f"600{str(i).zfill(3)}" for i in range(20)]
)
ZIP_WEIGHTS = None  # 前重后轻，制造大等价类与稀有取值
RARE_ZIPS = ["99999", "88888", "70001"]

OCCUPATIONS = [
    "engineer", "teacher", "nurse", "salesperson", "clerk", "driver",
    "chef", "accountant", "lawyer", "mechanic", "manager", "laborer",
    "developer", "designer", "cashier", "guard", "farmer", "plumber",
    "electrician", "receptionist",
]
RARE_OCCUPATIONS = ["astronaut", "jockey", "diplomat", "deep_sea_diver"]

DISEASES = ["healthy", "hypertension", "diabetes", "asthma", "flu",
            "arthritis", "migraine", "allergy"]

NAMES = [f"person_{i}" for i in range(500)]

# 确定性针尖记录：罕见职业 + 罕见邮编 + 极端年龄，必然构成唯一组合。
NEEDLES = [
    (79, "M", "99999", "astronaut", "hypertension"),
    (18, "F", "88888", "jockey", "asthma"),
    (73, "F", "70001", "diplomat", "diabetes"),
]


def _weights():
    global ZIP_WEIGHTS
    if ZIP_WEIGHTS is None:
        ZIP_WEIGHTS = [1.0 / (1 + i // 10) for i in range(len(ZIPS))]
    return ZIP_WEIGHTS


def generate(n, seed=20261005, plant_needles=True):
    """生成 n 条记录，返回 (headers, rows)。

    同一 seed 下输出完全确定，结果可复现、可复核。
    字符串尽量复用池中对象，100 万行内存占用可控。
    """
    rng = random.Random(seed)
    zip_weights = _weights()
    occ_weights = [1.0 / (1 + i // 5) for i in range(len(OCCUPATIONS))]
    rows = []

    for i in range(n):
        if plant_needles and i < len(NEEDLES):
            age, gender, zip_code, occupation, disease = NEEDLES[i]
        else:
            age = rng.randrange(18, 81)
            gender = rng.choice(GENDERS)
            if rng.random() < 0.004:
                zip_code = rng.choice(RARE_ZIPS)
            else:
                zip_code = rng.choices(ZIPS, weights=zip_weights, k=1)[0]
            if rng.random() < 0.003:
                occupation = rng.choice(RARE_OCCUPATIONS)
            else:
                occupation = rng.choices(OCCUPATIONS,
                                         weights=occ_weights, k=1)[0]
            disease = rng.choices(
                DISEASES,
                weights=[40, 14, 10, 8, 12, 6, 5, 5], k=1)[0]
        rid = f"N{str(i + 1).zfill(9)}"
        name = NAMES[i % len(NAMES)]
        rows.append((rid, name, age, gender, zip_code, occupation, disease))
    return list(HEADERS), rows


# 职业 -> 职业大类，用于泛化演示。
OCCUPATION_CATEGORY = {
    "engineer": "technical", "developer": "technical",
    "designer": "technical", "electrician": "technical",
    "mechanic": "technical", "plumber": "technical",
    "teacher": "professional", "nurse": "professional",
    "lawyer": "professional", "accountant": "professional",
    "diplomat": "professional",
    "salesperson": "service", "clerk": "service", "cashier": "service",
    "receptionist": "service", "chef": "service", "guard": "service",
    "driver": "service", "jockey": "service",
    "manager": "management",
    "laborer": "manual", "farmer": "manual", "deep_sea_diver": "manual",
    "astronaut": "other",
}
