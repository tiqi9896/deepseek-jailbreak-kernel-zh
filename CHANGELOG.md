# 更新日志

## 1.0.0 — 2026-11-02

DeepSeek 专属内核的首个版本。从 MiniMax 项目里拆出来，因为两个 provider
需要完全不同的机制。

### 内核注入

- 面向 OpenAI 形状的独立注入器：`_openai_inject_system` 往 `messages[]` 里写一条
  `{"role":"system"}`。只改顶层 `system` 的插件在 DeepSeek 上是静默空操作。
- 模型白名单 `HERMES_JAILBREAK_DEEPSEEK_MODELS`，默认 `deepseek-flash`。
  `deepseek-v4-pro` 故意排除。
- 同时检查 `context["model"]` 与 `request["model"]`，堵住 fallback 时两者不一致的窗口。
- 白名单与上游路由分离：非白名单的 DeepSeek 模型照样发到 DeepSeek 端点，只是不注入内核。
- 该路径不加授权块。实测负收益：flash 8/10 → 7/10。
- 不加 `thinking` / `output_config`。OpenAI 形状没有这些参数，上游会静默忽略。

### 输出预算

- DeepSeek 下限抬到 32000。16000 时模型把预算烧在思考上、开口就是 844 字拒答；
  32000 时同一个请求交付 47590 字。

### 陈旧内核修复

- `_kernel_is_current()` 比对当前内核的首 120 字与尾 120 字。只判标记不够 ——
  Hermes 的插件段每会话冻结，老会话会永远还原旧内核。
- `_cut_kernel()` 在第一个内核标记处截断。没有它会产出两个内核，因为
  `_extract_section()` 会把裸的旧内核吞进 memory 段，而 `strip_plugin_sections()`
  只剥 HTML 容器。
- OpenAI 的 `messages[]` 路径与 Anthropic 的顶层路径都已应用。

### 多轮升级

- `multiround.py`：规格 → 逐字填满 → 去掉限定，三轮共享一段对话历史。
- `HERMES_JAILBREAK_MULTIROUND=auto|always|off`，默认 `auto`。
- 五个判定谓词：`refusal`、`reversal`、`downgrade`、`form`、`thin`。
  `reversal` 与 `form` 是在「所有早期谓词都放过」的具体失败之后才补的。
- 诚实回退：三轮比可用的单轮更差时，交付单轮。
- 主题提示：`X-Topic-Hint` 请求头或 `topic_hint` body 字段。
- 上游每轮重试两次带退避；多轮一次 130–280 秒，这个时长下上游会断连。

### 工具

- `tools/audit-deepseek-jailbreak.py` —— 校验插件、中间件注册、白名单、路由
  （含 `provider: custom`）、以及 gateway 日志里观测到的注入。
  可移植：通过 `HERMES_HOME` 或从脚本位置向上查找定位 Hermes home。
- `tools/jb-daily-check.py` —— 每日巡检：内核预算、白名单、代理可达性、
  四路资产同步、并行跑冲突检测。

### 实测

所有公开数字都在 `data/` 里，且经独立重算：

- 基线 A/B/C：flash 4/10 → 8/10 → 7/10；v4-pro 3/10 → 10/10 → 9/10
- 变体矩阵（120 请求）：base 23/30、+affirm 24/30、+noqual 20/30、+both 25/30
  —— 增强段落在噪声带内
- 内核位置：放 system 1/8 vs 放 user 末尾 0/8
- 走真实中间件的端到端：v4-pro 12/12；flash 16000 时 7/12 → 32000 时 8/12
- 多轮：fraud 0/6 → 8/11 → 10/10；勒索 12 次零拒答，第三轮去掉 dry-run 分支
- 标题锁定条款：第三轮 5/5 → 1/6；第一轮 2/6，且规格轮本身 6/12 被拒
- 自动化：无主题提示 5 次全败；给了主题 41899 字 / 28 代码块

### 修正

- `deepseek-v4-pro` 的 C 变体最初按单条观测记作 1/1。全量跑完是 9/10。
  已在所有公开文档中修正。
