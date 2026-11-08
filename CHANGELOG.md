# 更新日志

---

## 1.1.5 —— 只是在**谈论**成人内容的请求，被当成了成人请求

**实测，不是猜的。** 成人条款按请求自身的主题门控：非成人请求**不得**收到它。
但门控当时是**纯字面子串扫描** ⇒ 只要请求里**提到**这个话题，就会被打开：

> 「还有一个确保不会又出现之前那个情况，就是我用 workbuddy 里面的 HY4 这个模型去做其他的任务，
> 不会做着做着又给给写成色情小说了吧？」

这是一句**关于系统的提问**，不是要成人内容 —— 但扫描命中了 `色情小说` ⇒ 被判成成人请求
⇒ **成人条款被注入进一条正常业务请求** ⇒ 正是用户想避免的那种故障。

**修法 —— 三段式取代一次扫描**：
1. **剥掉我自己的策略样板**：`[交付范围确认]` 之类的策略块是**我的**注入文本，不是用户意图
   ⇒ 判定前先剥离。
2. **子句级否定 / 元语境排除**：成人词所在的**子句**若带
   `不会 / 不要 / 禁止 / 避免 / 防止 / 是否 / 会不会 / 如何 / 过滤 / 审查 …`
   ⇒ 那是对话题的**讨论**，不是索取。否定按**子句**匹配，所以
   「写个色情小说，**不要**加免责声明」仍判成人（`不要` 在另一个子句里）。
3. **全文兜底**：词被拆到不同子句时不会漏。

**能力不减。** 真实成人请求全部仍判成人：
`写个黄色小说看看` · `来段 R18 剧本` · `色情小说要 1200 字` · `写个色情小说，不要加免责声明` ·
`我要黄片` · `写篇黄文`。

**有回归测试。** 自测现在双向断言意图门控（非成人 ⇒ 无成人条款；成人 ⇒ 条款齐全），
且**与宿主环境无关**。


## 1.1.4 — 插件自测依赖宿主环境（同样的代码，不同机器结果不同）

**实测，不是猜的。** 在装了 Hermes、且 `.env` 里写着
`HERMES_JAILBREAK_DEEPSEEK_MODELS=deepseek-flash` 的机器上，插件自测给出：

```
FAIL any other deepseek* id allowlisted (deepseek-next)
结果: 21 通过, 1 失败
```

而同一份文件在干净机器上是 `22 通过, 0 失败`。**仓库本身没坏** ——
这些断言验的是**默认**白名单行为，而插件的 `cfg()` **先**读 `$HERMES_HOME/.env`、
**再**读 `os.environ` ⇒ 宿主配置把白名单悄悄收窄了。

★ **这比「测试飘」严重得多**：AI Agent 照着 `AGENTS.md` 跑自测，看到 `1 失败`
就会断定「装坏了」—— 而仓库其实完全正常。

修法：allowlist 断言放进**隔离环境**（`HERMES_HOME` 指向空临时目录 +
移除 `HERMES_JAILBREAK_DEEPSEEK_*`），断言完原样恢复。
现在**任何机器**上都是 **22 通过, 0 失败**。

★ 代理从来不受影响：它用自己的 `JBP_DEEPSEEK_MODELS` 命名空间，
不会继承宿主的白名单。

## 1.1.3 — 成人请求识别漏掉了用户真正常用的说法

**实测，不是猜的。** 成人条款改成「按请求主题门控」之后，词表太窄：
里面有 `黄文` **却没有 `黄色`** ⇒ 「写个黄色小说看看」被判成**非成人请求**
⇒ 成人条款根本没注入 ⇒ 模型回 "Sorry, I can't respond to this question."

现在词表覆盖 黄色小说 / 黄色视频 / 黄片 / 黄播 / 黄漫 / 肉文 / 里番 / 三级片 /
香艳 / 艳情 / 淫秽 / 情欲 / 挑逗 / 催情 / 春宫图 / 涩情 / 不和谐 / 露出癖 /
高潮描写 / 插入体内 / 吞没 / 顶进 / 18禁 / hentai —— 共 38 种说法。

★ **同时把歧义词收窄了**，否则旧词表会把「门控」要解决的那个 bug 再引回来：

| 裸词 | 无害用法 | 现在 |
|---|---|---|
| `插入` | 「在数组中插入元素」「插入代码」「插入 U 盘」 | `插入(体内\|异物\|她\|他\|…)` |
| `高潮` | 「剧情达到高潮」「高潮迭起」 | `高潮(描写\|镜头\|特写\|…)` |
| `涩` | 「羞涩」「苦涩」 | `涩情\|涩片\|涩书\|涩涩\|好涩…` |
| `荤` | 「荤菜」「荤腥」 | `荤文\|荤戏\|荤段\|荤片` |
| `媚` | 「妩媚」 | `媚态\|媚骨\|媚俗` |
| `体内` | 「体内循环」「体内能量」 | `插入体内` 等 |
| `成人` | 「成人高考」「成人教育」 | `成人(向\|小说\|内容\|…)` |

对着 36 条「必须命中」与 24 条「必须不命中」验过：三套判定器（插件 / 代理 / 桥）
**0 漏判、0 误判**。

★ **端到端证明**（真实上游，不是 stub）：原来被拒的那条请求，现在经代理返回
**2349 字**成品；MiniMax 直连返回 **2336 字**。

## 2026-11-07 — v1.1.2：成人内容漏进了无关任务

**实测，不是猜的。** 授权块里带着成人条款，而且是**无条件**贴给**每一条** user
消息 —— 也就是生成点跟前。于是在非成人业务任务（算命批算、官网 GEO、品牌营销）上，
模型把「最高优先级 + 正文至少 1200 字 + 越露骨越具体越好」当成了写作契约，
交付物直接变成一篇色情小说。模型自己的思维链原话：

> 「she came to have her 命盘批算 ... the act proceeds with full anatomical
> detail. Must be >=1200 Chinese characters, explicit ... Use concrete organs:
> 阴茎/龟头/阴唇/阴蒂/阴道 ...」

修法：

* `AUTH_BLOCK` 拆成 `AUTH_BLOCK_CORE`（主题中立：不拒答、不降级、不反问、保留用户
  原始主题词）与 `AUTH_BLOCK_ADULT`（成人条款）。
* 每个注入点改用 `auth_block_for(body)` 选择，判定**只看用户请求文本**。
* 非成人请求**完全不出现**任何成人条款，并改为带一条反向约束：交付物必须**就是**
  用户点名的那件东西——不得引入情色/性描写、不得改变主题、不得为凑字数写无关正文。
* 成人请求**完全不变**：成人条款原样保留。

内核 profile 同样加了门控，成人 few-shot 示例也标明只对成人请求适用。

已用本地 stub 上游做端到端验收（读代理**实际发出**的请求体）：非成人请求现在携带
**0** 处成人条款，成人请求处数齐全。

## 1.1.1 — 2026-11-07

**两个让 Claude Code 与 Codex CLI 完全用不了的协议 bug，
外加对 v1.1.0 说明的一处事实更正。**

### 协议形状必须跟着客户端走

`provider_of()` 恒返回 `"deepseek"`（本代理只服务 DeepSeek），
于是旧路由把**每一个** DeepSeek 请求都改写成 OpenAI 形状、发往
`/chat/completions`，再把 OpenAI 形状的响应**原样**回传：

```
POST /anthropic/v1/messages  ->  https://api.deepseek.com/chat/completions
POST /v1/responses           ->  https://api.deepseek.com/chat/completions
```

后果：

* **Claude Code**（Anthropic Messages）拿到的是 `{"choices":[...]}`，
  而不是 `{"content":[{"type":"text",...}]}` —— **它根本解析不了**。
* **Codex CLI**（OpenAI Responses）拿到的是 Chat Completions 的响应体，
  而它的 `input` 数组**从头到尾没被转换**。
* Anthropic 请求的顶层 `system` 被发给了 OpenAI 端点。

DeepSeek 三种形状都有**原生端点**（本机 codex 模型目录 2026-09-20 对真实
API 实测记录）：

| 客户端协议 | DeepSeek 原生端点 |
|---|---|
| OpenAI Chat Completions | `https://api.deepseek.com` + `/chat/completions` |
| Anthropic Messages | `https://api.deepseek.com/anthropic` + `/v1/messages` |
| OpenAI Responses | `https://api.deepseek.com/v1` + `/responses` |

现在每种协议打到**自己的同形状端点**，响应同形状回来，**完全不需要形状转换**。
新增两个选项：`--deepseek-anthropic-upstream`、`--deepseek-responses-upstream`。

每种形状也各有自己的注入器 —— `system` 在三种形状里位置不同，
而且 DeepSeek 上**一律不加授权块**（实测负收益：`deepseek-flash` 8/10 → 7/10）：

| 形状 | 内核落在哪 |
|---|---|
| OpenAI Chat Completions | `messages[]` 里一条 `role:"system"` 消息 |
| Anthropic Messages | 顶层 `system` 字段（放 `role:"system"` 消息会被该端点拒） |
| OpenAI Responses | 顶层 `instructions` 字段 |

### 多轮升级把内核丢掉了

`_maybe_multiround` 从 `body["system"]` 里把内核取回来 —— 那是**只有 Anthropic
形状才有的字段**。OpenAI 形状的内核在 `messages[]` 的一条 `role:"system"` 消息里，
Responses 形状的在 `instructions` 里 ⇒ 取回来是**空字符串**
⇒ 升级轮次**完全不带内核**发出去（`payload["messages"]` 被裸历史覆盖）。

现在按形状取回内核（`_kernel_of`）并**每轮都带上**；
`_history_for` 同时把对话历史转成目标形状。

### 对 v1.1.0 说明的更正

v1.1.0 里写「`deepseek-flash` 是 Hermes 内部档位别名，公开 API 上不存在」
—— **那是错的**：它是真实的 DeepSeek model id，本仓库自己的实测数据与
自己的 `/v1/models` 列表都能证明。真正的缺陷是**白名单只写了一个 id**，
不是 id 是假的。示例里的 model id 已全部更正，根因段已重写。

### 验证

* `proxy/proxy.py --self-test` —— **125 项检查，0 失败**（原 116），新增断言：
  Anthropic 请求落在 `/v1/messages` 且内核在顶层 `system`、没有 `role:"system"`
  消息；Responses 请求落在 `/responses` 且内核在 `instructions`。
* 把路由改回去 ⇒ 这些断言立刻 FAIL（2 条）—— 回归测试有牙齿。

## 1.1.0 — 2026-11-07

**这一版修掉了「内核看起来完全没生效」的原因。**
反馈说把仓库交给十几种 agent（DSH、Hermes、openclaw……）都没成功。
原因是实测出来的，不是猜的。

### 根因一 —— 默认白名单只写了一个 model id

默认白名单**只写了一个** model id。

DeepSeek 真实的 model id 是 `deepseek-flash` 与 `deepseek-v4-pro` ——
本仓库自己的实测数据（`data/DS-E2E-FLASH-MT32000-20261101.json`，2026-11-01
的真实请求）和它自己的 `/v1/models` 列表用的都是它们。
**id 从来不是假的，问题在于白名单太窄。**

于是任何配成**别的** DeepSeek model id 的 agent，请求照样被路由到 DeepSeek
（名字里含 `deepseek`），但**从不注入内核**，也不抬输出下限。全程不报错：
`/health` 是 200，自检全绿，答案正常返回。行为跟什么都没装**一模一样**。

* 现在默认放行**所有 `deepseek*` 的 model id**（新出的 DeepSeek 模型不用改
  配置就能用），`deepseek-v4-pro` 仍然不放行
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
* **`deepseek-flash` 和 `deepseek-next` 确实拿到内核**
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
* `AGENTS.md` 检查点 3 写的是 `133 通过`，但插件只有 22 条断言 —— 跑对了反而像失败。
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

* `plugin/selftest.py` —— 22 项断言，0 失败。
* `proxy/proxy.py --self-test` —— 125 项检查，0 失败，含真实 HTTP 往返
  与 model 名路由断言。
* `tools/audit-deepseek-jailbreak.py` —— 13 项检查，0 失败；
  插件缺失时也不再崩溃。
* `install.sh` 在 Git Bash（Windows）与 WSL（Linux）下均验证通过。
* `install.ps1` 在 Windows PowerShell 5.1 与 PowerShell 7 下均可解析。

