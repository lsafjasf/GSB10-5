"""演示: 构造一个含四种引用情形的小场景, 输出分层影响清单。

运行: python3 demo.py
"""

from cfgimpact import ImpactGraph, ImpactAnalyzer, format_report
from selftest import build_graph


def main():
    analyzer = ImpactAnalyzer(build_graph())

    cases = [
        ["payment.db.timeout"],       # 直接引用 + 同名不同义判定
        ["payment.db.host"],          # 间接引用(配置模板链)
        ["cache.cn.ttl"],             # 动态拼接(已解析) + 模块依赖链
        ["feature.new_ui.enabled"],   # 动态拼接(未解析, 待人工确认)
    ]
    for changed in cases:
        print("=" * 72)
        print(format_report(analyzer.analyze(changed)))
    print("=" * 72)
    print("同名不同义判定: payment.db.timeout 变更仅影响 pay-service;")
    print("search-service 引用的是 search.db.timeout, 未被误判。")


if __name__ == "__main__":
    main()
