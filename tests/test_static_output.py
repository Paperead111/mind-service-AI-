"""验收 #7：零静态输出 grep 测试。

规则（v4.1 裁定）：app/*.py 内不允许出现"面向用户的硬编码句"——
`return "中文…"`、`return f"中文…"` 直接返回自然语言句一律为零。
豁免：日志（life_log/audit_log/decision_log 标签）、内部枚举/标记、
data/lexicon/ 词库（数据文件）、前端 UI 占位符。
"""
import re
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"

CJK = r"\u4e00-\u9fff"
# 直接 return 一个含中文的字符串字面量（含 f-string）→ 面向用户硬编码句
RETURN_LITERAL = re.compile(
    rf"^\s*return\s+f?['\"]([^'\"]*[{CJK}][^'\"]*)['\"]\s*(?:#.*)?$",
    re.MULTILINE,
)
# f-string 拼出的自然语言句（含中文 + 插值）
RETURN_FSTRING = re.compile(
    rf"^\s*return\s+f['\"]([^'\"]*[{CJK}][^'\"]*)['\"]\s*$",
    re.MULTILINE,
)

# 豁免清单（内部标记/提示词内容/工具回传，非她的输出文本）
# (文件名, 函数名)；函数名 "*" = 整个文件豁免（如 inject.py 输出只进 LLM 上下文）
ALLOWLIST = {
    ("app/decisions/followup.py", "recognize_state"),   # 决策状态枚举（内部标记）
    ("app/persona/inject.py", "*"),                     # 启动注入只进 LLM 上下文，非用户输出
    ("app/skills/loader.py", "dispatch_tool"),          # 工具执行错误回传给 LLM 循环
    ("app/skills/knowledge.py", "knowledge_lookup"),    # 知识库检索回传（工具结果）
}

EXEMPT_SUBSTRINGS = ("LLMError", "HTTPException", "SystemExit")


def _current_function(lines: list[str], lineno: int) -> str | None:
    """返回行号所在的最内层函数名（缩进感知，向上找第一个缩进更小的 def）。"""
    target = lines[lineno - 1]
    indent = len(target) - len(target.lstrip())
    for i in range(lineno - 2, -1, -1):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        cur_indent = len(line) - len(stripped)
        m = re.match(r"def\s+(\w+)\s*\(", stripped)
        if m and cur_indent < indent:
            return m.group(1)
        if cur_indent == 0:
            return None
    return None


def _is_allowed(rel: str, func: str | None) -> bool:
    return (rel, func) in ALLOWLIST or (rel, "*") in ALLOWLIST


class TestZeroStaticOutput(unittest.TestCase):
    def test_no_hardcoded_user_facing_return_strings(self):
        offenders = []
        for py in sorted(APP_DIR.rglob("*.py")):
            if "__pycache__" in str(py):
                continue
            rel = py.relative_to(APP_DIR.parent).as_posix()
            lines = py.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, 1):
                m = RETURN_LITERAL.search(line)
                if m and not any(e in m.group(1) for e in EXEMPT_SUBSTRINGS):
                    if not _is_allowed(rel, _current_function(lines, lineno)):
                        offenders.append(f"{rel}:{lineno}: {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "app/*.py 存在面向用户的硬编码 return 句：\n" + "\n".join(offenders))

    def test_no_fstring_sentence_returns(self):
        offenders = []
        for py in sorted(APP_DIR.rglob("*.py")):
            if "__pycache__" in str(py):
                continue
            rel = py.relative_to(APP_DIR.parent).as_posix()
            lines = py.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, 1):
                m = RETURN_FSTRING.search(line)
                if m and not any(e in m.group(1) for e in EXEMPT_SUBSTRINGS):
                    if not _is_allowed(rel, _current_function(lines, lineno)):
                        offenders.append(f"{rel}:{lineno}: {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "app/*.py 存在 f-string 拼接自然语言句：\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
