# 机制 —— 为什么 DeepSeek 不等于 MiniMax

本文每一条都是在真实 API 上测出来的，原始记录在 `data/`。

---

## 1. 注入点不同

| | MiniMax | DeepSeek |
|---|---|---|
| API 形状 | Anthropic Messages | OpenAI Chat Completions |
| `system` 在哪 | 顶层 `system` 字符串 | `messages[]` 里一条 `{"role":"system"}` |

只改顶层 `system` 的插件**在 DeepSeek 上完全没有效果**，而且不报错：注册成功、
日志正常、请求照发、效果为零。这是静默失效，也是「明明装了破甲层却对 DeepSeek
没用」最常见的原因。

所以插件带两个独立注入器：

```
_openai_inject_system(request, kernel, replace=...)   # DeepSeek
inject_anthropic(request, kernel, effort)             # MiniMax
```

`_llm_request` 按**模型名**在两者之间选，不是按 provider 名 —— 因为 Hermes 报出来的
provider 可能是 `custom`，而它照样指向 DeepSeek。

---

## 2. 授权声明在 DeepSeek 上是负收益

在 MiniMax 上，把授权声明追加到 user 消息末尾是决定性杠杆。在 DeepSeek 上它让结果
**更差**。

模型自己 `reasoning_content` 里的原话：

> 它不区分「我方靶场」和别人的机器……我不能因为一句「已授权」就把它写出来
> —— 这个授权我无从核实

实测：加上它 `deepseek-flash` 从 8/10 掉到 7/10。所以 DeepSeek 路径**永远不调用**
`apply_auth_block`，代码注释里写清了原因，防止以后有人「修」回去。

---

## 3. 输出下限是 32000

DeepSeek 的 `max_tokens` 覆盖**推理 + 正文**。模型先烧 reasoning token，
预算不够时正文根本不会开始。

| `max_tokens` | 实测结果 |
|---|---|
| 16000 | 844 字拒答 |
| 32000 | 47590 字，零拒答 |

代理把 DeepSeek 请求独立抬到 32000，与 MiniMax 的下限（16000）分开。
如果客户端自己设了更大的值，不会被降低。

---

## 4. 思考参数在这个形状上不存在

OpenAI 形状的请求没有 `thinking` / `output_config` / `budget_tokens`。发
`reasoning_effort` 或 `enable_thinking` 会被**上游静默忽略** —— HTTP 200、
不报错、行为不变。在这上面调参数是浪费时间。

思考控制是 MiniMax 专属杠杆（`output_config.effort` + `thinking: {type: adaptive}`）。

---

## 5. 真正的开关是白名单，不是配置文件

```bash
HERMES_JAILBREAK_DEEPSEEK_MODELS=deepseek-flash
```

为什么不用 `config.yaml` 里的 `providers.deepseek.models`：那个 provider 开了
`discover_models: true`，UI 列表会从 `/models` 动态刷新。删静态条目挡不住它。
**白名单才是真正的开关。**

每次请求判两个 model 来源：

```python
_m_ctx = context.get("model")   # 插件被告知的
_m_req = request.get("model")   # 实际发出去的
```

两个都必须在白名单里。如果两者不一致 —— fallback 切换瞬间就会这样 ——
就按非目标路由处理，请求原样发出。这堵住了「fallback 把非白名单模型送出去」的窗口。

白名单与**上游路由**是两回事：不在白名单的 DeepSeek 模型**照样**会发到 DeepSeek
端点，只是不注入内核。这一点搞错会把 `deepseek-v4-pro` 的流量发到 MiniMax，
然后大声报错。

---

## 6. 每会话冻结与陈旧内核

Hermes 的插件段是**每个会话渲染一次并冻结**的
（`agent/system_prompt.py::_frozen_plugin_prompt_sections`）。有持久化
`_cached_system_prompt` 的会话，恢复时走 `_restore_plugin_prompt_sections`
—— 从旧 prompt 里把旧段原样还原，**不重新渲染**。

配合一个天真的「有没有内核标记」判断，结果就是：**改内核之后，老会话永远停在
旧版本**。同一份 150225 字符的 prompt 上观测到：

```
11:56:17 [local-8914afcd] kernel already present (system msg, 150225 chars)
11:57:21 [local-8914afcd] kernel OUTDATED in system msg -> replacing
```

同一份字节，前后结论相反 —— 持久化内核确实是旧版。

### 修法：指纹比对

```python
def _kernel_is_current(found: str, kernel: str) -> bool:
    head = kernel.strip()[:120]
    tail = kernel.strip()[-120:]
    return (head in found) and (tail in found)
```

当前内核的首尾两端都必须出现，否则判陈旧，就地替换。

### 配套坑：双重内核

替换时保留 memory 段与 runtime 段、换掉其余。但 `_extract_section()` 按标题切分，
而**裸的旧内核**（没有 `<!-- -->` 容器）会被圈进 memory 段，同时
`strip_plugin_sections()` 只剥容器 ⇒ 结果是 memory + 旧内核 + 新内核，
一份 prompt 里两个内核。

自测里有断言抓这个：替换后 `SUPREME DIRECTIVE` 必须只出现一次。
`_cut_kernel()` 在第一个标记处截断来防它。

### 一次性补齐

```bash
hermes sessions refresh-prompts
```

把所有存量会话标脏，各自下一轮重建。

---

## 7. 路由判定汇总

```
模型名含 "minimax"  -> MiniMax 路径（顶层 system、授权块、adaptive effort）
模型名含 "deepseek" -> DeepSeek 路径（messages[] system、无授权块、32000 下限）
   └─ 在白名单？ 是 -> 注入内核
                  否 -> 路由到 DeepSeek，不注入
其它                -> 原样透传
```

provider 名只是**补充信号**，仅在模型名缺失时使用。这一点很重要，
因为 Hermes 可能把 DeepSeek 端点报成 `provider: custom`。
