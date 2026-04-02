---
name: images-2-ppt
description: >-
  将图片或 PDF 转为可编辑 PowerPoint（.pptx）：云端 OCR（默认腾讯，兼容百度）提取文字与位置，设计语义去字得到干净底图，再按原位置放置可编辑文本框。在用户需要截图/幻灯片/PDF 转可编辑 PPT，或提到「图片转 PPT」「PDF 转 PPT」「可编辑幻灯片」「OCR 去字」「截图转 PPT」时使用。
---

# 图片 / PDF 转可编辑 PPT

## 何时使用

- 用户提供图片、多图目录或 PDF，要生成可编辑 .pptx
- 用户提到：图片转 PPT、PDF 转 PPT、截图转 PPT、可编辑幻灯片、OCR、去字

## 前置条件

- 工作区为**本仓库根目录**（含 `cli.py`、`src/`、`requirements.txt`）
- 运行环境与 Python 版本由使用者自行配置；依赖安装示例见下。

## 1. 依赖

在仓库根目录执行（具体用哪个 Python / 是否隔离环境由当前 Skill 宿主决定）：

```bash
pip install -r requirements.txt
```

`requirements.txt` 已指定国内镜像（清华）；境外可用官方源：`pip install -r requirements.txt --index-url https://pypi.org/simple`。

## 2. OCR 配置（至少一种）

默认引擎选择规则：

- 两种都配置：默认腾讯
- 仅配置一种：使用该引擎
- 都未配置：运行失败并提示配置缺失

仓库根目录：`cp .env.example .env`，至少填写以下任一组：

### 腾讯 OCR（推荐）

- `TENCENT_OCR_SECRET_ID`
- `TENCENT_OCR_SECRET_KEY`
- `TENCENT_OCR_REGION`（可选，默认 `ap-guangzhou`）

接口参考：[腾讯云通用文字识别（高精度版）](https://cloud.tencent.com/document/product/866/34937)

### 百度 OCR（可选）

- [百度智能云控制台](https://console.bce.baidu.com/ai/#/ai/ocr/overview/index) 创建应用并开通该接口（需实名，有免费额度）
- 填写：
   - `BAIDU_OCR_API_KEY`
   - `BAIDU_OCR_SECRET_KEY`

## 3. 运行

在仓库根目录执行：

| 输入 | 命令 |
|------|------|
| 单张图 | `python cli.py --input image.png --output out.pptx` |
| 图片目录 | `python cli.py --input images_dir --output out.pptx`（按文件名排序；会生成合并 PDF + 一个 pptx） |
| PDF | `python cli.py --input doc.pdf --output out.pptx` |

常用参数：

- `-i` / `-o`：输入、输出 .pptx
- `--font-normal`：正文（默认 Tencent Sans W3）
- `--font-bold`：强调（默认 Tencent Sans W7）
- `--ocr-engine`：`auto|tencent|baidu`（默认 `auto`，优先腾讯）
- `-q`：安静模式

示例：`python cli.py -i in.png -o out.pptx --font-normal "思源黑体" --font-bold "思源黑体 Bold"`

## 流水线（三步）

1. **OCR + 样式**：云端 OCR（默认腾讯，兼容百度），加粗/颜色/字号推断  
2. **去字**：设计语义分层重建（纯色/渐变重绘，复杂背景用近似色填充，无深度学习模型）  
3. **导出**：无字底图 + bbox 文本框 → .pptx（PPTXBuilder，见 banana-slides）

## 为人类开发者

更完整的说明见仓库根目录 **README.md**。
