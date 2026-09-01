# -*- coding: utf-8 -*-
"""知识库打包：把每个技能的 知识库/*.md 合并成单个 知识库.md（精简发布形态）。

GitHub 网页上传有 99 文件/批限制；把 ~140 个零散知识文件压成 ~20 个单文件。
检索代码（app/skills/knowledge.py）同时支持目录形态与单文件形态。
运行：python scripts/bundle_knowledge.py
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "data" / "skills"


def bundle_skill(skill_dir: Path) -> int:
    kb = skill_dir / "知识库"
    if not kb.is_dir():
        return 0
    files = sorted(kb.glob("*.md"))
    if not files:
        return 0
    parts = []
    for f in files:
        content = f.read_text(encoding="utf-8", errors="replace").strip()
        parts.append(f"# {f.stem}\n\n{content}")
    bundle = skill_dir / "知识库.md"
    bundle.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
    for f in files:
        f.unlink()
    kb.rmdir()
    print(f"  打包 {skill_dir.name}: {len(files)} 份 → 知识库.md")
    return len(files)


def main():
    total = 0
    for d in sorted(SKILLS.iterdir()):
        if d.is_dir():
            total += bundle_skill(d)
    print(f"完成：共合并 {total} 份知识文件")


if __name__ == "__main__":
    main()
