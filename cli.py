#!/usr/bin/env python3
"""入口：--input image.png 或 file.pdf --output out.pptx"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# 保证从项目根运行时可找到 src
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _load_manifest(path: Path) -> dict:
    """读取 run_manifest.json；不存在或损坏时返回空 dict。"""
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_manifest(path: Path, data: dict) -> None:
    """原子写回 manifest（先写 .tmp 再 rename），失败时静默不抛出，避免影响主流程。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except OSError as e:
        print(f"[warn] 写回 run-manifest 失败：{e}", file=sys.stderr)


def _update_manifest_section(
    manifest_path: "Path | None",
    *,
    status: str,
    output: "Path | None" = None,
    error: "str | None" = None,
    started_at: "str | None" = None,
) -> "str | None":
    """更新 manifest 顶层的 `images_2_ppt` 段。返回 started_at 以便上层串联。"""
    if manifest_path is None:
        return started_at
    data = _load_manifest(manifest_path)
    section = data.get("images_2_ppt") if isinstance(data.get("images_2_ppt"), dict) else {}
    now = _now_iso()
    if started_at is None:
        started_at = section.get("started_at") or now
    section["status"] = status
    section["started_at"] = started_at
    section["updated_at"] = now
    if status in ("succeeded", "failed"):
        section["finished_at"] = now
    if output is not None:
        section["output"] = str(output)
    if error is not None:
        section["error"] = error
    elif status == "succeeded":
        section.pop("error", None)
    data["images_2_ppt"] = section
    data["updated_at"] = now
    _save_manifest(manifest_path, data)
    return started_at


def _progress_callback_with_bar():
    """返回一个使用 tqdm 进度条的进度回调。总步数 = 1(加载) + 页数×3(OCR/样式/去字) + 页数(导出)。"""
    from tqdm import tqdm

    bar = None

    def _cb(phase: str, current: int, total: int, message: str) -> None:
        nonlocal bar
        if phase == "load":
            if "已加载" in message and total == 1:
                parts = message.split()
                n = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 1
                total_steps = 1 + 4 * n
                bar = tqdm(total=total_steps, unit="步", ncols=100, desc="处理进度", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} {postfix}")
            if bar:
                bar.update(1)
                bar.set_postfix_str(message)
        elif phase == "page":
            if bar:
                bar.update(1)
                bar.set_postfix_str(f"第 {current}/{total} 页 - {message}" if total > 1 else message)
        elif phase == "export":
            if current == 0 and "开始" in message:
                if bar:
                    bar.set_postfix_str(message)
            elif "完成" in message:
                if bar:
                    bar.update(1)
                    bar.set_postfix_str("完成")
                    bar.close()
                    bar = None
            else:
                if bar:
                    bar.update(1)
                    bar.set_postfix_str(f"导出第 {current}/{total} 页" if total > 1 else message)
    return _cb


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将单张图片 / 图片目录 / PDF 转为可编辑 PPT（需已 pip install -r requirements.txt）",
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="输入：本地路径（单张图片 / 图片目录 / PDF）或 http/https 图片或 PDF 直链（自动下载）",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="输出的 .pptx 路径；不指定时，默认与输入同名（单文件）或在目录旁生成同名 .pptx",
    )
    parser.add_argument(
        "--pdf-output",
        default=None,
        help="当 --input 为图片目录时，可选输出合并后的 PDF 路径；默认与 ppt 同名 .pdf",
    )
    parser.add_argument(
        "--font-normal",
        default=None,
        help="正文字体名（西文 latin；不指定时按系统语言自适应：中文→腾讯字体 W3，英文→TencentSans W3）",
    )
    parser.add_argument(
        "--font-bold",
        default=None,
        help="标题/强调字体名（西文 latin；不指定时按系统语言自适应：中文→腾讯字体 W7，英文→TencentSans W7）",
    )
    parser.add_argument(
        "--font-ea-normal",
        default=None,
        help="正文东亚字体名（不指定时按系统语言自适应：中文→腾讯字体 W3，英文→TencentSans W3）",
    )
    parser.add_argument(
        "--font-ea-bold",
        default=None,
        help="标题/强调东亚字体名（不指定时按系统语言自适应：中文→腾讯字体 W7，英文→TencentSans W7）",
    )
    parser.add_argument(
        "--text-lang",
        default="zh-CN",
        help="文本 run 的主语言标签（默认 zh-CN，避免中文被判成英文而触发拼写检查红线）",
    )
    parser.add_argument(
        "--text-alt-lang",
        default="en-US",
        help="文本 run 的副语言标签（默认 en-US，用于夹杂英文）",
    )
    parser.add_argument(
        "--text-pad-ratio",
        type=float,
        default=0.08,
        help="文本框向右扩宽比例（默认 0.08，防止贴边折行；0 表示不扩宽）",
    )
    parser.add_argument(
        "--width-safety",
        type=float,
        default=0.96,
        help="字号反推的宽度安全系数（默认 0.96，越小字号越保守；临界折行严重时可降到 0.90）",
    )
    parser.add_argument(
        "--no-merge-textbox",
        action="store_true",
        help="关闭同行短文本框合并（调试用，默认开启合并）",
    )
    parser.add_argument(
        "--ocr-engine",
        default="auto",
        choices=["auto", "tencent", "baidu"],
        help="OCR 引擎：auto(默认，优先腾讯)、tencent、baidu",
    )
    parser.add_argument(
        "--slide-size-mode",
        default="widescreen",
        choices=["widescreen", "native"],
        help=(
            "PPT 页面尺寸模式："
            "widescreen=固定 16:9，保持现有行为（默认）；"
            "native=单张图片场景下严格匹配输入图片尺寸，适合海报等需要 1:1 文字调整的场景"
        ),
    )
    parser.add_argument(
        "--run-manifest",
        default=None,
        help=(
            "可选：上游 orchestrator 的 run_manifest.json 路径。"
            "若指定，导出前/后会在该 JSON 顶层写入/更新 `images_2_ppt` 段，"
            "记录 status/started_at/finished_at/output/error，便于跟踪整体流水线状态。"
            "manifest 的其它字段不动；文件不存在时会按需创建父目录并新建。"
        ),
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="不输出处理进度",
    )
    args = parser.parse_args()

    from src.pipeline import run_pipeline
    from src.extract.ocr import ocr_env_setup_help, resolve_ocr_engine
    from src.input.loader import suggest_output_pptx_path
    from src.utils.fonts import default_fonts

    # 字体：未显式指定则按系统语言自适应
    _dl_normal, _dl_bold, _dea_normal, _dea_bold = default_fonts()
    font_normal = args.font_normal or _dl_normal
    font_bold = args.font_bold or _dl_bold
    font_ea_normal = args.font_ea_normal or _dea_normal
    font_ea_bold = args.font_ea_bold or _dea_bold

    raw_input = args.input.strip()
    if args.output:
        pptx_path = Path(args.output)
    else:
        pptx_path = suggest_output_pptx_path(raw_input)

    try:
        selected_engine = resolve_ocr_engine(ocr_engine=args.ocr_engine)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        print(ocr_env_setup_help(), file=sys.stderr)
        sys.exit(2)

    print(f"开始处理… OCR 引擎: {selected_engine}")
    print(f"字体: latin={font_normal}/{font_bold}  ea={font_ea_normal}/{font_ea_bold}")

    manifest_path = Path(args.run_manifest).expanduser() if args.run_manifest else None
    started_at = _update_manifest_section(manifest_path, status="running", output=pptx_path)

    try:
        run_pipeline(
            raw_input,
            pptx_path,
            font_normal=font_normal,
            font_bold=font_bold,
            font_ea_normal=font_ea_normal,
            font_ea_bold=font_ea_bold,
            text_lang=args.text_lang,
            text_alt_lang=args.text_alt_lang,
            text_pad_ratio=args.text_pad_ratio,
            width_safety=args.width_safety,
            merge_textbox=not args.no_merge_textbox,
            ocr_engine=args.ocr_engine,
            pdf_output_path=args.pdf_output,
            slide_size_mode=args.slide_size_mode,
            progress_callback=None if args.quiet else _progress_callback_with_bar(),
        )
    except Exception as e:  # noqa: BLE001 - 失败也要写回 manifest
        _update_manifest_section(
            manifest_path,
            status="failed",
            output=pptx_path,
            error=f"{type(e).__name__}: {e}",
            started_at=started_at,
        )
        raise

    _update_manifest_section(
        manifest_path,
        status="succeeded",
        output=pptx_path,
        started_at=started_at,
    )
    print(f"已生成: {pptx_path}")
    if manifest_path is not None:
        print(f"已更新 run-manifest: {manifest_path}")


if __name__ == "__main__":
    main()
