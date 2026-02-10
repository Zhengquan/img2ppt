# 图片 / PDF 转 PPT

将单张图片或 PDF 转为可编辑的 PowerPoint（.pptx）：先提取文字与样式，再做去字修补，最后在无字底图上按原位置与样式还原文字。

## 环境与构建

所有依赖安装、构建、运行均在 Conda 环境 **LLMs** 中进行。

```bash
conda activate LLMs
pip install -r requirements.txt
```

## 配置百度 OCR（必填）

**OCR 已完全采用云端方案**：使用百度智能云「通用文字识别（高精度含位置版）」，不再依赖本地 PaddleOCR。

1. 在[百度智能云控制台](https://console.bce.baidu.com/ai/#/ai/ocr/overview/index)创建应用，开通 **「通用文字识别（高精度含位置版）」**（需实名认证，有免费额度）。
2. 在项目根目录复制环境变量并填写 Key：
   ```bash
   cp .env.example .env
   ```
   编辑 `.env`，填入：
   - `BAIDU_OCR_API_KEY`：应用的 API Key  
   - `BAIDU_OCR_SECRET_KEY`：应用的 Secret Key  

未配置或 Key 错误时，运行 `cli.py` 会提示缺少配置或调用失败。

## 关于 Big LaMa（可选）

去字步骤使用「设计语义分层重建」：纯色、渐变区域直接填充，**复杂纹理**在默认配置下用近似色填充，**不会加载 big-lama**，因此不会出现 `Loading model: lama` 或下载 `big-lama.pt`。

若希望对复杂纹理使用 LaMa 修补（需安装 iopaint/torch 并下载 big-lama），在 `.env` 中设置：

```bash
USE_INPAINTING_FALLBACK=1
```

## 使用方式

在项目根目录、且已 `conda activate LLMs` 的前提下：

- **单张图片**：`python cli.py --input image.png --output out.pptx`
- **PDF 文件**：`python cli.py --input doc.pdf --output out.pptx`（每页 PDF 对应一页幻灯片）
- **指定字体**：`python cli.py -i in.png -o out.pptx --font-normal "思源黑体" --font-bold "思源黑体 Bold"`

## 输入 / 输出

- **输入**：单张图片（PNG/JPG 等）或 PDF 文件
- **输出**：仅 PPT 格式（.pptx），每张图或每页 PDF 对应一页幻灯片

## 流水线概要

1. 文字与样式提取（**百度云端高精度 OCR** + 加粗/颜色/字号推断）
2. 去文字化（**设计语义分层重建**：纯色/渐变直接填充，复杂纹理用近似色填充；默认**不加载 big-lama**）
3. 使用 **banana-slides** 的 [PPTXBuilder](https://github.com/Anionex/banana-slides) 在无字底图上按 bbox 放置可编辑文本框并导出 .pptx

字体默认使用 Tencent Sans W3（正文）/ Tencent Sans W7（标题/强调）；若系统未安装，可经配置改为其他字体（如思源黑体）。

## 与 banana-slides 的复用

- **`src/utils/pptx_builder.py`**：复用自 banana-slides 的 PPTXBuilder，用于按像素 bbox 创建可编辑文本框、字号自适应、对齐与颜色样式。
- **`src/export/ppt.py`**：`build_editable_pptx()` 将本项目的「干净底图 + styled_blocks」转为 PPTXBuilder 所需格式并生成可编辑 PPT；保留 `add_slide_from_image_and_blocks()` 作为备用。
