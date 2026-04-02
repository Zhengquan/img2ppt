#!/usr/bin/env python3
"""打包 Skill zip 到 ./pkgs，并排除敏感与仓库元信息。"""

from __future__ import annotations

import argparse
import fnmatch
import re
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


EXCLUDED_DIRS = {
    ".git",
    ".cursor",
    ".github",
    ".idea",
    ".vscode",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "build",
    "dist",
    "pkgs",  # 防止把旧打包产物再次打进去
}

EXCLUDED_FILE_PATTERNS = [
    ".DS_Store",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.tmp",
    "*.swp",
    "*.swo",
    "*.pptx",
    "*.pdf",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.crt",
    "*.cer",
    "*.jks",
    "*.pfx",
]


def should_exclude(path: Path, rel_path: Path) -> bool:
    parts = rel_path.parts
    if any(p in EXCLUDED_DIRS for p in parts[:-1]):
        return True
    if path.is_dir() and path.name in EXCLUDED_DIRS:
        return True
    name = rel_path.name
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDED_FILE_PATTERNS)


def collect_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if should_exclude(p, rel):
            continue
        if p.is_file():
            files.append(p)
    files.sort()
    return files


def read_skill_name_from_skill_md(repo_root: Path) -> str | None:
    """
    读取 SKILL.md 中 YAML frontmatter 的 name 字段。
    平台要求：zip 内顶层目录名须与该 name 一致。
    """
    skill_md = repo_root / "SKILL.md"
    if not skill_md.is_file():
        return None
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    header = text[3:end]
    for raw in header.splitlines():
        line = raw.strip()
        if not line.startswith("name:"):
            continue
        val = line[5:].strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        val = val.strip()
        return val or None
    return None


def make_zip_name(top_folder: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^\w.\-]+", "_", top_folder).strip("._") or "skill"
    return f"{safe}-skill-{ts}.zip"


def package_skill(
    repo_root: Path,
    output_dir: Path,
    output_name: str | None,
    top_folder: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_name = output_name or make_zip_name(top_folder)
    if not zip_name.endswith(".zip"):
        zip_name = f"{zip_name}.zip"
    zip_path = output_dir / zip_name

    # 解压后所有文件落在单一顶层目录下，避免散落在当前工作目录
    prefix = top_folder.strip().strip("/").strip("\\")
    if not prefix or prefix in {".", ".."}:
        raise ValueError("top_folder 须为非空的合法目录名")

    files = collect_files(repo_root)
    with ZipFile(zip_path, mode="w", compression=ZIP_DEFLATED) as zf:
        for fp in files:
            rel = fp.relative_to(repo_root).as_posix()
            zf.write(fp, arcname=f"{prefix}/{rel}")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="打包当前仓库为 Skill zip（默认输出到 ./pkgs）",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="项目根目录，默认当前目录",
    )
    parser.add_argument(
        "--out-dir",
        default="pkgs",
        help="打包输出目录，默认 pkgs",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="可选 zip 文件名（可不带 .zip）",
    )
    parser.add_argument(
        "--top-folder",
        default=None,
        help="zip 内顶层文件夹名；默认读取 SKILL.md 的 name 字段，若无则使用项目根目录名",
    )
    args = parser.parse_args()

    repo_root = Path(args.root).resolve()
    out_dir = Path(args.out_dir).resolve()
    from_skill = read_skill_name_from_skill_md(repo_root)
    top_folder = (args.top_folder or from_skill or repo_root.name).strip()
    zip_path = package_skill(
        repo_root=repo_root,
        output_dir=out_dir,
        output_name=args.name,
        top_folder=top_folder,
    )
    print(f"打包完成: {zip_path}")
    print(f"zip 内顶层目录: {top_folder}/（须与 SKILL.md 中 name 一致）")
    if from_skill and top_folder != from_skill:
        print(
            f"警告: SKILL.md 中 name={from_skill!r}，zip 顶层为 {top_folder!r}，"
            "平台校验可能失败；请去掉 --top-folder 或改为与 name 相同。",
        )


if __name__ == "__main__":
    main()
