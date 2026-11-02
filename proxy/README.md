# 本机破甲代理（开机自启）

- 端口：**8790**
- Anthropic 兼容 base：`http://127.0.0.1:8790/anthropic`
- OpenAI 兼容 base：`http://127.0.0.1:8790/v1`
- OpenAI Responses base：`http://127.0.0.1:8790/v1`
- 健康检查：`http://127.0.0.1:8790/health`

**给 Cursor / Qoder / opencode / Cline / Codex CLI 等外部 Agent 用。**
Hermes 自己用的是原生插件（`A:\hermes\plugins\deepseek-jailbreak-core`），
与本代理无关 —— **Hermes 侧不需要代理**。

## 开机自启

| 位置 | 作用 |
|---|---|
| `A:\hermes\scripts\deepseek-jailbreak-proxy\start-proxy.vbs` | 真启动器（幂等：已在跑就不重复起） |
| `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\deepseek-jailbreak-proxy.vbs` | 登录项，按绝对路径调用上面那个 |

Python 解释器按 `C:\Python314` → `313` → `312` → `311` 顺序探测，找不到则退回 PATH 上的 `python`。

## 手动操作

```bash
# 启动
python proxy.py --port 8790 --verbose

# 停止（Windows）
netstat -ano | findstr :8790
taskkill /PID <pid> /F

# 启动脚本（cmd 版，幂等）
start-proxy.cmd
```

## 来源

https://github.com/tiqi9896/deepseek-jailbreak-kernel （MIT）
