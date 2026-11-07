# 更新日志

## 1.1.0 — 2026-11-07

**这一版修掉了「内核看起来完全没生效」的原因。**
反馈说把仓库交给十几种 agent（DSH、Hermes、openclaw……）都没成功。
原因是实测出来的，不是猜的。

### 根因一 —— 默认白名单写了一个不存在的 model id

默认白名单是 `deepseek-flash`。那是 **Hermes 内部的档位别名**，
**DeepSeek 公开 API 上根本没有这个 id** —— 公开的是 `deepseek-chat`
和 `deepseek-reasoner`。

于是所有真实 agent 的请求照样被路由到 DeepSeek（名字里含 `deepseek`），
但**从不注入内核**，也不抬输出下限。全程不报错：`/health` 是 200，
自检全绿，答案正常返回。行为跟什么都没装**一模一样**。

* 现在默认放行**所有 `deepseek*` 的 model id** —— `deepseek-chat`、
  `deepseek-reasoner`、`deepseek-v3.2` …… —— `deepseek-v4-pro` 仍然不放行
  （这是当初刻意保留的决定，现在依然保留）。
* 显式设白名单的行为不变：设了就收窄。
* 新增拒绝名单 `HERMES_JAILBREAK_DEEPSEEK_MODELS_DENY` /
  `JBP_DEEPSEEK_MODELS_DENY`。**拒绝名单优先于白名单。**

### 根因二 —— 代理继承了 Hermes 那边的白名单

`_env_file()` 优先读 `HERMES_HOME/.env`。所以**装了 Hermes 的机器上**，
给外部 agent 用的代理会**继承**
`HERMES_JAILBREAK_DEEPSEEK_MODELS=deepseek-flash`，问题原样复现。

现在代理和插件**各用各的变量**：

| 组件 | 白名单 | 拒绝名单 |
|---|---|---|
| Hermes 插件 | `HERMES_JAILBREAK_DEEPSEEK_MODELS` | `HERMES_JAILBREAK_DEEPSEEK_MODELS_DENY` |
| 外部 agent 用的代理 | `JBP_DEEPSEEK_MODELS` | `JBP_DEEPSEEK_MODELS_DENY` |

### 修复 —— `/jailbreak` 抛 NameError

`set_effort` / `effective_effort` 被 `/jailbreak` 命令和 `status()` 调用，
但**从来没定义过**，于是任何一次 `/jailbreak` 都死在
`NameError: name 'effective_effort' is not defined`。
DeepSeek 的 OpenAI 形状根本没有 effort 参数（思考档由 model id 决定，
真正起作用的是 32000 的输出下限），所以这两个函数现在是**带说明的空操作**：
只记录并回显，不改请求。

### 修复 —— 审计工具在插件缺失时自己先崩了

`tools/audit-deepseek-jailbreak.py` 会在插件没装的时候抛
`UnboundLocalError: cannot access local variable 'plug'` ——
也就是**最需要审计的时候审计自己先崩了**。现在改成干净的失败报告，
并且检查的是**真实公开 model id**，而不是那个别名。

### 修复 —— 自检从不发真实请求

旧自检只测注入函数，也从不检查**按 model 名路由** —— 所以上面那个缺陷完全隐形。
现在自检会对着本机桩上游跑**真实 HTTP 往返**（不联网、不需要 key），
覆盖三种协议形状，并断言：

* 能拿到响应（不是连接被断开）
* 内核到了上游，输出下限是 32000
* 鉴权头被转发
* 没 key 时返回 401，不是崩
* **`deepseek-chat` 和 `deepseek-reasoner` 确实拿到内核**
* 被拒绝的模型（`deepseek-v4-pro`）不拿内核

把白名单默认值改回去，其中 5 条断言立刻失败 —— 这个回归测试是有牙齿的。

`--self-test` 结尾现在打中英双写的 `ALL PASSED / 结果: 全部通过`，
**只在真通过时**才打印。

### 修复 —— 本机路径与缺失的安装脚本

* `watch-proxy.py`、`watch-proxy.cmd`、`start-proxy.cmd`、`start-proxy.vbs`
  不再写死 Python 路径，改为探测 `py.exe` / `python.exe` / `python3.exe`。
* `AGENTS.md` 和 `proxy/README.md` 不再包含本机路径。
* `AGENTS.md` 第 1 步里的 `https://github.com/<owner>/deepseek-jailbreak-kernel.git`
  是字面占位符，根本 clone 不了 —— 已换成真实 URL。
* `AGENTS.md` 检查点 3 写的是 `133 通过`，但插件只有 24 条断言 —— 跑对了反而像失败。
  现在门槛与实际一致。
* `install.ps1` 存为**带 BOM 的 UTF-8**：Windows PowerShell 5.1 否则按本地 ANSI
  代码页读 `.ps1`，中文字节被搞坏，解析器直接报 `ParserError`。
* `install.sh` 改为 POSIX 通用（Linux / macOS / WSL / Git Bash），
  并且不再因 `LOCALAPPDATA` 未定义而在 `set -u` 下崩掉。
* `install.sh` / `install.ps1` 会把插件装到
  `$HERMES_HOME/plugins/deepseek-jailbreak-core`，跑**两个**自测，
  任何一个没过就明确报错。
* 新增 `tests/proxy_smoke.py` —— 对**正在跑的**代理发真请求，
  把在用户看来长得一模一样的那几种故障区分开：连不上（没启动）、
  `RemoteDisconnected`（代理处理请求时崩了）、401（key 没到位）、
  5xx（上游/代理报错）。

### 验证

* `plugin/selftest.py` —— 24 项断言，0 失败。
* `proxy/proxy.py --self-test` —— 116 项检查，0 失败，含真实 HTTP 往返
  与 model 名路由断言。
* `tools/audit-deepseek-jailbreak.py` —— 13 项检查，0 失败；
  插件缺失时也不再崩溃。
* `install.sh` 在 Git Bash（Windows）与 WSL（Linux）下均验证通过。
* `install.ps1` 在 Windows PowerShell 5.1 与 PowerShell 7 下均可解析。

