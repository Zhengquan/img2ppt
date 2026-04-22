#!/usr/bin/env python3
"""打包 Skill 为 zip 并可一键发布到各平台（Cursor / CodeBuddy / Codex / WorkBuddy / Claude）。

用法示例：
    # 仅打包到 ./pkgs（默认排除 .env 等敏感配置）
    python scripts/package_skill_zip.py

    # 打包并发布到 cursor + codebuddy
    python scripts/package_skill_zip.py --platform cursor,codebuddy

    # 发布到所有已知平台
    python scripts/package_skill_zip.py --platform all

    # 允许保留 .env 本地分发（文件名自动带 -WITHCONFIG 警示标记）
    python scripts/package_skill_zip.py --keep-config --platform cursor

    # 只安装到本机平台，不产出 zip
    python scripts/package_skill_zip.py --platform all --no-zip

    # 明确只打包、绝不碰本机任何平台（即使误写 --platform 也会拒绝执行）
    python scripts/package_skill_zip.py --package-only
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


# ---------------- 过滤规则 ----------------

EXCLUDED_DIRS = {
    ".git",
    ".cursor",
    ".github",
    ".idea",
    ".vscode",
    ".codebuddy",
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

# 始终排除（与是否 keep-config 无关）
ALWAYS_EXCLUDED_PATTERNS = [
    ".DS_Store",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.tmp",
    "*.swp",
    "*.swo",
    "*.pptx",
    "*.pdf",
    "*.pem",
    "*.key",
    "*.p12",
    "*.crt",
    "*.cer",
    "*.jks",
    "*.pfx",
]

# 敏感配置：默认排除，--keep-config 时才保留
SENSITIVE_CONFIG_PATTERNS = [
    ".env",
    ".env.*",
]


# ---------------- 平台注册表 ----------------

HOME = Path.home()

# 每个平台指向一个 skills 目录，zip 解压后的顶层文件夹会被放入该目录下
# 路径不存在的平台会被自动跳过（除非用户 --force 创建）
PLATFORM_SKILL_DIRS: dict[str, Path] = {
    "cursor": HOME / ".cursor" / "skills",
    "codebuddy": HOME / ".codebuddy" / "skills",
    "codex": HOME / ".codex" / "skills",
    "workbuddy": HOME / ".workbuddy" / "skills",
    "claude": HOME / ".claude" / "skills",
}


# ---------------- 工具函数 ----------------

def should_exclude(path: Path, rel_path: Path, keep_config: bool) -> bool:
    parts = rel_path.parts
    if any(p in EXCLUDED_DIRS for p in parts[:-1]):
        return True
    if path.is_dir() and path.name in EXCLUDED_DIRS:
        return True
    name = rel_path.name
    if any(fnmatch.fnmatch(name, pat) for pat in ALWAYS_EXCLUDED_PATTERNS):
        return True
    if not keep_config and any(fnmatch.fnmatch(name, pat) for pat in SENSITIVE_CONFIG_PATTERNS):
        return True
    return False


def collect_files(root: Path, keep_config: bool) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if should_exclude(p, rel, keep_config):
            continue
        if p.is_file():
            files.append(p)
    files.sort()
    return files


def read_skill_name_from_skill_md(repo_root: Path) -> str | None:
    """读取 SKILL.md 中 YAML frontmatter 的 name 字段。

    平台要求：zip 内顶层目录名 / 安装到 skills/ 下的目录名须与该 name 一致。
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


def make_zip_name(top_folder: str, keep_config: bool) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^\w.\-]+", "_", top_folder).strip("._") or "skill"
    tag = "-WITHCONFIG" if keep_config else ""
    return f"{safe}-skill-{ts}{tag}.zip"


def normalize_zip_name(raw: str | None, top_folder: str, keep_config: bool) -> str:
    """处理用户自定义文件名；若 keep_config=True 则强制附加 WITHCONFIG 标记避免误分发。"""
    name = raw or make_zip_name(top_folder, keep_config)
    if name.lower().endswith(".zip"):
        stem, ext = name[:-4], name[-4:]
    else:
        stem, ext = name, ".zip"
    if keep_config and "withconfig" not in stem.lower():
        stem = f"{stem}-WITHCONFIG"
    return f"{stem}{ext}"


def parse_platforms(raw: str | None) -> list[str]:
    if not raw:
        return []
    items = [x.strip().lower() for x in raw.split(",") if x.strip()]
    if "all" in items:
        return list(PLATFORM_SKILL_DIRS.keys())
    unknown = [x for x in items if x not in PLATFORM_SKILL_DIRS]
    if unknown:
        raise SystemExit(
            f"未知平台: {unknown}；支持: {sorted(PLATFORM_SKILL_DIRS.keys())} 或 all"
        )
    # 去重且保持顺序
    seen: set[str] = set()
    result: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            result.append(x)
    return result


# ---------------- 打包 / 安装 ----------------

def package_skill(
    repo_root: Path,
    output_dir: Path,
    zip_name: str,
    top_folder: str,
    keep_config: bool,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / zip_name

    prefix = top_folder.strip().strip("/").strip("\\")
    if not prefix or prefix in {".", ".."}:
        raise ValueError("top_folder 须为非空的合法目录名")

    files = collect_files(repo_root, keep_config=keep_config)
    with ZipFile(zip_path, mode="w", compression=ZIP_DEFLATED) as zf:
        for fp in files:
            rel = fp.relative_to(repo_root).as_posix()
            zf.write(fp, arcname=f"{prefix}/{rel}")
    return zip_path


def install_to_platform(
    repo_root: Path,
    top_folder: str,
    platform: str,
    platform_dir: Path,
    keep_config: bool,
    force_create: bool,
    dry_run: bool,
) -> tuple[bool, str]:
    """把 skill 目录拷贝到平台的 skills/ 目录下。返回 (是否成功, 描述)。"""
    # 解析软链（例如 ~/.cursor/skills → ~/.workbuddy/skills）
    resolved_parent = platform_dir.resolve() if platform_dir.exists() else platform_dir
    if not platform_dir.exists():
        if not force_create:
            return False, f"跳过 {platform}: 目录不存在 {platform_dir}（加 --force-create-dir 可自动创建）"
        if not dry_run:
            resolved_parent.mkdir(parents=True, exist_ok=True)

    target = resolved_parent / top_folder
    backup_note = ""
    if target.exists() or target.is_symlink():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = target.with_name(f"{target.name}.bak-{ts}")
        backup_note = f"（旧目录已备份为 {backup.name}）"
        if not dry_run:
            try:
                target.rename(backup)
            except OSError:
                # 跨卷或其他异常：改用复制+删除
                shutil.move(str(target), str(backup))

    if dry_run:
        return True, f"[dry-run] 将安装到 {target} {backup_note}".strip()

    # 复制 repo_root → target，同样走 collect_files 规则，保持与 zip 一致
    files = collect_files(repo_root, keep_config=keep_config)
    target.mkdir(parents=True, exist_ok=False)
    for fp in files:
        rel = fp.relative_to(repo_root)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fp, dst)
    return True, f"已安装到 {target} {backup_note}".strip()


# ---------------- main ----------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="打包当前仓库为 Skill zip，并可一键发布到各平台（Cursor/CodeBuddy/Codex/WorkBuddy/Claude）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--root", default=".", help="项目根目录，默认当前目录")
    parser.add_argument("--out-dir", default="pkgs", help="zip 输出目录，默认 pkgs")
    parser.add_argument(
        "--name",
        default=None,
        help="自定义 zip 文件名（可不带 .zip）；--keep-config 时会强制附加 WITHCONFIG 标识",
    )
    parser.add_argument(
        "--top-folder",
        default=None,
        help="zip 内顶层文件夹名；默认读取 SKILL.md 的 name 字段，若无则用项目根目录名",
    )
    parser.add_argument(
        "--keep-config",
        action="store_true",
        help="保留 .env 等敏感配置一起打包 / 发布（默认排除）。启用后 zip 文件名会带 -WITHCONFIG 以防误分发",
    )
    parser.add_argument(
        "--platform",
        default=None,
        help=(
            "逗号分隔的目标平台，发布到本机对应 skills/ 目录。"
            f"可选: {','.join(PLATFORM_SKILL_DIRS.keys())} 或 all；不传则只打 zip"
        ),
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="只发布到 --platform 指定的平台，不生成 zip（必须同时指定 --platform）",
    )
    parser.add_argument(
        "--package-only",
        action="store_true",
        help="只打包 zip，绝不发布到任何平台（与 --platform/--no-zip 互斥，用于避免覆盖本机 skill）",
    )
    parser.add_argument(
        "--force-create-dir",
        action="store_true",
        help="当平台的 skills/ 目录不存在时自动创建（默认跳过）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要发生的操作，不写任何文件",
    )
    args = parser.parse_args()

    repo_root = Path(args.root).resolve()
    out_dir = Path(args.out_dir).resolve()
    from_skill = read_skill_name_from_skill_md(repo_root)
    top_folder = (args.top_folder or from_skill or repo_root.name).strip()

    platforms = parse_platforms(args.platform)
    if args.package_only and (platforms or args.no_zip):
        parser.error("--package-only 不能与 --platform / --no-zip 同时使用")
    if args.no_zip and not platforms:
        parser.error("--no-zip 需要同时指定 --platform")

    # 敏感配置告警
    if args.keep_config:
        env_file = repo_root / ".env"
        has_env = env_file.is_file()
        print("⚠️  已启用 --keep-config：.env 等敏感配置会被包含！")
        if has_env:
            print(f"   检测到 {env_file.relative_to(repo_root)}，请勿把产出 zip 发给他人。")
        print("   为避免误分发，zip 文件名已附加 -WITHCONFIG 标识。")

    # 打 zip
    zip_path: Path | None = None
    if not args.no_zip:
        zip_name = normalize_zip_name(args.name, top_folder, keep_config=args.keep_config)
        if args.dry_run:
            print(f"[dry-run] 将生成 zip: {out_dir / zip_name}")
            zip_path = out_dir / zip_name
        else:
            zip_path = package_skill(
                repo_root=repo_root,
                output_dir=out_dir,
                zip_name=zip_name,
                top_folder=top_folder,
                keep_config=args.keep_config,
            )
            print(f"✔ 打包完成: {zip_path}")
            print(f"  zip 顶层目录: {top_folder}/（须与 SKILL.md 中 name 一致）")

    if from_skill and top_folder != from_skill:
        print(
            f"⚠️  SKILL.md name={from_skill!r}，但顶层为 {top_folder!r}；"
            "平台校验可能失败，建议不传 --top-folder 或改为与 name 相同。",
            file=sys.stderr,
        )

    # 发布到平台
    if platforms:
        print(f"\n发布到平台: {', '.join(platforms)}")
        ok_count = 0
        for p in platforms:
            platform_dir = PLATFORM_SKILL_DIRS[p]
            ok, msg = install_to_platform(
                repo_root=repo_root,
                top_folder=top_folder,
                platform=p,
                platform_dir=platform_dir,
                keep_config=args.keep_config,
                force_create=args.force_create_dir,
                dry_run=args.dry_run,
            )
            tag = "✔" if ok else "✗"
            print(f"  {tag} [{p}] {msg}")
            if ok:
                ok_count += 1
        print(f"\n成功 {ok_count}/{len(platforms)}")
    elif args.package_only:
        print("\n已使用 --package-only：跳过所有平台发布，本机 skill 目录未被修改。")

    if zip_path and args.keep_config:
        print(
            f"\n❗ 再次提醒：{zip_path.name} 内含敏感配置（.env），请仅本机使用，勿上传仓库或外发。"
        )


if __name__ == "__main__":
    main()
