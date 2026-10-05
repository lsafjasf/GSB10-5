"""textgen -- 为评测/压测生成合成中文文本（仅标准库）。

用词表随机拼接成"句子"和"文档"。随机文本之间几乎不共享 10 字以上的
连续片段，因此无关文档天然可作为负样本；BOILER 中的固定套话用于
构造"模板型"误报场景。
"""

import random

WORDS = (
    "公司 合同 项目 工程 数据 系统 安全 管理 用户 服务 产品 技术 开发 测试 "
    "部署 服务器 数据库 网络 协议 接口 模块 版本 需求 设计 文档 报告 会议 "
    "计划 预算 成本 风险 质量 进度 人员 部门 客户 市场 销售 财务 审计 法律 "
    "合规 保密 权限 密码 密钥 证书 备份 恢复 监控 告警 日志 性能 优化 升级 "
    "迁移 架构 缓存 队列 消息 任务 调度 集群 节点 容器 镜像 仓库 分支 代码 "
    "评审 发布 回滚 故障 应急 预案 演练 培训 考核 制度 流程 规范 标准 指标 "
    "统计 分析 平台 业务 场景 方案 验收 交付 运维 巡检 漏洞 补丁 防护 入侵 "
    "加密 解密 签名 验签 令牌 会话 超时 重试 熔断 限流 降级 扩容 缩容 灰度 "
    "滚动 停机 维护 窗口 通知 邮件 短信 值班 机房 带宽 流量 域名 证书 快照 "
    "镜像 备份 容灾 切换 演练 工单 审批 抄送 归档 留存 销毁 脱敏 水印 追溯"
).split()

# 用于"改写"的常用字池
CHARS = (
    "的一是了我不人在他有这个上们来到时大地为子中你说生国年着就那和要"
    "她出也得里后自以会家可下而过天去能对小多然于心学么之都好看起发"
    "当没成只如事把还用第样道想作种开美总从无情己面最女但现前些所同"
)

# 模板套话：会被大量文档共享，用来测试误报控制
BOILER = [
    "本文件属于公司机密信息未经授权不得复制传播",
    "如有泄露公司将依法追究相关人员的法律责任",
    "本方案最终解释权归项目管理委员会所有",
]


def gen_sentence(rng: random.Random, lo: int = 4, hi: int = 9) -> str:
    return "".join(rng.choices(WORDS, k=rng.randint(lo, hi)))


def gen_doc(rng: random.Random, n_sent: int) -> str:
    return "。".join(gen_sentence(rng) for _ in range(n_sent)) + "。"


def gen_short_doc(rng: random.Random) -> str:
    """25~70 字左右的短文档。"""
    return "。".join(gen_sentence(rng, 3, 6)
                     for _ in range(rng.randint(3, 5))) + "。"


def corrupt(text: str, rng: random.Random, rate: float) -> str:
    """按给定比例随机替换字符（模拟改字）。"""
    chars = list(text)
    n = max(1, int(len(chars) * rate))
    for _ in range(n):
        i = rng.randrange(len(chars))
        chars[i] = rng.choice(CHARS)
    return "".join(chars)


def reorder(text: str, rng: random.Random) -> str:
    """句子级乱序（模拟换序改写）。"""
    sents = [s for s in text.split("。") if s]
    rng.shuffle(sents)
    return "。".join(sents) + "。"


def splice(src: str, rng: random.Random, frag_len: int,
           pad_sent: int = 8) -> str:
    """从 src 截取一段，拼进无关文本中间（模拟拆散拼接）。"""
    if len(src) <= frag_len:
        frag = src
    else:
        start = rng.randrange(0, len(src) - frag_len)
        frag = src[start:start + frag_len]
    return gen_doc(rng, pad_sent) + frag + "。" + gen_doc(rng, pad_sent)
