---
name: images-2-ppt
description: >-
  将图片或 PDF 转为可编辑 PowerPoint（.pptx）：云端 OCR（默认腾讯，兼容百度）提取文字与位置，设计语义去字得到干净底图，再按原位置放置可编辑文本框。在用户需要截图/幻灯片/PDF 转可编辑 PPT，或提到「图片转 PPT」「PDF 转 PPT」「可编辑幻灯片」「OCR 去字」「截图转 PPT」时使用。
---

# 图片 / PDF 转可编辑 PPT

## 何时使用

- 用户提供图片、多图目录或 PDF，要生成可编辑 .pptx
- 用户提供**图片或 PDF 的直链**（`http://` 与 `https://` 均可，非网页文章页），要生成可编辑 .pptx
- 用户提到：图片转 PPT、PDF 转 PPT、截图转 PPT、可编辑幻灯片、OCR、去字

## 图片大小与上下文（必读）

- **单张图片**（含 URL 下载下来的整段内容）建议并**默认按 ≤ 4MB** 处理：`cli.py` 对 **http/https 直链下载**与**本地单图**会拒绝超过 **4MB** 的文件，避免 OCR/对话**上下文溢出**报错。
- **PDF** 未按 4MB 硬截断（多页 PDF 常更大）；若在对话里嵌入整份 PDF 或超大图，仍可能触达宿主上下文上限——应改为只传链接、压缩或分页处理。
- 向用户说明：若原图超过 4MB，需先压缩、降分辨率或裁剪后再传路径/链接。

## 链接展示与下载（面向用户时的固定话术）

当需要把**可下载链接**（原图直链、网盘直链、临时下载地址等）发给用户时，**必须同时**提醒：

- 微信、QQ、部分 App **内置浏览器**可能拦截直链，页面显示类似「**站点正在维护中，请稍后重试**」**并不代表**链接失效。
- 请用户复制链接，用**系统默认浏览器**（Safari / Chrome / Edge 等）打开后再下载；必要时「在浏览器中打开」或「复制链接到浏览器」。

## 前置条件

- 工作区为**本仓库根目录**（含 `cli.py`、`src/`、`requirements.txt`）
- 运行环境与 Python 版本由使用者自行配置；依赖安装示例见下。

## 0. 硬性规则（Agent 必读）

本 Skill **依赖云端 OCR 密钥**。在未确认密钥有效之前，**禁止**执行 `python cli.py …`（包括「先跑一遍看报什么错」）。

### 必须先完成的步骤（顺序固定）

1. **检查**仓库根目录是否存在 `.env`，且其中**至少一组** OCR 变量为**真实值**（非空、非 `.env.example` 里的占位符如 `your-api-key` / `your-secret-id` 等）。
2. 若 `.env` 不存在、为空、或仍为占位符：**停止自动化执行**，向**用户**说明需要腾讯或百度 OCR 凭据，请用户在本 Skill 根目录执行 `cp .env.example .env` 后自行填入，或由用户在对话中提供密钥后由 Agent **仅写入工作区内的 `.env`**（勿写入聊天记录以外的公开位置；勿提交 `.env`）。
3. **仅在**步骤 1 通过后，再执行 `pip install -r requirements.txt`（若尚未安装）。
4. **仅在**步骤 1 通过后，再执行 `python cli.py -i … -o …`。

### 禁止行为

- **禁止**在未配置有效密钥时运行 `python cli.py`，以试探错误信息。
- **禁止**在未配置有效密钥时，用「把图片直接插进 PPT」等方式替代本流水线并声称完成了本 Skill 的「可编辑 OCR 还原」能力（除非用户明确只要嵌入图片）。
- **禁止**在其它目录或工作区猜测是否存在 `.env`；以**本 Skill 仓库根目录**的 `.env` 为准。

### 如何自检「已配置」

- 根目录存在 `.env`。
- 下列**至少一组**两个变量均为非占位非空字符串：
  - 腾讯：`TENCENT_OCR_SECRET_ID` + `TENCENT_OCR_SECRET_KEY`
  - 百度：`BAIDU_OCR_API_KEY` + `BAIDU_OCR_SECRET_KEY`
- 若用户同时配置了腾讯与百度，未传 `--ocr-engine` 时默认使用腾讯。

若自检不通过，`cli.py` 会以退出码 `2` 退出并打印配置说明；Agent 仍应先按上文向用户索取密钥，而不是反复空跑命令。

## 1. 依赖

在仓库根目录执行（具体用哪个 Python / 是否隔离环境由当前 Skill 宿主决定）：

```bash
pip install -r requirements.txt
```

`requirements.txt` 已指定国内镜像（清华）；境外可用官方源：`pip install -r requirements.txt --index-url https://pypi.org/simple`。

**注意**：安装依赖不要求密钥，但**安装后仍不得在密钥未就绪时运行 `cli.py`**。

## 2. OCR 配置（至少一种）

默认引擎选择规则：

- 两种都配置：默认腾讯
- 仅配置一种：使用该引擎
- 都未配置或仍为占位符：`cli.py` 立即退出（退出码 `2`），须先配置

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
| **图片或 PDF 直链** | `python cli.py --input "http://example.com/slide.png" --output out.pptx`（`http` / `https` 均可；自动下载；须为**直接文件 URL**，不能是需登录的 HTML 预览页；下载体 ≤4MB） |

`-i` 为 URL 且未指定 `-o` 时，默认输出名为 URL 路径中的文件名（stem）+ `.pptx`，若无有效文件名则用 `remote_input.pptx`（生成在当前工作目录）。

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
