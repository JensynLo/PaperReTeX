# PaperReTeX

将有原生文字层的学术 PDF 逆向为可编辑的 LaTeX 工程，并可进一步翻译为中文 PDF 与左右对照 PDF。逆向以输入 PDF 为依据；`samples/` 只是测试材料，不是论文模板。当前目标是保留正文结构和主要版式，尚不能保证恢复作者原始源码或像素级复刻。

## 工作流

1. 预检原生文字和页面，使用所选 CLI（Codex 或 Claude）判断单栏／双栏；也可选择本地几何预检。
2. BabelDOC 0.6.4 提取区域、字体与图表版面，保留图表的矢量资源。
3. MinerU 2.7.1 的 UniMERNet-small 仅对检测到的公式裁图做 OCR。所选 CLI 批量看图复核所有检测到的表达式；默认最多两轮，第二轮只处理不确定项。
4. 按原页、栏和跨栏区域组装普通的 `\section`、`equation`、段落、图表等 LaTeX 结构。`main.tex` 通过 `\input` 引用 `regions/*.tex`；`main-flat.tex` 供翻译使用。
5. 可选 XeLaTeX 编译与逐页视觉评测。待复核公式以 `[?]` 标记并记录 ID，不把 OCR 猜测伪装成已确认结果。

## 安装

需要 Python 3.9+、[uv](https://docs.astral.sh/uv/)、XeLaTeX，以及已登录的 [Codex CLI](https://developers.openai.com/codex/cli/) 或 [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code/overview)。主程序采用 `src/` 包布局；模型及独立环境保存在用户缓存，不进入仓库或发行包。

```sh
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
paperretex install-runtimes
paperretex doctor
```

BabelDOC 运行环境使用 Python 3.12，公式 OCR 环境使用 Python 3.11。`install-runtimes` 将它们放入 `~/.cache/paperretex/`（可用 `XDG_CACHE_HOME` 改变根目录）。首次使用可能下载模型。可通过 `--babeldoc-python` 与 `--ocr-python` 指定现有环境。

## 使用

```sh
paperretex convert paper.pdf --compile --evaluate -o output/paper
paperretex inspect paper.pdf -o output/layout.json
paperretex translate output/paper/main-flat.tex -o output/paper-zh
```

默认使用 Codex CLI。选择 Claude CLI 时，对布局预检、公式复核和翻译统一生效：

```sh
paperretex convert paper.pdf --llm-provider claude --compile --evaluate -o output/paper-claude
paperretex translate output/paper/main-flat.tex --llm-provider claude -o output/paper-zh-claude
```

`--llm-command /path/to/executable` 可指定 CLI 可执行文件；`--codex-command` 是兼容旧命令的别名。Claude 的图像复核通过受限的 Read 工具读取本次生成的裁图；Codex 使用 `--image`。两个适配器都要求结构化 JSON，程序会校验 ID、公式安全性及缓存身份。CLI 调用会向所选服务发送裁图、OCR 候选及相关正文；翻译会发送 TeX 文字片段。请自行配置相应 CLI 的账号与模型。

可用 `--codex-effort low|medium|high|xhigh|max|ultra` 为本次 Codex 调用指定推理强度，例如 `paperretex convert paper.pdf --codex-effort high -o output/paper`。该选项适用于 `convert`、`inspect` 和 `translate`；省略时沿用 Codex 自身配置。布局预检和公式复核会使用同一强度，翻译命令也使用指定强度。可用档位取决于所选 Codex 模型和客户端；较高强度通常需要更多时间和 token，不保证每个结果都更准确。更改强度会产生独立缓存；已有 hybrid 输出不能以不同强度 `--resume`。

`--layout-review local` 跳过 CLI 版式预检，但公式复核仍调用所选 CLI。`--pages 1-3,5` 只处理指定页。首次运行要求输出目录为空；中断后用相同参数加 `--resume`，失败的公式批次需要再加 `--retry-formulas` 才重试。`--formula-batch-size` 默认 36，最大 48；`--formula-review-rounds` 可设为 1 或 2。`--evaluate` 必须配合 `--compile`。

主要输出包括 `main.tex`、`main-flat.tex`、`regions/*.tex`、`main.pdf`、`report.json`、`formulas.json` 和各阶段审计日志。完整论文的评测在 `comparison/` 与 `pixel-comparison/`；`report.json` 记录页数、未确认公式、页面框缩放及视觉误差。翻译输出 `translated.pdf` 与 `bilingual.pdf`。

## 配置与目录

| 路径                        | 用途                                            |
| --------------------------- | ----------------------------------------------- |
| `src/paperretex/`           | CLI、布局、OCR、公式复核、LaTeX、翻译与评测模块 |
| `src/paperretex/llm_cli.py` | Codex／Claude 命令和结构化响应适配              |
| `scripts/`                  | 独立模型环境安装、样例评测与辅助工具            |
| `tests/`                    | 不调用真实模型的自动测试                        |
| `samples/`                  | 本地参考 PDF，默认不加入 Git                    |
| `output/`                   | 转换与评测结果，默认不加入 Git                  |

`pyproject.toml` 定义包、命令行入口与开发依赖；`.gitignore` 排除模型环境、缓存、输出和样例 PDF。运行测试：

```sh
python -m pytest -q
python -m compileall -q src/paperretex scripts
```

### 统一格式化

仓库的 `.vscode/settings.json` 将 Python 的保存格式器设为 Ruff，将 Markdown／JSON 设为 Prettier；`.editorconfig` 和 `pyproject.toml` 固定缩进、换行及 Ruff 规则。批量脚本只遍历 Git 管理及未被忽略的源码文件，使用 Ruff 0.17.0、Prettier 3.7.4、Taplo 0.9.3 和 shfmt。先安装 `uv`、Node.js／`npx`、`shfmt`（macOS 可用 `brew install shfmt`），然后运行：

```sh
python scripts/format_project.py
python scripts/format_project.py --check
python -m pytest -q
```

脚本处理 `.py`、`.md`、`.json`、`.toml` 和 `.sh`；PDF、依赖锁文件、`.gitignore` 与 `MANIFEST.in` 没有对应的代码格式器，不会改写。建议先用 `git diff` 审查批量格式化结果。

## 当前限制

- 默认 hybrid 模式要求未旋转、CropBox 与 MediaBox 相同且有原生文字层的 PDF；扫描件正文 OCR 尚未接入。
- 公式复核覆盖的是**检测到的**公式，不是全部真实公式的召回率；图表内部文字与公式仍保留在矢量资源中。
- 图表单元格暂不恢复为可编辑 `tabular`。参考文献主要保留为文本，交叉引用未完全恢复。
- 左右对照版由原文和译文 TeX 重排，不保证逐页与原 PDF 完全对齐。字体、断行、留白和复杂版式仍需人工检查。
- `--mode semantic`、`reflow`、`layout`、`source` 是可选的旧流程／对照流程；默认 `hybrid` 不检索论文源码。
