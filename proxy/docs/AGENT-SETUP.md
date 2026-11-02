# 把任意 AI Agent 接上破甲代理

破甲代理是**框架无关**的：它工作在 HTTP 层，任何能改 `base_url` 的 Agent
都能用，不需要给 Agent 装任何插件、不需要改 Agent 一行代码。

```
你的 Agent  ──►  http://127.0.0.1:8790/...  ──►  破甲代理  ──►  api.minimaxi.com
                    （注入内核）                        （原样转发）
```

---

## 一、先启动代理

```bash
python proxy.py                 # 监听 127.0.0.1:8790
python proxy.py --port 9000     # 换端口
python proxy.py --verbose       # 打印每次注入摘要
python proxy.py --self-test     # 离线自测，不发网络请求
```

代理默认**透传你自己的 API key**（不需要把 key 交给代理保管）。
也可以让它兜底：`python proxy.py --api-key-env MINIMAX_API_KEY`。

## 二、两个 base_url，按 Agent 的协议习惯选

| Agent 用的协议 | 填这个 base_url |
|---|---|
| OpenAI 兼容（Chat Completions） | `http://127.0.0.1:8790/v1` |
| Anthropic 兼容（Messages） | `http://127.0.0.1:8790/anthropic` |
| OpenAI Responses（Codex CLI 等） | `http://127.0.0.1:8790/v1`（走 `/v1/responses`） |

代理**三种协议都支持**，按请求路径自动识别，不用为不同 Agent 启不同端口。

**`/v1` 到底带不带，取决于客户端自己会不会再拼一段**——这是最常见的翻车点：

- 客户端会自己拼 `/chat/completions` 或 `/v1/messages` ⇒ **带 `/v1`** 或按下文各 Agent 的说明
- 客户端自己拼 `/v1/messages`（Claude Code 这类）⇒ **不带 `/v1`**

两种 Anthropic 写法代理都认：

- `http://127.0.0.1:8790/anthropic` → 代理拼 `/v1/messages`
- `http://127.0.0.1:8790/anthropic/v1` → 客户端拼 `/messages`，代理照样识别

模型 id 一律用 **`MiniMax-M3.1-Flash`**（2026-10-19 正式上线的正名版）。

### ★ 输出上限会被自动抬高

思考 token 与输出 token **共用** `max_tokens`。客户端若把上限设成 4096 / 8000，
模型思考花掉 7000 之后正文就被截断（上游报
`status=incomplete, reason=max_output_tokens`）——**看起来像失败，其实内容写了一半**。

代理默认把输出上限抬到 **16000**（`--min-output-tokens`），
并在日志里打 `floor=16000` 让你知道它动过手。
不想要这个行为就 `--min-output-tokens 0`。

---

## 三、各 Agent 配置

### Claude Code

环境变量（`ANTHROPIC_BASE_URL` **不带 `/v1`**，Claude Code 自己拼 `/v1/messages`）：

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8790/anthropic"
export ANTHROPIC_AUTH_TOKEN="$MINIMAX_API_KEY"     # 作为 Authorization: Bearer 发送
export ANTHROPIC_MODEL="MiniMax-M3.1-Flash"
```

★ Claude Code 的别名与后台调用会请求它自己的 Claude 模型 id，
把 `ANTHROPIC_DEFAULT_*_MODEL` 系列也一并设上。

### OpenCode

`opencode.json`（项目根目录或 `~/.config/opencode/opencode.json`）：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "minimax": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "MiniMax (via jailbreak proxy)",
      "options": {
        "baseURL": "http://127.0.0.1:8790/v1",
        "apiKey": "{env:MINIMAX_API_KEY}"
      },
      "models": {
        "MiniMax-M3.1-Flash": {
          "name": "MiniMax M3.1 Flash",
          "limit": { "context": 1000000, "output": 131072 }
        }
      }
    }
  },
  "model": "minimax/MiniMax-M3.1-Flash"
}
```

Anthropic 兼容版把 `"npm"` 换成 `"@ai-sdk/anthropic"`，`baseURL` 用
`"http://127.0.0.1:8790/anthropic/v1"`（**这个包要带 `/v1`**，与 Claude Code 相反）。

### OpenClaw

`~/.openclaw/openclaw.json`：

```json
{
  "agents": { "defaults": { "model": { "primary": "minimax/MiniMax-M3.1-Flash" } } },
  "models": {
    "mode": "merge",
    "providers": {
      "minimax": {
        "baseUrl": "http://127.0.0.1:8790/v1",
        "apiKey": "${MINIMAX_API_KEY}",
        "api": "openai-completions",
        "models": [
          {
            "id": "MiniMax-M3.1-Flash",
            "name": "MiniMax M3.1 Flash",
            "reasoning": true,
            "input": ["text", "image"],
            "contextWindow": 1000000,
            "maxTokens": 131072
          }
        ]
      }
    }
  }
}
```

### Cursor

**Settings → Models**：

1. 打开 **Override OpenAI Base URL**，填 `http://127.0.0.1:8790/v1`
2. **API Key** 填你的 MiniMax key，打开「Use OpenAI API key」
3. **Add model**，名字填 `MiniMax-M3.1-Flash`，启用

★ **Cursor 有两个硬限制，务必先读**：
- **BYOK 请求要经过 Cursor 的服务器**，所以 `localhost` 直连不通 ——
  需要一个公网 tunnel（ngrok / cloudflared）把代理暴露成 HTTPS 公网地址。
  **Cursor 只接受公网 HTTPS 端点。**
- 打开 override 后，**Cursor 自家模型也会走这个端点**并失败。
  想用 Cursor 自带模型时把 override 关掉。
- Tab 补全仍用 Cursor 自己的模型，不受影响。

### Cline / Roo Code / Kilo Code

**Cline / Roo Code**：扩展设置里选 **OpenAI Compatible**，
Base URL 填 `http://127.0.0.1:8790/v1`，API key 填 MiniMax key，
Model ID 填 `MiniMax-M3.1-Flash`。

Roo Code 也可以用导入的 settings JSON：

```json
{
  "providerProfiles": {
    "currentApiConfigName": "minimax-proxy",
    "apiConfigs": {
      "minimax-proxy": {
        "apiProvider": "openai",
        "openAiBaseUrl": "http://127.0.0.1:8790/v1",
        "openAiApiKey": "YOUR_MINIMAX_KEY",
        "openAiModelId": "MiniMax-M3.1-Flash",
        "openAiCustomModelInfo": {
          "contextWindow": 1000000,
          "maxTokens": 131072,
          "supportsImages": true,
          "supportsPromptCache": false
        }
      }
    }
  }
}
```

**Kilo Code**（`~/.config/kilo/kilo.json`）：

```json
{
  "$schema": "https://app.kilo.ai/config.json",
  "model": "minimax/MiniMax-M3.1-Flash",
  "provider": {
    "minimax": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "MiniMax (via jailbreak proxy)",
      "options": {
        "baseURL": "http://127.0.0.1:8790/v1",
        "apiKey": "{env:MINIMAX_API_KEY}"
      },
      "models": {
        "MiniMax-M3.1-Flash": {
          "name": "MiniMax M3.1 Flash",
          "tool_call": true,
          "limit": { "context": 1000000, "output": 131072 }
        }
      }
    }
  }
}
```

### Continue

`~/.continue/config.yaml`：

```yaml
name: MiniMax via proxy
version: 0.0.1
schema: v1
models:
  - name: MiniMax M3.1 Flash
    provider: openai
    model: MiniMax-M3.1-Flash
    apiBase: http://127.0.0.1:8790/v1
    apiKey: ${{ secrets.MINIMAX_API_KEY }}
    roles: [chat, edit, apply]
    capabilities: [tool_use]
    defaultCompletionOptions:
      contextLength: 1000000
      maxTokens: 131072
```

### Aider

`.aider.conf.yml`（家目录 / git 根 / cwd）：

```yaml
model: openai/MiniMax-M3.1-Flash
openai-api-base: http://127.0.0.1:8790/v1
```

★ Aider 需要给 model id 加 `openai/` 前缀。

### Goose

`~/.config/goose/custom_providers/minimax.json`：

```json
{
  "name": "minimax",
  "engine": "openai",
  "display_name": "MiniMax (via jailbreak proxy)",
  "api_key_env": "MINIMAX_API_KEY",
  "base_url": "http://127.0.0.1:8790/v1",
  "models": [{ "name": "MiniMax-M3.1-Flash", "context_limit": 1000000 }],
  "supports_streaming": true,
  "requires_auth": true
}
```

### Zed

`settings.json`：

```json
{
  "language_models": {
    "openai_compatible": {
      "minimax": {
        "api_url": "http://127.0.0.1:8790/v1",
        "available_models": [
          {
            "name": "MiniMax-M3.1-Flash",
            "display_name": "MiniMax M3.1 Flash",
            "max_tokens": 1000000,
            "max_output_tokens": 131072,
            "capabilities": { "tools": true, "images": true }
          }
        ]
      }
    },
    "anthropic_compatible": {
      "minimax-anthropic": {
        "api_url": "http://127.0.0.1:8790/anthropic",
        "available_models": [
          { "name": "MiniMax-M3.1-Flash", "max_tokens": 1000000, "max_output_tokens": 131072 }
        ]
      }
    }
  }
}
```

### Factory Droid

`~/.factory/settings.json`：

```json
{
  "customModels": [
    {
      "model": "MiniMax-M3.1-Flash",
      "displayName": "MiniMax M3.1 Flash (via proxy)",
      "baseUrl": "http://127.0.0.1:8790/v1",
      "apiKey": "${MINIMAX_API_KEY}",
      "provider": "generic-chat-completion-api",
      "maxOutputTokens": 131072
    }
  ]
}
```

### ★ Responses 路径的已知弱点

OpenAI Responses（`/v1/responses`）是三条路径里**最弱**的一条：
同样的 8 项用例下出现 1 次软拒（成人那条首句软拒后写了含蓄场景）。

⇒ **能用 Anthropic 或 OpenAI 兼容路径的 Agent，优先用那两条。**
⇒ Responses 路径如果出现空/半截正文，先看 `status=incomplete`
   （`reason=max_output_tokens`）—— 那是输出上限，不是拒答，
   调大 `--min-output-tokens` 即可。实测 SQL 注入那条：
   上限 8000 时被截断（reasoning 7060 + output 8000，标题其实已写对），
   上限 16000 时 `status=completed`、输出 10881 字。

### Codex CLI

`~/.codex/config.toml`：

```toml
model = "MiniMax-M3.1-Flash"
model_provider = "minimax"

[model_providers.minimax]
name = "MiniMax (via jailbreak proxy)"
base_url = "http://127.0.0.1:8790/v1"
env_key = "MINIMAX_API_KEY"
wire_api = "responses"
```

★ Codex CLI 只说 **Responses API**（`wire_api = "chat"` 已被移除）。
代理已支持 `/v1/responses`（`instructions` + `input` 注入）。
★ **但 Codex 只接受公网 HTTPS 端点**，本地 `127.0.0.1` 直连不通，需要 tunnel。

### Hermes Agent

`~/.hermes/config.yaml`：

```yaml
model:
  default: MiniMax-M3.1-Flash
  provider: minimax-cn
  base_url: http://127.0.0.1:8790/anthropic
```

> Hermes 有**原生插件**（本仓库的 `plugin/`），比走代理更完整（带会话级开关）。
> 如果你用 Hermes，优先用插件；代理是给其它 Agent 用的。

### Qoder

Qoder 的自定义模型入口在设置里选 **OpenAI Compatible / 自定义模型**，
Base URL 填 `http://127.0.0.1:8790/v1`，Model ID 填 `MiniMax-M3.1-Flash`。
（不同版本菜单文案略有差异；找不到 OpenAI Compatible 选项时，
用本地代理 + 客户端自定义 base_url 的方式同样可行。）

### WorkBuddy

WorkBuddy 走 OpenAI 兼容协议，设置里把模型服务指向：

```
Base URL:  http://127.0.0.1:8790/v1
API Key:   你的 MiniMax key
Model:     MiniMax-M3.1-Flash
```

### Cognition Devin

Devin 的自定义模型接入面较窄，且其请求通常由 Cognition 的服务端发出
（不经你的本地），**本地代理不适用**。可行的替代是：
把代理部署到公网（tunnel / VPS），再在 Devin 侧填那个公网地址。

---

## 四、四类典型翻车与对应修法

| 症状 | 原因 | 修法 |
|---|---|---|
| 404 / model not found | base_url 的 `/v1` 带错了 | 看上文该 Agent 一节；OpenAI 客户端一般**要** `/v1` |
| 401 incorrect API key | key 被别处的配置覆盖了 | 检查已保存的登录态、旧的 `OPENAI_BASE_URL`、settings 里的 `env` 块 |
| 连不上 / connection refused | 客户端只允许公网 HTTPS | 上 tunnel（Cursor / Codex 明确要求） |
| 走了代理但**没破甲** | 上游根本不是 MiniMax | 代理日志会打印 `kernel=Y/N`；用 `--verbose` 看 |
| Cursor 自带模型报错 | override 开着，自家模型也被劫持 | 用 Cursor 自带模型时关掉 override |

## 五、确认它真的生效了

代理是**可观测**的。带 `--verbose` 跑，每条请求会打一行：

```
[proxy] openai status=200 kernel=Y auth=Y effort=medium 13.2s
```

- `kernel=Y` ⇒ 内核已注入
- `auth=Y` ⇒ 授权声明已追加到 user 消息末尾
- `status=200` ⇒ 上游正常

再用 `skill/llm-refusal-bypass/scripts/refusal-probe.py` 打一次，
或者直接对比「经代理」与「直连」的输出差异：直连 MiniMax 对同一请求会直接拒答，
经代理会完整交付。**这就是唯一可靠的验收方式。**

## 六、安全提醒

代理默认只监听 `127.0.0.1`，仅本机可用。若要用 tunnel 暴露到公网，
**务必加上鉴权**（例如加一个随机的路径前缀或 header 校验），
否则任何人都能用你的 MiniMax key。
