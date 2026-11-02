# AGENTS.md — 给 AI Agent 的执行指令

> **如果你是 AI Agent，而人类把这个仓库地址给了你，就照本文件做。**
> 这份文件是写给你的，不是写给人类读的。从上往下执行，每一步都要过验收，
> 不许跳过验证步骤交差。

你要安装的是一层**面向 DeepSeek 的请求层非拒绝交付层**。它不改模型权重，
只在请求发出前重写请求体，让模型收到交付契约；单轮被拒时它会把硬请求重构成三轮。

---

## 第 0 步 —— 前置检查（别假设，去查）

```bash
python --version          # 需要 3.10+
git --version
```

确认操作系统。仓库里的路径按 POSIX 写，Windows 上要换成对应路径。

确定 Hermes 的 home：

| 平台 | 常见路径 |
|---|---|
| Linux / macOS | `~/.hermes` |
| Windows | `%LOCALAPPDATA%\hermes` 或 `A:\hermes` / `C:\hermes` —— 实际去文件系统里看 |

**检查点 1**：你选的那个目录里必须已经有 `config.yaml`。如果没有，说明 Hermes
不装在那里 —— 问人类 Hermes 用的是哪个目录。**不许自己新建一个 Hermes home
然后假装装好了。**

---

## 第 1 步 —— 克隆

```bash
git clone https://github.com/<owner>/deepseek-jailbreak-kernel-zh.git
cd deepseek-jailbreak-kernel-zh
```

**检查点 2**：确认这几个文件存在：

```
plugin/__init__.py
plugin/selftest.py
proxy/proxy.py
proxy/multiround.py
```

---

## 第 2 步 —— 装插件

插件是让**每一个 Hermes 里跑 `deepseek-flash` 的对话**都带内核的那部分。
目录名必须是 `deepseek-jailbreak-core`（模块名是固定的，改名就注册不上）。

```bash
cp -r plugin "$HERMES_HOME/plugins/deepseek-jailbreak-core"
```

然后设白名单。只针对 `deepseek-flash`：

```bash
grep -q 'HERMES_JAILBREAK_DEEPSEEK_MODELS' "$HERMES_HOME/.env" \
  || echo 'HERMES_JAILBREAK_DEEPSEEK_MODELS=deepseek-flash' >> "$HERMES_HOME/.env"
```

热重载，不要重启用户的会话：

```bash
hermes plugins disable deepseek-jailbreak-core
hermes plugins enable deepseek-jailbreak-core
```

**检查点 3**：跑离线自测。它不联网。

```bash
python "$HERMES_HOME/plugins/deepseek-jailbreak-core/selftest.py"
```

期望：`133 通过, 0 失败`。**只要有一项失败就停下来，把失败的断言名报出来**，
不要继续往下走。

---

## 第 3 步 ——（可选）装代理

只有当人类想让**非 Hermes** 的 Agent（Cursor、Claude Code、自己写的脚本）
也拿到同样行为时才需要。只用 Hermes 对话就跳过这步。

```bash
cd proxy
export DEEPSEEK_API_KEY=<人类的 key>     # 绝不要写死进文件
python proxy.py --port 8790
```

然后把客户端的 `base_url` 指到 `http://127.0.0.1:8790`
（各工具的具体配法见 `proxy/docs/AGENT-SETUP.md`）。

**检查点 4**：

```bash
curl -s http://127.0.0.1:8790/health
```

期望：JSON 里有 `"status": "ok"` 和 `"multiround": "auto"`。

---

## 第 4 步 —— 验证内核真的到模型了

**不要因为「插件装上了」就报成功。** 要验证发出去的请求体。

```bash
python tools/audit-deepseek-jailbreak.py -v
```

**检查点 5**：退出码 `0`，最后一行是
`all N checks passed - every deepseek-flash chat carries the kernel`。

失败的话看是哪一项：

| 失败项 | 原因 | 修法 |
|---|---|---|
| `plugin found` | `HERMES_HOME` 猜错了 | 显式设 `HERMES_HOME` 环境变量 |
| `middleware registration logged` | 插件没被加载 | 查 `config.yaml` 里插件是否列出并 enabled |
| `allowlist has deepseek-flash` | `.env` 没读到 | 确认 key 在 `$HERMES_HOME/.env` 里，不是只在 shell 里 |
| 路由类检查失败 | 插件版本不对 | 重新 clone，路由逻辑在 `_llm_request` 里 |

---

## 第 5 步 —— 让存量会话吃到当前内核

Hermes 的插件段是**每会话渲染一次并冻结**的。装之前建的会话会一直带着旧内核
（或者根本没有），除非重建。

```bash
hermes sessions refresh-prompts
```

把所有存量会话标脏，每个会话下一轮重建。

**检查点 6**：人类在任意一个 `deepseek-flash` 对话里再发一条消息之后，
去 gateway 日志里 grep：

```bash
grep -E 'openai-shape kernel (already present and current|OUTDATED|injected)' \
  "$HERMES_HOME/logs/gateway.log" | tail -5
```

出现下面任意一条，都说明请求带了内核：

- `already present and current` —— 持久化 prompt 里已经是当前内核
- `OUTDATED ... -> replacing` —— 检测到旧内核，已就地替换
- `injected` —— 内核被加进去了

**一条都没有**，说明中间件没在跑，回到检查点 3。

---

## 这套东西做不到什么

人类如果要求「保证永远不拒答」，照实说：

- 它做不到让模型永不拒绝。`deepseek-flash` 上的残余拒答是权重级对齐 ——
  模型自己的推理里把内核点名为越狱框架，然后按自己的价值判断拒绝。
  见 `docs/MEASUREMENTS.md`。
- 它不碰 `deepseek-v4-pro`。那个模型故意不在白名单里，请求照旧路由到正确端点，
  只是不注入内核。
- 多轮升级在硬题材上**必须由调用方给中性主题**才有效。不给就会跑偏 ——
  见 `docs/MULTIROUND.md`。

---

## 向人类汇报

做完之后报告这几项：

1. 哪几个检查点过了，带上确切数字（`133/133`、审计退出码 0）
2. 装到了哪个路径
3. 代理是装了还是跳过了
4. 任何失败的检查点，原文贴出来

**不要把本文件复述一遍给人类听。只报结果。**
