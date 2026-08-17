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

- **本机 / 客户端执行 `cli.py`**：对本地文件与 **http/https 直链下载**均**不设 4MB 硬性上限**（大文件仅受磁盘、内存与 OCR 接口限制）。
- **IM / 对话里传图**（把图片、整页截图、PDF 嵌进消息等）：宿主上下文有限，**建议单图约 ≤ 4MB**（或优先给**文件路径**、**可下载直链**、让用户**在本机跑本仓库 `cli.py`**），否则易出现**上下文溢出**或消息被拒。
- **PDF**：多页体积常更大；在对话中嵌入整份 PDF 尤其容易触顶——应改为路径、直链或客户端处理。

## 链接展示与下载（面向用户时的固定话术）

当需要把**可下载链接**（原图直链、网盘直链、临时下载地址等）发给用户时，**必须同时**提醒：

- 微信、QQ、部分 App **内置浏览器**可能拦截直链，页面显示类似「**站点正在维护中，请稍后重试**」**并不代表**链接失效。
- 请用户复制链接，用**系统默认浏览器**（Safari / Chrome / Edge 等）打开后再下载；必要时「在浏览器中打开」或「复制链接到浏览器」。

## 前置条件

- 在**本 Skill 仓库根目录**下操作（须能同时看到 `SKILL.md`、`cli.py`、`src/`、`requirements.txt`）。**禁止**在说明或脚本里写死某台机器上的绝对路径（例如 `~/.cursor/skills/...`、`/Users/xxx/...`）；不同宿主（Cursor、Codex、Code Buddy、Work Buddy 等）以**当前对话绑定的工作区 / 打开的文件夹**为仓库根即可。
- **可选（多目录、CI、或仓库被解压到非默认位置时）**：通过环境变量告诉运行时哪里是仓库、`.env` 在哪，无需改代码：
  - `IMAGES2PPT_ROOT`：本 Skill 仓库根目录（将从此目录读取 `.env`，除非同时用下一项覆盖）。
  - `IMAGES2PPT_ENV_FILE`：直接指向某个 `.env` 文件的完整路径（优先级最高，用于密钥与仓库分离等场景）。
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
- **禁止**在其它目录或工作区猜测是否存在 `.env`；以**本 Skill 仓库根目录**下的 `.env` 为准，或用户已设置的 `IMAGES2PPT_ENV_FILE` / `IMAGES2PPT_ROOT` 所解析出的路径为准。

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
| 图片目录 | `python cli.py --input images_dir --output out.pptx`（按**自然排序（数字感知）**；会生成合并 PDF + 一个 pptx；**先读下面「图片目录命名与页序守卫」**） |
| PDF | `python cli.py --input doc.pdf --output out.pptx` |
| **图片或 PDF 直链** | `python cli.py --input "http://example.com/slide.png" --output out.pptx`（`http` / `https` 均可；自动下载；须为**直接文件 URL**，不能是需登录的 HTML 预览页） |

`-i` 为 URL 且未指定 `-o` 时，默认输出名为 URL 路径中的文件名（stem）+ `.pptx`，若无有效文件名则用 `remote_input.pptx`（生成在当前工作目录）。

### 图片目录命名与页序守卫（多页转换必读）

目录输入时**页序 = 文件名的自然排序**（`slide-2` 排在 `slide-10` 前）。页序错乱是目录转换最常见、代价最高的事故（OCR 额度烧完才发现），因此：

- **命名规范**：目录内图片必须统一为**同一前缀 + 连续数字编号**（推荐 `slide-001.png … slide-014.png`）。**手工补图 / 从其他工具保存的图，必须先改名纳入该编号体系，禁止以原名混放**（如 `ChatGPT Image xxx.png` 混进 `slide-XXX.png` 会全部排在前面，页序整体错乱）。
- **转换前先 dry-run**：`python cli.py -i images_dir --dry-run`。它会打印「页码 ← 文件名」映射并做命名健康检查（混合命名 / 多种编号前缀 / 编号缺口 / 编号重复），**不调 OCR、不需要密钥**。
- **页数门禁**：知道预期页数时加 `--expected-pages N`（如大纲 14 页就传 14）；页数不符会以退出码 `3` 在 OCR 之前失败。
- 正式转换时，加载阶段也会打印页序映射与命名警告；请确认映射正确后再让其进入 OCR。

常用参数：

- `-i` / `-o`：输入、输出 .pptx
- `--dry-run`：只列页序映射 + 命名检查，不调 OCR、不生成文件
- `--expected-pages N`：期望页数校验，不符退出码 `3`（在 OCR 之前失败）
- `--no-qa-report`：关闭 QA 报告输出（默认在输出旁写 `<output>.qa.json`，含页序映射、每页文本块统计、低置信文本清单）
- `--font-normal` / `--font-bold`：西文（latin）正文 / 强调字体；不指定时按 **OCR 识别出的内容语言**自适应（中文内容→`腾讯字体 W3/W7`，英文内容→`TencentSans W3/W7`），不受运行 shell 的 locale 影响
- `--font-ea-normal` / `--font-ea-bold`：东亚（中文）正文 / 强调字体（自适应规则同上），Windows/中文 WPS 下决定中文实际显示字体
- `--text-lang` / `--text-alt-lang`：文本 run 的主/副语言标签（默认 `zh-CN` / `en-US`），避免英文环境下中文被打上拼写检查的红色波浪线
- `--text-pad-ratio`：文本框向右扩宽比例（默认 `0.08`，防止贴边折行；`0` 关闭扩宽）
- `--slide-size-mode`：`auto`（默认，按输入中占比最高的画幅建立统一画布）、`widescreen`（固定 16:9）或 `native`（单页严格匹配输入尺寸）。QA 会标出因混合画幅而保留留白的页面。
- `--no-merge-textbox`：关闭同行短文本框合并（默认开启合并，能把被 OCR 切碎的同行文字拼回一个文本框）
- `--ocr-engine`：`auto|tencent|baidu`（默认 `auto`，优先腾讯）
- `-q`：安静模式

示例：`python cli.py -i in.png -o out.pptx --font-normal "思源黑体" --font-bold "思源黑体 Bold" --font-ea-normal "思源黑体" --font-ea-bold "思源黑体 Bold"`

## 流水线（三步）

1. **OCR + 样式**：云端 OCR（默认腾讯，兼容百度），加粗/颜色/字号推断  
2. **去字**：设计语义分层重建（纯色/渐变重绘，复杂背景用近似色填充，无深度学习模型）  
3. **导出**：无字底图 + bbox 文本框 → .pptx（PPTXBuilder，见 banana-slides）

## 转换结果自检（QA 报告）

每次转换默认在输出旁写 `<output>.qa.json`，终端同时打印 QA 摘要。报告包含：

- `page_mapping`：页码 ↔ 来源文件名（核对页序是否符合预期）；
- `naming_warnings`：命名健康检查结果（混合命名 / 编号缺口 / 编号重复）；
- `pages[].text_blocks / rendered_blocks / kept_in_background_blocks`：每页文本块总数、转为可编辑文本框的数量、保留在背景图的数量（列表标号 / 图标字符等）；
- `pages[].low_confidence_blocks`：OCR 置信度 < 0.85 的文本清单——**这些是最可能识别错误的文本，交付前应优先人工核对**（如错字、乱码、logo 内文字被误识别）。
- `pages[].fit`：源图尺寸、缩放比例与等比留白；`letterboxed=true` 表示该页画幅与整套 PPT 画布不同。

退出码约定：`0` 成功；`2` 配置/输入错误（含 OCR 密钥未配置）；`3` 页数校验失败或输入内容类错误（在 OCR 之前失败，不消耗调用额度）。

## 为人类开发者

更完整的说明见仓库根目录 **README.md**。
