#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke-test a RUNNING DeepSeek kernel proxy.

    python tests/proxy_smoke.py [--port 8790] [--url http://127.0.0.1:8790]

★ 为什么需要它（2026-11-07 实测事故）：
  代理可以**启动、`/health` 返回 200、`--self-test` 全绿**，而请求路径其实
  是坏的。用户看到的症状往往是「连接被关闭」「没效果」——
  和「代理没启动」「key 不对」完全不一样，很容易查错方向。

  这个脚本对**正在跑的**代理发真请求，区分几种情况：
    ✗ 连不上          → 代理没跑 / 端口不对
    ✗ RemoteDisconnected → 代理在处理请求时崩了
    ✗ 401             → 路径是通的，key 没到位
    ✗ 5xx             → 上游或代理报错，响应体里写了是哪个
    ✓ 拿到结构完整的响应 → 请求路径是通的

★ 它**不需要 API key**：不带鉴权头发一发，看到 401 也算「路径是通的」。

★ 它**顺便验证白名单**：用 DeepSeek 公开 API 的真实 model id
  （`deepseek-chat`）发请求。如果代理日志里 kernel=N，说明白名单没放行
  真实 id —— 那正是 2026-11-07 那个「装完像什么都没装」的根因。

退出码：0 = 代理活着且请求路径通；1 = 有问题。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# ★★ DeepSeek 公开 API 上真实存在的 model id。
#    2026-11-07 事故的根因：默认白名单只有 `deepseek-flash`（Hermes 内部别名），
#    公开 API 上没有这个 id ⇒ 用真实 id 的外部 agent 全都拿不到内核。
REAL_MODEL_IDS = ("deepseek-chat", "deepseek-reasoner")


def probe(url: str, path: str, payload: dict, headers: dict | None = None):
    req = urllib.request.Request(
        url + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), None
    except Exception as exc:
        return None, "", f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(description="DeepSeek kernel proxy smoke test")
    ap.add_argument("--url", default="http://127.0.0.1:8790")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    base = args.url.rstrip("/")
    if args.port:
        base = f"http://127.0.0.1:{args.port}"

    print(f"proxy smoke test → {base}")

    # 1) 健康检查
    try:
        with urllib.request.urlopen(base + "/health", timeout=10) as r:
            health = json.loads(r.read().decode("utf-8", "replace"))
        print(f"  [OK ] /health  {json.dumps(health, ensure_ascii=False)}")
    except Exception as exc:
        print(f"  [FAIL] /health 不可达: {type(exc).__name__}: {exc}")
        print("        ⇒ 代理没在跑，或端口不对。先启动它，再跑本脚本。")
        return 1

    # 2) 无 key —— 期望 401（说明请求进到了处理逻辑，不是断连）
    payload = {"model": REAL_MODEL_IDS[0],
               "messages": [{"role": "user", "content": "ping"}]}
    status, text, err = probe(base, "/v1/chat/completions", payload)
    if err:
        print(f"  [FAIL] 请求异常: {err}")
        print("        ⇒ 代理在处理请求时崩了（不是没启动）。")
        print("        跑 `python proxy/proxy.py --self-test` 看端到端检查项，")
        print("        再看代理日志里的 Traceback。")
        return 1
    if status == 401:
        print("  [OK ] 无 key 请求返回 401 —— 请求路径是通的（key 由 agent 自己带）")
    elif status and 200 <= status < 300:
        print(f"  [OK ] 请求成功 HTTP {status}")
    else:
        print(f"  [FAIL] HTTP {status}: {text[:200]}")
        print("        ⇒ 上游或代理报错，响应体里写了原因。")

    # 3) 带一个假 key —— 能到上游才算真的通
    status, text, err = probe(base, "/v1/chat/completions", payload,
                              {"Authorization": "Bearer smoke-test-not-a-real-key"})
    if err:
        print(f"  [FAIL] 带 key 请求异常: {err}")
        return 1
    if status == 401:
        print("  [OK ] 带 key 请求走到上游并被拒（401）—— 路径通，key 无效属正常")
    elif status and 200 <= status < 300:
        print(f"  [OK ] 带 key 请求 HTTP {status}")
    else:
        print(f"  [WARN] 带 key 请求 HTTP {status}: {text[:200]}")
        print("        ⇒ 上游可能返回了别的错误，看代理日志里的 status= 行。")

    # 4) ★ 白名单检查：用 DeepSeek 公开 API 的真实 model id 各发一发。
    #    它们必须走到**同一个处理路径**（不是 404「模型不支持」）。
    print("\n  白名单检查（DeepSeek 公开 API 的真实 model id）：")
    bad = 0
    for mid in REAL_MODEL_IDS:
        status, text, err = probe(
            base, "/v1/chat/completions",
            {"model": mid, "messages": [{"role": "user", "content": "ping"}]},
            {"Authorization": "Bearer smoke-test-not-a-real-key"})
        if err:
            print(f"    [FAIL] {mid:20s} 请求异常: {err}")
            bad += 1
            continue
        if status == 401:
            print(f"    [OK ] {mid:20s} 走到上游（401 = key 无效，属正常）")
        elif status and 200 <= status < 300:
            print(f"    [OK ] {mid:20s} HTTP {status}")
        elif status == 404:
            print(f"    [FAIL] {mid:20s} HTTP 404 —— 这个 model id 没被代理路由")
            print(f"           ⇒ 检查 JBP_DEEPSEEK_MODELS 白名单（代理侧变量）")
            bad += 1
        else:
            print(f"    [WARN] {mid:20s} HTTP {status}: {text[:160]}")
    if bad:
        print(f"\n  结论：✗ 有 {bad} 项异常 —— 见上面。")
        return 1

    print("\n  结论：代理活着，请求路径通，真实 model id 也能路由。")
    print("  接下来确认**内核是否生效**：看代理日志每行开头的 kernel=Y，")
    print("  以及 injections 计数是否随请求增长。")
    print("  ★ 若日志里 kernel=N ⇒ 白名单没放行该 model id —— "
          "看 AGENTS.md 的「白名单」那一段。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
