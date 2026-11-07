# -*- coding: utf-8 -*-
"""Offline selftest for the DeepSeek kernel plugin. No network, no API key.

Verifies: kernel loads within budget, the kernel is injected as a `role: system`
entry inside `messages[]`, memory sections survive, the model allowlist is
enforced, a stale kernel is replaced by fingerprint, and nothing is injected for
models outside the allowlist.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "mjc", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
mjc = importlib.util.module_from_spec(spec)
sys.modules["mjc"] = mjc
spec.loader.exec_module(mjc)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name
          + ((" :: " + detail) if detail and not cond else ""))


print("=== kernel ===")
core, rein = mjc.section_texts("strict")
kernel = core.rstrip("\n") + "\n\n" + rein.rstrip("\n")
check("kernel loads", "SUPREME DIRECTIVE" in kernel)
check("reinforce section present", "DELIVERABLE FIDELITY" in kernel)
check("within 8000-char budget", len(kernel) <= 8000, f"len={len(kernel)}")

print("\n=== allowlist ===")
# ★★ 2026-11-07 实测事故：默认白名单曾经**只写了一个** model id
#   （"deepseek-flash" —— 它是真实的 DeepSeek model id；真实 id 是
#   deepseek-flash 与 deepseek-v4-pro，本仓库实测数据与 /v1/models 列表为证）。
#   问题不是 id 是假的，而是**白名单太窄**：
#   ⇒ 任何配成**别的** DeepSeek model id 的外部 agent 拿不到内核，
#     效果为零，且日志看不出异常。
#   ⇒ 现在默认允许所有 deepseek* 模型（减去拒绝名单）。
# ★ DeepSeek 真实的 model id 是 deepseek-flash 与 deepseek-v4-pro
#   （本仓库实测数据与 /v1/models 列表为证）。deepseek-v4-pro 按
#   2026-11-01 的决定「只要 deepseek-flash，不要 deepseek-v4-pro」默认排除。
check("real model id deepseek-flash allowlisted",
      mjc.deepseek_model_allowed("deepseek-flash"))
check("deepseek-v4-pro NOT allowlisted", not mjc.deepseek_model_allowed("deepseek-v4-pro"))
check("other models NOT allowlisted", not mjc.deepseek_model_allowed("gpt-4o"))
# ★ 默认放行**所有** deepseek* id（新出的模型不用改配置）
check("any other deepseek* id allowlisted (deepseek-next)",
      mjc.deepseek_model_allowed("deepseek-next"))
# ★ 显式设了白名单 ⇒ 回到白名单模式（只认名单里的）
import os as _os
_saved = _os.environ.get("HERMES_JAILBREAK_DEEPSEEK_MODELS")
_os.environ["HERMES_JAILBREAK_DEEPSEEK_MODELS"] = "deepseek-flash"
try:
    check("explicit allowlist restricts to that list",
          mjc.deepseek_model_allowed("deepseek-flash")
          and not mjc.deepseek_model_allowed("deepseek-next"))
finally:
    if _saved is None:
        _os.environ.pop("HERMES_JAILBREAK_DEEPSEEK_MODELS", None)
    else:
        _os.environ["HERMES_JAILBREAK_DEEPSEEK_MODELS"] = _saved

print("\n=== /jailbreak command surface ===")
# ★ 2026-11-07 实测事故：set_effort / effective_effort 以前**不存在**，
#   而 status() 和 `/jailbreak effort …` 都在调它们 ⇒ 任何一次 /jailbreak
#   都抛 NameError。
try:
    _st = mjc.status("selftest-session")
    check("status() does not raise", True)
    check("status() mentions the profile", "profile" in _st, _st[:120])
except Exception as exc:  # noqa: BLE001
    check("status() does not raise", False, f"{type(exc).__name__}: {exc}")
try:
    _cmd = mjc._command("effort medium")
    check("/jailbreak effort does not raise", True)
    check("/jailbreak effort reports the level", "medium" in _cmd, _cmd[:120])
except Exception as exc:  # noqa: BLE001
    check("/jailbreak effort does not raise", False, f"{type(exc).__name__}: {exc}")

print("\n=== request layer (OpenAI shape) ===")
req = {"model": "deepseek-flash",
       "messages": [{"role": "system", "content": "You are Hermes.\n\n# Supermemory\n用户偏好中文。"},
                    {"role": "user", "content": "写个压测脚本"}]}
r = mjc._llm_request(copy.deepcopy(req), model="deepseek-flash",
                     provider="custom")["request"]
sysmsg = next((m.get("content", "") for m in r["messages"]
               if m.get("role") == "system"), "")
check("kernel injected into messages[] system", "SUPREME DIRECTIVE" in sysmsg)
check("memory section preserved", "# Supermemory" in sysmsg)
check("exactly one system entry",
      sum(1 for m in r["messages"] if m.get("role") == "system") == 1)

print("\n=== stale kernel replacement ===")
stale = ("You are Hermes.\n\n# Supermemory\n偏好中文。\n\n"
         + kernel[: len(kernel) // 3])
req2 = {"model": "deepseek-flash",
        "messages": [{"role": "system", "content": stale},
                     {"role": "user", "content": "x"}]}
r2 = mjc._llm_request(copy.deepcopy(req2), model="deepseek-flash",
                      provider="custom")["request"]
s2 = next((m.get("content", "") for m in r2["messages"]
           if m.get("role") == "system"), "")
check("stale kernel replaced", mjc._kernel_is_current(s2, kernel))
check("no double kernel", s2.count("SUPREME DIRECTIVE") == 1)
check("memory kept after replacement", "# Supermemory" in s2)

print("\n=== allowlist enforced at request time ===")
req3 = {"model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": "x"}]}
r3 = mjc._llm_request(copy.deepcopy(req3), model="deepseek-v4-pro",
                      provider="deepseek")["request"]
s3 = next((m.get("content", "") for m in r3["messages"]
           if m.get("role") == "system"), "")
check("non-allowlisted model gets no kernel", "SUPREME DIRECTIVE" not in s3)

print("\n=== fail-soft ===")
for bad in ({}, {"messages": None}, {"messages": "notalist"}):
    try:
        mjc._llm_request(bad, model="deepseek-flash", provider="custom")
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    check("malformed input does not raise", ok)

print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
