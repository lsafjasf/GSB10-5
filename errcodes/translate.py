"""翻译与覆盖率统计。

locale 文件格式（JSON）::

    {"locale": "zh-CN", "messages": {"E0101001": "参数无效"}}

键统一用错误码，避免错误名变动导致翻译错配。
某条码无翻译时按 fallback 策略回退：
- "default"：回退到定义里的 default_message；
- "code"   ：回退到错误码本身（前端可直接显示原码排查）。
未注册的码同样回退为原码。
"""

import json

FALLBACK_DEFAULT = "default"
FALLBACK_CODE = "code"


def load_locale(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("locale", "unknown"), data.get("messages", {})


class Translator:
    def __init__(self, registry, locale, messages,
                 fallback=FALLBACK_DEFAULT, default_text="Unknown error"):
        self.registry = registry
        self.locale = locale
        self.messages = messages
        self.fallback = fallback
        self.default_text = default_text

    def translate(self, code):
        """返回 (文案, 来源)。来源：translated/default/code/missing。"""
        entry = self.registry.entries.get(code)
        translated = self.messages.get(code)
        if translated:
            return translated, "translated"
        if entry is not None:
            if self.fallback == FALLBACK_DEFAULT:
                return entry["def"].get("default_message") or code, "default"
            return code, "code"
        # 未注册码：定义缺失，只能显示原码
        return code, "missing"

    def coverage(self):
        """统计覆盖率，返回汇总与缺失清单（含分模块拆分）。"""
        total = 0
        translated = 0
        missing = []
        by_module = {}

        for code, entry in self.registry.entries.items():
            module = entry["def"].get("module", "?")
            stats = by_module.setdefault(module, {"total": 0, "translated": 0})
            total += 1
            stats["total"] += 1
            if code in self.messages and self.messages[code]:
                translated += 1
                stats["translated"] += 1
            else:
                missing.append({
                    "code": code,
                    "module": module,
                    "name": entry["def"].get("name"),
                    "default_message": entry["def"].get("default_message"),
                })

        percent = (translated * 100.0 / total) if total else 100.0
        return {
            "locale": self.locale,
            "total": total,
            "translated": translated,
            "missing_count": len(missing),
            "coverage_percent": round(percent, 2),
            "by_module": {
                m: {
                    "total": s["total"],
                    "translated": s["translated"],
                    "coverage_percent": round(
                        s["translated"] * 100.0 / s["total"], 2)
                    if s["total"] else 100.0,
                }
                for m, s in sorted(by_module.items())
            },
            "missing": missing,
        }
