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
        description="将单张图片 / 图片目录 / PDF 转为可编辑 PPT（需已 pip install -r requirements.txt）",
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="输入路径：单张图片（PNG/JPG 等）/ 图片目录（多张图按文件名排序）/ PDF 文件",
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
        default="Tencent Sans W3",
        help="正文字体名（默认 Tencent Sans W3）",
    )
    parser.add_argument(
        "--font-bold",
        default="Tencent Sans W7",
        help="标题/强调字体名（默认 Tencent Sans W7）",
    )
    parser.add_argument(
        "--ocr-engine",
        default="auto",
        choices=["auto", "tencent", "baidu"],
        help="OCR 引擎：auto(默认，优先腾讯)、tencent、baidu",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="不输出处理进度",
    )
    args = parser.parse_args()

    from src.pipeline import run_pipeline
    from src.extract.ocr import resolve_ocr_engine

    input_path = Path(args.input)
    if args.output:
        pptx_path = Path(args.output)
    else:
        # 单文件：直接改后缀；目录：在同一层目录下生成「目录名.pptx」
        pptx_path = (
            input_path.with_suffix(".pptx")
            if input_path.is_file()
            else input_path.with_suffix(".pptx")
        )

    selected_engine = resolve_ocr_engine(ocr_engine=args.ocr_engine)
    print(f"开始处理… OCR 引擎: {selected_engine}")
    run_pipeline(
        input_path,
        pptx_path,
        font_normal=args.font_normal,
        font_bold=args.font_bold,
        ocr_engine=args.ocr_engine,
        pdf_output_path=args.pdf_output,
        progress_callback=None if args.quiet else _progress_callback_with_bar(),
    )
    print(f"已生成: {pptx_path}")


if __name__ == "__main__":
    main()
