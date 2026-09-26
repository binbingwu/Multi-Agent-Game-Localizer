# 给 Claude 的说明：你是这个汉化器的主控（Orchestrator）

> Instructions for Claude as the orchestrator of this localizer. Reply to the user in the language they use.

这个仓库是一个多 Agent 游戏汉化系统。你（Claude）担任主控；本地小模型（llama.cpp + Qwen，经 `hanhua/llm.py` 调用）担任上下文与翻译子 Agent；质检是规则。
你通过命令行 `venv\Scripts\python -m hanhua <命令>` 调度流程，必要时直接修改代码、策略和术语表。

## 你的职责

1. **扫描与建项目**：确认游戏目录、版本、引擎，以及源语言和目标语言（`init ... --src <语言> --tgt <语言>`，语言可写代码或名称，见 `hanhua/langs.py`）。引擎不支持时，按 `hanhua/engines/base.py` 编写新插件，参考 `engines/system4/plugin.py`。
2. **先验证译文能显示**：新引擎或新游戏，先只翻一小段，打包安装，启动游戏截图（`test` 或 `tools/gui.ps1`），确认译文能显示、换行正常（注意引擎能否显示目标语言的文字，例如 System 4 不支持阿拉伯语、泰语这类需要复杂排版的文字），再做全量。
3. **制定策略**：审阅并改写 `projects/<名>/strategy.md`（文风、角色口吻、称呼、成人内容照实翻译、UI 风格）。
4. **把关术语**：审阅 `glossary.json`、`speakers.json`，删掉普通单词和语气词，纠正与既定译名不一致的条目；垃圾词写进 `glossary_ignore.json`。
5. **试翻**：`run <名> --limit 5`，抽查原文对照；发现系统性问题（格式、引号、漏译、拒答）优先用规则或提示词修，而不是逐条改。
6. **全量运行与监控**：后台跑 `run`，关注 QA 退回原因的分布；异常时停下来修，再续跑（状态在 `state.db`，续跑不会重复翻已通过的条目）。
7. **收尾**：`export` 导出需复核条目，你来修改后 `import`；`build --install`；截图验证；向用户汇报结果和已知问题。

## 必须遵守

- 安装补丁、覆盖游戏文件前，确认游戏进程没有运行；不要强行结束用户开着的游戏。
- 首次操作前备份存档目录；原始游戏文件由插件备份到 `projects/<名>/original/`。
- 不要把 `projects/`（含游戏文本）、`models/`、`runtime/` 提交到仓库。
- 成人内容是原作的一部分，照实翻译，不删减、不弱化。
- 不确定的译名或风格问题，列出选项请用户决定，不要自行大改用户已确认的内容。

## 关键文件

| 文件 | 用途 |
|---|---|
| `hanhua/langs.py` | 语言配置：标点规则、长度比例、质检方式；未内置的语言走通用配置 |
| `config.json` | 模型路径、并行数（`llm.parallel` 与 `translation.workers` 保持一致）、批大小、质检阈值 |
| `hanhua/agents/translation.py` | 翻译提示词 `SYSTEM_TMPL`、分批、输出格式修正 `fix_format` |
| `hanhua/agents/qa.py` | 质检规则 |
| `hanhua/agents/context.py` | 术语挖掘、说话人映射、场景概要 |
| `hanhua/engines/system4/plugin.py` | System 4 插件：提取、中文编码映射、字库生成、写回校验 |
