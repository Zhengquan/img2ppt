"""解析 Skill 仓库根目录与 .env 路径，便于多宿主（Cursor / Codex / Code Buddy 等）覆盖默认推导。"""
from __future__ import annotations

import os
from pathlib import Path


def package_repo_root() -> Path:
    """从本包在仓库中的位置推导仓库根（与 `cli.py` 同级）。"""
    return Path(__file__).resolve().parent.parent.parent


def resolve_repo_root() -> Path:
    """
    若设置环境变量 ``IMAGES2PPT_ROOT``，则使用该路径为仓库根；
    否则与 ``package_repo_root()`` 一致。
    """
    explicit = os.environ.get("IMAGES2PPT_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return package_repo_root()


def resolve_dotenv_path() -> Path:
    """
    解析要加载的 ``.env`` 文件路径，优先级：

    1. ``IMAGES2PPT_ENV_FILE``：指向具体 .env 文件（绝对或相对路径均可）
    2. ``IMAGES2PPT_ROOT`` 下的 ``.env``
    3. 包推导的仓库根下的 ``.env``
    """
    env_file = os.environ.get("IMAGES2PPT_ENV_FILE", "").strip()
    if env_file:
        return Path(env_file).expanduser().resolve()
    return resolve_repo_root() / ".env"
