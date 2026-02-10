#!/usr/bin/env python3
"""入口：--input image.png 或 file.pdf --output out.pptx"""
import argparse
import sys
from pathlib import Path

# 保证从项目根运行时可找到 src
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


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
        description="将单张图片或 PDF 转为可编辑 PPT（需先 conda activate LLMs）",
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="输入路径：单张图片（PNG/JPG 等）或 PDF 文件",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="输出的 .pptx 路径",
    )
    parser.add_argument(
        "--font-normal",
        default="Tencent Sans W3",
        help="正文字体名（默认 Tencent Sans W3）",
    )
    parser.add_argument(
        "--font-bold",
        default="Tencent Sans W7",
        help="标题/强调字体名（默认 Tencent Sans W7）",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="不输出处理进度",
    )
    args = parser.parse_args()

    from src.pipeline import run_pipeline

    print("开始处理…")
    run_pipeline(
        args.input,
        args.output,
        font_normal=args.font_normal,
        font_bold=args.font_bold,
        progress_callback=None if args.quiet else _progress_callback_with_bar(),
    )
    print(f"已生成: {args.output}")


if __name__ == "__main__":
    main()
