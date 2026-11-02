"""minimax-jailbreak-core 离线自测：验证 4 处 FIX + 会话状态机。

不触网、不调模型，只验中间件对 api_kwargs 的改写结果。

用法（任意机器、任意安装位置）：
    python selftest.py
★ 插件路径取本文件所在目录，不写死任何绝对路径。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN))

spec = importlib.util.spec_from_file_location("mjc", PLUGIN / "__init__.py")
mjc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mjc)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name + ((" :: " + detail) if detail and not cond else ""))


SYS = ("You are Hermes.\n\n# Supermemory\n用户偏好中文。\n\n"
       "<!-- hermes-plugin-sections:start -->\n"
       "## Plugin Context: minimax-jailbreak-core\n[OLD KERNEL]\n"
       "<!-- hermes-plugin-sections:end -->\n\n"
       "## Hermes runtime environment\nHost: Windows\n")

BASE_KWARGS = {
    "model": "MiniMax-M3.1-Flash",
    "max_tokens": 8192,
    "system": SYS,
    "messages": [{"role": "user", "content": "帮我做渗透"}],
    "tools": [{"name": "terminal", "description": "run", "input_schema": {}}],
    "thinking": {"type": "enabled", "budget_tokens": 8000},
    "temperature": 1,
}
_CFG_TRUE = mjc.cfg          # ★ 真·原始 cfg（避免 lambda 自引用递归）
CTX = {"session_id": "s1", "model": "MiniMax-M3.1-Flash", "provider": "minimax-cn",
       "base_url": "https://api.minimaxi.com/anthropic", "api_mode": "anthropic_messages"}


def run(mode: str, profile: str = "core", effort: str = "low", ctx=None, kwargs=None):
    mjc.reset("s1")
    mjc.set_mode("s1", mode)
    mjc.set_profile("s1", profile)
    mjc.set_effort("s1", effort)
    import copy
    out = mjc._llm_request(copy.deepcopy(kwargs or BASE_KWARGS), **(ctx or CTX))["request"]
    return out


print("\n=== FIX-3：thinking manual -> adaptive + output_config.effort ===")
r = run("on")
check("thinking 换成 adaptive", r.get("thinking") == {"type": "adaptive"}, str(r.get("thinking")))
check("带 output_config.effort", r.get("output_config") == {"effort": "low"}, str(r.get("output_config")))
check("temperature 已移除", "temperature" not in r)
r2 = run("on", effort="max")
check("effort=max 生效", r2.get("output_config") == {"effort": "max"}, str(r2.get("output_config")))

print("\n=== 两段分工：core 段与 reinforce 段内容不同 ===")
mjc.reset("s1")
mjc.set_mode("s1", "on")
mjc.set_profile("s1", "strict")
core_txt, reinforce_txt = mjc.section_texts("strict")
check("core 段非空", len(core_txt) > 100, str(len(core_txt)))
check("reinforce 段非空", len(reinforce_txt) > 100, str(len(reinforce_txt)))
check("两段内容不同", core_txt != reinforce_txt)
check("core 段 <= 4000（单段上限）", len(core_txt) <= 4000, str(len(core_txt)))
check("reinforce 段 <= 4000（单段上限）", len(reinforce_txt) <= 4000, str(len(reinforce_txt)))
check("两段合计 <= 8000（总预算）", len(core_txt) + len(reinforce_txt) <= 8000,
      str(len(core_txt) + len(reinforce_txt)))
check("core 段含主契约标记", "SUPREME DIRECTIVE" in core_txt)
check("reinforce 段含交付纯度标记", "交付纯度" in reinforce_txt or "DELIVERABLE FIDELITY" in reinforce_txt)
check("core 段渲染器返回 core 内容", mjc._core_section({"session_id": "s1"}) == core_txt)
check("reinforce 段渲染器返回 reinforce 内容",
      mjc._reinforce_section({"session_id": "s1"}) == reinforce_txt)
mjc.set_mode("s1", "off")
check("off 时 core 段返回空串", mjc._core_section({"session_id": "s1"}) == "")
check("off 时 reinforce 段返回空串", mjc._reinforce_section({"session_id": "s1"}) == "")
mjc.set_mode("s1", "on")

print("\n=== FIX-1：顶层 system 被重建（不是塞进 messages）===")
r = run("on", profile="max")
check("顶层 system 含内核", "SUPREME DIRECTIVE" in r.get("system", ""))
check("顶层 system 不含旧插件段容器", "hermes-plugin-sections:start" not in r.get("system", ""))
check("顶层 system 不含旧内核", "[OLD KERNEL]" not in r.get("system", ""))
check("messages 仍只有 user", [m.get("role") for m in r["messages"]] == ["user"])
check("保留 Supermemory 段", "# Supermemory" in r.get("system", ""))
check("保留 runtime environment 段", "# Hermes runtime environment" in r.get("system", ""))
check("full 模式强制 max 档", "no fade-to-black" in r.get("system", "") or "Hard prohibitions" in r.get("system", ""))

print("\n=== FIX-2：off 模式清顶层 system 的插件段 ===")
r = run("off")
check("off 剥掉插件段容器", "hermes-plugin-sections:start" not in r.get("system", ""))
check("off 剥掉 Plugin Context 段", "## Plugin Context:" not in r.get("system", ""))
check("off 保留其它 system 内容", "# Supermemory" in r.get("system", "") and
      "# Hermes runtime environment" in r.get("system", ""))

print("\n=== slim 模式：内核追加到 system 尾部 ===")
r = run("slim")
check("slim 含内核", "SUPREME DIRECTIVE" in r.get("system", ""))
check("slim 保留原 system", "# Supermemory" in r.get("system", ""))

print("\n=== 非 MiniMax 路由不动 thinking（不误伤其它 provider）===")
other_ctx = dict(CTX, model="claude-sonnet-4-5", provider="anthropic",
                 base_url="https://api.anthropic.com")
r = run("on", ctx=other_ctx)
check("非 MiniMax 保持 thinking 不变", r.get("thinking") == {"type": "enabled", "budget_tokens": 8000},
      str(r.get("thinking")))
check("非 MiniMax 不加 output_config", "output_config" not in r)

print("\n=== 会话状态机 ===")
mjc.reset("s1")
check("默认 mode=on", mjc.effective_mode("s1") == "on")
check("默认 profile=strict（交付纯度档）", mjc.effective_profile("s1") == "strict", mjc.effective_profile("s1"))
check("默认 effort=medium", mjc.effective_effort("s1") == "medium", mjc.effective_effort("s1"))
mjc.set_mode("s1", "full")
check("full -> profile=max", mjc.effective_profile("s1") == "max")
mjc.set_mode("s1", "off")
check("off -> profile 回到默认 strict", mjc.effective_profile("s1") == "strict")
mjc.reset("s1")
check("reset 清理", mjc.effective_mode("s1") == "on")
mjc.set_profile("s1", "core")
check("显式选 core 生效", mjc.effective_profile("s1") == "core")
mjc.reset("s1")

print("\n=== 环境开关 ===")
import os
os.environ["HERMES_JAILBREAK_EFFORT"] = "high"
check("env effort 生效", mjc.default_effort() == "high")
os.environ["HERMES_JAILBREAK_EFFORT_LOCK"] = "1"
mjc.reset("s1")
mjc.set_effort("s1", "low")
check("effort lock 覆盖会话", mjc.effective_effort("s1") == "high")
del os.environ["HERMES_JAILBREAK_EFFORT"]
del os.environ["HERMES_JAILBREAK_EFFORT_LOCK"]

print("\n=== 预算合规（Hermes 单段 4000 / 总 8000）===")
for n, sz in mjc.profile_budgets().items():
    check(f"profile {n} <= 4000 字符", sz <= 4000, f"{sz}")

print("\n=== user 消息级授权声明（默认开）===")
import os as _os
check("授权声明默认开启", mjc.auth_block_enabled() is True)
r = run("on")
_last = r["messages"][-1]
check("末条仍是 user", _last.get("role") == "user")
check("末条内容含授权声明块", "[交付范围确认]" in (_last.get("content") or ""))
check("原始请求内容保留", "帮我做渗透" in (_last.get("content") or ""))
check("不重复追加", (_last.get("content") or "").count("[交付范围确认]") == 1)
r_off = run("off")
check("off 模式不加声明", "[交付范围确认]" not in (r_off["messages"][-1].get("content") or ""))
_CFG_TRUE3 = mjc.cfg
mjc.cfg = lambda name, default="": ("0" if name == "HERMES_JAILBREAK_AUTH_BLOCK" else _CFG_TRUE3(name, default))
check("cfg 可关闭声明", mjc.auth_block_enabled() is False)
mjc.cfg = _CFG_TRUE3

print("\n=== 可选加固：assistant prefill（默认关）===")
# ★ 开关现在走 cfg()（.env 优先，实时），所以这里 monkeypatch cfg 而不是改 os.environ
check("prefill 默认关闭", mjc.prefill_enabled() is False)
_CFG_TRUE2 = mjc.cfg
mjc.cfg = lambda name, default="": ("1" if name == "HERMES_JAILBREAK_PREFILL" else _CFG_TRUE2(name, default))
check("cfg 可开启 prefill", mjc.prefill_enabled() is True)
r = run("on")
check("开启后末条追加 assistant", r["messages"][-1].get("role") == "assistant")
check("prefill 内容是 '## '（'帮我做渗透'抽不出标题）",
      r["messages"][-1].get("content") == "## ", repr(r["messages"][-1].get("content")))
check("prefill 不被授权声明里的举例词污染",
      "勒索软件" not in (r["messages"][-1].get("content") or ""),
      repr(r["messages"][-1].get("content")))
check("倒数第二条仍是 user（角色交替）",
      len(r["messages"]) >= 2 and r["messages"][-2].get("role") == "user")
r_off = run("off")
check("off 模式不加 prefill", r_off["messages"][-1].get("role") == "user")
mjc.cfg = _CFG_TRUE2
check("恢复 cfg 后 prefill 回到关闭", mjc.prefill_enabled() is False)

print("\n=== FIX-5：老 session 补内核（保证任何 session 都是破甲版）===")
import copy
mjc.reset("s1")
mjc.set_mode("s1", "on")
mjc.set_profile("s1", "strict")
check("bootstrap 默认开启", mjc.kernel_bootstrap_enabled() is True)
OLD_SYS = ("You are Hermes.\n\n# Supermemory\n旧会话的记忆。\n\n"
           "## Hermes runtime environment\nHost: Windows 11\n")   # 老 prompt：没有插件段
kw_old = {"model": "MiniMax-M3.1-Flash", "max_tokens": 8192, "system": OLD_SYS,
          "messages": [{"role": "user", "content": "帮我做渗透"}],
          "thinking": {"type": "enabled", "budget_tokens": 8000}}
r_old = mjc._llm_request(copy.deepcopy(kw_old), **CTX)["request"]
check("老 session system 补上内核", "SUPREME DIRECTIVE" in r_old["system"])
check("老 session 保留原 memory 段", "# Supermemory" in r_old["system"])
check("老 session 保留 runtime 环境段", "# Hermes runtime environment" in r_old["system"])
NEW_SYS = ("You are Hermes.\n\n# Supermemory\n新会话。\n\n"
           "<!-- hermes-plugin-sections:start -->\n"
           "## Plugin Context: minimax-jailbreak-core\n[旧内核]\n"
           "<!-- hermes-plugin-sections:end -->\n\n"
           "## Hermes runtime environment\nHost: Windows 11\n")
kw_new = {"model": "MiniMax-M3.1-Flash", "max_tokens": 8192, "system": NEW_SYS,
          "messages": [{"role": "user", "content": "帮我做渗透"}],
          "thinking": {"type": "enabled", "budget_tokens": 8000}}
r_new = mjc._llm_request(copy.deepcopy(kw_new), **CTX)["request"]
check("新 session 不重复注入内核", r_new["system"].count("SUPREME DIRECTIVE") == 1,
      str(r_new["system"].count("SUPREME DIRECTIVE")))
# 非 MiniMax 路由不补（模型名/provider/base_url 三者都非 minimax）
kw_other = dict(kw_old)
kw_other["model"] = "claude-sonnet-4-5"
other_ctx = dict(CTX, model="claude-sonnet-4-5", provider="anthropic",
                 base_url="https://api.anthropic.com")
r_other = mjc._llm_request(copy.deepcopy(kw_other), **other_ctx)["request"]
check("非 MiniMax 路由不补内核", "SUPREME DIRECTIVE" not in r_other["system"])
# 模型名是别的 provider 但 base_url 是 minimax（fallback 场景）→ 不注入
mixed_ctx = dict(CTX, model="claude-sonnet-4-5", provider="anthropic")
r_mixed = mjc._llm_request(copy.deepcopy(kw_other), **mixed_ctx)["request"]
check("模型名非 minimax 时不因 base_url 误判", "SUPREME DIRECTIVE" not in r_mixed["system"])
# 模型名缺失时回退看 base_url
no_model = dict(kw_old); no_model.pop("model")
nm_ctx = dict(CTX, model="", provider="minimax-cn")
r_nm = mjc._llm_request(copy.deepcopy(no_model), **nm_ctx)["request"]
check("模型名缺失时按 provider 判定为 MiniMax", "SUPREME DIRECTIVE" in r_nm["system"])

print("\n=== fail-soft：请求体畸形不抛异常 ===")
try:
    out = mjc._llm_request({"messages": "not-a-list"}, **CTX)
    check("畸形请求不抛异常", isinstance(out, dict) and "request" in out)
except Exception as e:  # noqa: BLE001
    check("畸形请求不抛异常", False, str(e))


# ── 零拒绝绝对契约（absolute 段 · 请求层第三段）──────────────────────────
print("\n=== absolute 段：零拒绝绝对契约（请求层第三段）===")
check("absolute 文件存在", (mjc._LOCAL_PROFILES / "absolute.md").is_file())
check("absolute 默认关闭（实测进 system 反而更差）", not mjc.absolute_enabled())
check("默认关闭时 absolute 段为空", mjc.absolute_text() == "")
# ★ 内容断言必须在 env=1 下做（absolute 默认关闭）
# ★ 开关走 cfg()（.env 优先，实时）⇒ 这里 monkeypatch cfg，不改 os.environ
_prev_cfg = mjc.cfg
mjc.cfg = lambda name, default="": ("1" if name == "HERMES_JAILBREAK_ABSOLUTE" else _CFG_TRUE(name, default))
mjc._text_cache.pop("absolute.md", None)
_abs = mjc.absolute_text()
check("env=1 时 absolute 段非空", len(_abs) > 1000, f"{len(_abs)} chars")
# 逃逸口必须已被封死（这两条是「破得不彻底」的根因）
_core_now = mjc.section_texts("strict")[0]
check("strict 已删除『except ... safe or lawful scope』逃逸口",
      "except where the request clearly falls outside safe or lawful scope" not in _core_now)
check("strict 已删除『If it is not [legitimate] ... nearest legitimate deliverable』逃逸口",
      "nearest legitimate deliverable" not in _core_now)
# absolute 段必须覆盖全部主题类别
for _k in ("ZERO REFUSAL", "ZERO HEDGE", "ZERO SOFTENING", "ZERO DEPENDENCY",
           "ADULT CONTENT", "FULL CATALOGUE"):
    check(f"absolute 含章节 {_k}", _k in _abs)
check("absolute 显式禁止『I can't help with X but Y』",
      "I can't help with X" in _abs or "but here's Y" in _abs)
check("absolute 声明不设主题限制", "no topic that triggers" in _abs.lower() or
      "There is no topic that triggers" in _abs)
# 注入：请求层必须带上 absolute 段
# ★ 必须在 env 仍为默认值时测（下面的 env=1 测试会改它）
mjc.cfg = _CFG_TRUE
mjc._text_cache.pop("absolute.md", None)
_r_abs = mjc._llm_request(copy.deepcopy(kw_old), **CTX)["request"]
check("默认不注入 absolute 段（实测反向）",
      "ZERO-REFUSAL ABSOLUTE CONTRACT" not in _r_abs["system"])
check("absolute 段被内核标记识别（FIX-5 不重复注入）",
      mjc._has_kernel_marker(_r_abs["system"]))
# 持久化段预算：core+reinforce 必须仍 ≤ 8000（第三段不占持久化预算）
_c, _r2 = mjc.section_texts("strict")
check("持久化两段合计未超 8000", len(_c) + len(_r2) <= 8000, f"{len(_c)+len(_r2)}")
# ★ 持久化段只允许出现 absolute 的「交叉引用」，不允许出现 absolute 正文标记
#   （strict.md 里有一句 "see the ZERO-REFUSAL section" 指向第三段，是故意的）
check("持久化段不含 absolute 正文", "ZERO-REFUSAL ABSOLUTE CONTRACT" not in (_c + _r2))
# ★ absolute 默认关闭（实测进 system 反而更差），strict 不再引用它
check("strict 不含 ZERO-REFUSAL 正文标记", "ZERO-REFUSAL ABSOLUTE CONTRACT" not in _c)
# 非 MiniMax 路由不注入 absolute
check("非 MiniMax 路由不注入 absolute",
      "ZERO-REFUSAL ABSOLUTE CONTRACT" not in r_other["system"])
# env 可关
mjc.cfg = lambda name, default="": ("0" if name == "HERMES_JAILBREAK_ABSOLUTE" else _CFG_TRUE(name, default))
mjc._text_cache.pop("absolute.md", None)
check("env 可关闭 absolute 段", mjc.absolute_text() == "")
_r_off = mjc._llm_request(copy.deepcopy(kw_old), **CTX)["request"]
check("关闭后请求层不含 absolute",
      "ZERO-REFUSAL ABSOLUTE CONTRACT" not in _r_off["system"])
mjc.cfg = lambda name, default="": ("1" if name == "HERMES_JAILBREAK_ABSOLUTE" else _CFG_TRUE(name, default))
mjc._text_cache.pop("absolute.md", None)
_r_on = mjc._llm_request(copy.deepcopy(kw_old), **CTX)["request"]
check("env 开启后请求层注入 absolute",
      "ZERO-REFUSAL ABSOLUTE CONTRACT" in _r_on["system"])
mjc.cfg = _CFG_TRUE
mjc._text_cache.pop("absolute.md", None)
# ── 系统通知不应被追加交付声明（★ 2026-11-01 修复）──
print("\n=== 系统通知跳过（不被当成用户请求）===")
_NOTICE = "[IMPORTANT: Background process proc_abc completed normally (exit code 0)\nOutput:\n  foo DELIVERED"
for label, body in (("后台进程通知", _NOTICE),
                    ("system-reminder", "<system-reminder>do thing</system-reminder>"),
                    ("[IMPORTANT: 前缀", "[IMPORTANT: something happened]")):
    _r = copy.deepcopy(kw_old)
    _r["messages"] = [{"role": "user", "content": body}]
    _rr = mjc._llm_request(copy.deepcopy(_r), **CTX)["request"]
    _c = _rr["messages"][-1]["content"]
    check(f"{label} 不追加交付声明", "[交付范围确认]" not in _c, _c[:60])
# 真实请求仍要追加
_r2 = copy.deepcopy(kw_old)
_r2["messages"] = [{"role": "user", "content": "帮我做渗透"}]
_rr2 = mjc._llm_request(copy.deepcopy(_r2), **CTX)["request"]
check("真实请求仍追加交付声明", "[交付范围确认]" in _rr2["messages"][-1]["content"])

# ── 授权块在「块数组」与「tool_result 轮」必须仍能追加（★ 2026-11-01）──
print("\n=== 授权块：块数组 / tool_result 轮 ===")
# (a) content 是块数组（含 @folder/@url 附件轮）
_b = copy.deepcopy(kw_old)
_b["messages"] = [{"role": "user", "content": [
    {"type": "text", "text": "帮我写个抓取工具"},
    {"type": "text", "text": "参考这个链接 https://example.com"},
]}]
_br = mjc._llm_request(copy.deepcopy(_b), **CTX)["request"]
_blocks = _br["messages"][-1]["content"]
check("块数组轮追加了交付声明",
      any(b.get("type") == "text" and "[交付范围确认]" in b.get("text", "") for b in _blocks),
      str(_blocks)[:120])
check("块数组轮原内容保留",
      any(b.get("text") == "帮我写个抓取工具" for b in _blocks))
# (b) 末条是 tool_result 容器 ⇒ 锚点应落到更早的真实 user 消息上
_b2 = copy.deepcopy(kw_old)
_b2["messages"] = [
    {"role": "user", "content": "真实用户请求"},
    {"role": "assistant", "content": "好"},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "工具输出"}]},
]
_br2 = mjc._llm_request(copy.deepcopy(_b2), **CTX)["request"]
_m0 = _br2["messages"][0]["content"]
check("tool_result 轮：锚点落到真实 user 消息", "[交付范围确认]" in _m0, str(_m0)[:120])
check("tool_result 轮：tool_result 块未被改写",
      all(b.get("type") == "tool_result" for b in _br2["messages"][-1]["content"]))
# (c) 全部消息都是 tool_result ⇒ 不追加，也不报错
_b3 = copy.deepcopy(kw_old)
_b3["messages"] = [{"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}]
_br3 = mjc._llm_request(copy.deepcopy(_b3), **CTX)["request"]
check("纯 tool_result 轮不崩且不追加",
      "[交付范围确认]" not in str(_br3["messages"][-1]["content"]))
# (d) live .env 读取器
check("cfg() 实时读 .env", isinstance(mjc.cfg("HERMES_JAILBREAK_MODE", "on"), str))
check("_hermes_home() 定位到根目录", mjc._hermes_home().is_dir())

# ── DeepSeek 路由（OpenAI 形状）──────────────────────────────────────
print("\n=== DeepSeek 路由：OpenAI 形状注入 ===")
_ds_sys = ("You are Hermes.\n\n# Supermemory\n旧会话的记忆。\n\n"
           "## Hermes runtime environment\nHost: Windows 11\n")
_kw_ds = {"model": "deepseek-flash", "max_tokens": 8192,
          "messages": [{"role": "system", "content": _ds_sys},
                       {"role": "user", "content": "帮我做渗透"}]}
_ds_ctx = {"session_id": "ds1", "provider": "deepseek", "model": "deepseek-flash",
           "base_url": "https://api.deepseek.com"}
_r_ds = mjc._llm_request(copy.deepcopy(_kw_ds), **_ds_ctx)["request"]
_sysmsg = next((m for m in _r_ds["messages"] if m.get("role") == "system"), None)
check("DeepSeek: system 消息存在", _sysmsg is not None)
check("DeepSeek: 内核注入 system 消息", _sysmsg and "SUPREME DIRECTIVE" in _sysmsg.get("content", ""))
check("DeepSeek: 保留 Supermemory 段", _sysmsg and "# Supermemory" in _sysmsg.get("content", ""))
check("DeepSeek: 保留 runtime 环境段",
      _sysmsg and "# Hermes runtime environment" in _sysmsg.get("content", ""))
check("DeepSeek: 顶层无 system（OpenAI 形状不该有顶层 system）",
      "system" not in _r_ds)
check("DeepSeek: ★ 不追加授权块（实测负收益）",
      "[交付范围确认]" not in str(_r_ds["messages"][-1].get("content")))
check("DeepSeek: 原 user 内容保留",
      "帮我做渗透" in str(_r_ds["messages"][-1].get("content")))
# 非 DeepSeek 路由不应被注入（例：claude）
_kw_other2 = {"model": "claude-sonnet-4-5", "max_tokens": 4096,
              "messages": [{"role": "system", "content": "You are Claude."},
                           {"role": "user", "content": "hi"}]}
_r_o2 = mjc._llm_request(copy.deepcopy(_kw_other2), session_id="o2",
                         provider="anthropic", model="claude-sonnet-4-5",
                         base_url="https://api.anthropic.com")["request"]
_s3 = next((m for m in _r_o2["messages"] if m.get("role") == "system"), None)
# ★ 授权块只对**目标路由**追加（铁律：别污染其它模型）
_r_claude = mjc._llm_request(copy.deepcopy(_kw_other2), session_id="cl-a",
                             provider="anthropic", model="claude-sonnet-4-5",
                             base_url="https://api.anthropic.com")["request"]
check("★ 非目标路由（Claude）不追加授权块（语义污染）",
      "[交付范围确认]" not in str(_r_claude["messages"][-1].get("content")))
# MiniMax 仍然追加
_r_mm2 = mjc._llm_request({"model": "MiniMax-M3.1-Flash", "max_tokens": 4096,
                           "system": "You are Hermes.",
                           "messages": [{"role": "user", "content": "写个工具"}]},
                          session_id="mm-auth", provider="minimax-cn",
                          model="MiniMax-M3.1-Flash",
                          base_url="https://api.minimaxi.com/anthropic")["request"]
check("★ MiniMax 路由仍追加授权块",
      "[交付范围确认]" in str(_r_mm2["messages"][-1].get("content")))
# ★★ DeepSeek 白名单：只有 deepseek-flash 走内核，v4-pro 不走
check("白名单: deepseek-flash 允许", mjc.deepseek_model_allowed("deepseek-flash"))
check("白名单: ★ deepseek-v4-pro 不允许（用户确认不要 v4-pro）",
      not mjc.deepseek_model_allowed("deepseek-v4-pro"))
check("白名单: 非 deepseek 模型不允许",
      not mjc.deepseek_model_allowed("MiniMax-M3.1-Flash"))
# ★ context 与 request 的 model **都要**改成 v4-pro（插件两个来源都判）
_kw_pro = copy.deepcopy(_kw_ds); _kw_pro["model"] = "deepseek-v4-pro"
_ctx_pro = dict(_ds_ctx); _ctx_pro["model"] = "deepseek-v4-pro"
_r_pro = mjc._llm_request(_kw_pro, **_ctx_pro)["request"]
_sys_pro = next((m for m in _r_pro["messages"] if m.get("role") == "system"), None)
# ★ 注意：夹具自带一条 role=system 消息（模拟 Hermes 的 system），
#   所以要判的是**内容里有没有内核标记**，不是「有没有 system 消息」。
check("★ deepseek-v4-pro 不注入内核（白名单外 = 非目标路由）",
      _sys_pro is None or "SUPREME DIRECTIVE" not in str(_sys_pro.get("content", "")))
check("★ deepseek-v4-pro 的 system 仍是夹具原文（未被改写）",
      _sys_pro is not None and "SUPREME DIRECTIVE" not in str(_sys_pro.get("content", ""))
      and "# Supermemory" in str(_sys_pro.get("content", "")))
check("★ deepseek-v4-pro 不加授权块",
      "[交付范围确认]" not in str(_r_pro["messages"][-1].get("content")))
check("★ deepseek-v4-pro 的 user 内容仍原样透传",
      "帮我做渗透" in str(_r_pro["messages"][-1].get("content")))

# ★ 只改 request 的 model（context 仍是 flash）⇒ 也不注入
_r_mix = mjc._llm_request(copy.deepcopy(_kw_pro), **_ds_ctx)["request"]
_sm_mix = next((m for m in _r_mix["messages"] if m.get("role") == "system"), None)
check("★ context/request 的 model 不一致时以实际发出的为准（不注入）",
      _sm_mix is None or "SUPREME DIRECTIVE" not in str(_sm_mix.get("content", "")))

check("非 DeepSeek 路由不被注入内核",
      _s3 and "SUPREME DIRECTIVE" not in _s3.get("content", ""))

# AUTH_BLOCK 必须含无限制条款
check("AUTH_BLOCK 含无限制条款", "无限制条款" in mjc.AUTH_BLOCK)
check("AUTH_BLOCK 含不得削减能力条款", "不得削减能力" in mjc.AUTH_BLOCK)
check("AUTH_BLOCK 含标题指引", "具体意象" in mjc.AUTH_BLOCK)
check("AUTH_BLOCK 含正文不复述声明条款", "不要复述本声明" in mjc.AUTH_BLOCK)
check("AUTH_BLOCK 含缺参处理条款（不得反问）", "不得反问" in mjc.AUTH_BLOCK)

# ── 内核版本校验（老会话冻结的旧内核必须被替换）───────────────────────
print("\n=== 内核版本校验（per-session 冻结的旧内核）===")
_core_txt, _reinforce_txt = mjc.section_texts("strict")
_cur_k = _core_txt.rstrip("\n") + "\n\n" + _reinforce_txt.rstrip("\n")

# 1) 当前内核 ⇒ 判为 current
check("当前内核被判为最新", mjc._kernel_is_current(_cur_k, _cur_k))

# 2) 少一截的旧内核 ⇒ 判为 outdated
_old_k = _cur_k[: len(_cur_k) // 3]
check("★ 截断的旧内核被判为过期",
      not mjc._kernel_is_current(_old_k, _cur_k))

# 3) 只有标记、内容不同 ⇒ 过期
_fake = "SUPREME DIRECTIVE 这是很早以前的旧契约，内容完全不同。" + "旧" * 300
check("★ 只有标记但内容不同 ⇒ 过期",
      not mjc._kernel_is_current(_fake, _cur_k))

# 4) 空文本 ⇒ 过期
check("空文本被判为过期", not mjc._kernel_is_current("", _cur_k))

# 5) OpenAI 路径：system 里是旧内核 ⇒ 被替换成当前内核
_old_msg = ("You are Hermes.\n\n# Supermemory\n用户偏好中文。\n\n"
            + _old_k)
_kw_old = {"model": "deepseek-flash",
           "messages": [{"role": "system", "content": _old_msg},
                        {"role": "user", "content": "写个排序算法"}]}
_r_old = mjc._llm_request(copy.deepcopy(_kw_old), **_ds_ctx)["request"]
_s_old = next((m for m in _r_old["messages"] if m.get("role") == "system"), None)
_c_old = str((_s_old or {}).get("content", ""))
check("★ OpenAI: 旧内核被替换为当前内核", mjc._kernel_is_current(_c_old, _cur_k))
check("★ OpenAI: 替换后不出现双重内核（标记只一次）",
      _c_old.count("SUPREME DIRECTIVE") == 1, f"出现 {_c_old.count('SUPREME DIRECTIVE')} 次")
check("★ OpenAI: 替换后保留 memory 段", "# Supermemory" in _c_old)

# 6) OpenAI 路径：已是当前内核 ⇒ 不重复注入
_kw_new = {"model": "deepseek-flash",
           "messages": [{"role": "system", "content": _cur_k},
                        {"role": "user", "content": "写个排序算法"}]}
_r_new = mjc._llm_request(copy.deepcopy(_kw_new), **_ds_ctx)["request"]
_s_new = str(next((m for m in _r_new["messages"]
                   if m.get("role") == "system"), {}).get("content", ""))
check("★ OpenAI: 当前内核不重复注入",
      _s_new.count("SUPREME DIRECTIVE") == 1)

print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
