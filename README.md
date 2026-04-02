# 图片 / PDF 转 PPT

将单张图片或 PDF 转为可编辑的 PowerPoint（.pptx）：先提取文字与样式，再做去字修补，最后在无字底图上按原位置与样式还原文字。

## 仓库结构（极简）

| 路径 | 作用 |
|------|------|
| **SKILL.md** | 给 Agent 用：何时触发、配置 `.env`、运行 `python cli.py …`（对外 Skill 入口） |
| **README.md** | 本说明：环境、OCR 配置、使用方式、流水线 |
| **.env.example** | 腾讯/百度 OCR 环境变量模板 |
| **pip.conf.example** | 国内 pip 镜像配置示例 |
| **cli.py** | 命令行入口 |
| **src/** | 加载输入 → OCR/样式 → 去字 → 导出 pptx |

克隆本仓库后，根目录 **SKILL.md** 即为 Skill 唯一说明；无需 `.cursor/skills` 等额外目录。

## 环境与构建

依赖见 `requirements.txt`。在仓库根目录安装后即可运行；Python 版本与虚拟环境由你的运行环境（本机、CI、Agent 沙箱等）自行决定。

```bash
pip install -r requirements.txt
```

`requirements.txt` **已内置**清华源（`--index-url` / `--trusted-host`），直接执行上式即走国内镜像。若在境外或需官方源，可：

```bash
pip install -r requirements.txt --index-url https://pypi.org/simple
```

或编辑 `requirements.txt` 删掉文件最上方两行镜像配置。其他镜像（阿里云等）可自行替换那两行里的 URL；本机全局配置仍可用 [pip.conf.example](pip.conf.example)。

## 配置 OCR（至少一种）

**OCR 已完全采用云端方案**：支持腾讯云与百度云，不再依赖本地 PaddleOCR。

- 若腾讯与百度都配置：默认使用腾讯
- 若仅配置一种：使用该引擎
- 若都未配置，或 `.env` 中仍为 `.env.example` 里的占位符（如 `your-api-key`）：`cli.py` 会立即退出（退出码 `2`）并提示先配置真实密钥

### 方案 A：腾讯云（推荐）

1. 在[腾讯云 OCR 文档](https://cloud.tencent.com/document/product/866/34937)对应产品中开通 **「通用文字识别（高精度版）」**。
2. 在项目根目录复制环境变量并填写：
   ```bash
   cp .env.example .env
   ```
3. 编辑 `.env`，填入：
   - `TENCENT_OCR_SECRET_ID`
   - `TENCENT_OCR_SECRET_KEY`
   - `TENCENT_OCR_REGION`（可选，默认 `ap-guangzhou`）

### 方案 B：百度智能云（可选）

1. 在[百度智能云控制台](https://console.bce.baidu.com/ai/#/ai/ocr/overview/index)创建应用，开通 **「通用文字识别（高精度含位置版）」**（需实名认证，有免费额度）。
2. 在 `.env` 填入：
   - `BAIDU_OCR_API_KEY`：应用的 API Key
   - `BAIDU_OCR_SECRET_KEY`：应用的 Secret Key

也可通过 `--ocr-engine` 显式指定引擎：`auto|tencent|baidu`（默认 `auto`）。

## 使用方式

在项目根目录、已安装依赖的前提下：

- **单张图片**：`python cli.py --input image.png --output out.pptx`
- **图片目录**：`python cli.py --input images_dir --output out.pptx`（按文件名排序；会自动合并生成 `out.pdf`，并输出一个 `out.pptx`）
- **PDF 文件**：`python cli.py --input doc.pdf --output out.pptx`（每页 PDF 对应一页幻灯片）
- **指定字体**：`python cli.py -i in.png -o out.pptx --font-normal "思源黑体" --font-bold "思源黑体 Bold"`
- **指定 OCR 引擎**：`python cli.py -i in.png -o out.pptx --ocr-engine tencent`

## 输入 / 输出

- **输入**：单张图片（PNG/JPG 等）/ **包含多张图片的目录** / PDF 文件
- **输出**：仅 PPT 格式（.pptx），每张图或每页 PDF 对应一页幻灯片

## 流水线概要

1. 文字与样式提取（**云端 OCR：默认腾讯，兼容百度** + 加粗/颜色/字号推断）
2. 去文字化（**设计语义分层重建**：纯色/渐变重绘，复杂纹理用近似色填充，不加载任何 inpainting 模型）
3. 使用 **banana-slides** 的 [PPTXBuilder](https://github.com/Anionex/banana-slides) 在无字底图上按 bbox 放置可编辑文本框并导出 .pptx

字体默认使用 Tencent Sans W3（正文）/ Tencent Sans W7（标题/强调）；若系统未安装，可经配置改为其他字体（如思源黑体）。

## 与 banana-slides 的复用

- **`src/utils/pptx_builder.py`**：复用自 banana-slides 的 PPTXBuilder，用于按像素 bbox 创建可编辑文本框、字号自适应、对齐与颜色样式。
- **`src/export/ppt.py`**：`build_editable_pptx()` 将本项目的「干净底图 + styled_blocks」转为 PPTXBuilder 所需格式并生成可编辑 PPT；保留 `add_slide_from_image_and_blocks()` 作为备用。

## 对外发布（Skill）

- 公开本仓库即可；他人克隆后在 Cursor 等环境中打开仓库，Agent 读取根目录 **SKILL.md** 即可按流程协助转换。
- `.cursor/` 已加入 `.gitignore`，本地 Cursor 配置不会入库。
