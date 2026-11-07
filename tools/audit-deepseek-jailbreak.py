# -*- coding: utf-8 -*-
"""DeepSeek jailbreak coverage audit.

Answers: do *all* DeepSeek chats get the kernel?

Checks:
  1. plugin enabled and the llm_request middleware registered
  2. the allowlist admits DeepSeek's **real public model ids**
     (`deepseek-flash` / `deepseek-next` / …) and still excludes the
     denied ones (`deepseek-v4-pro`)
  3. plugin routing (custom provider, deepseek provider, missing model)
  4. recent DeepSeek injection lines in gateway.log
  5. deepseek session count in state.db

Usage:
    python audit-deepseek-jailbreak.py [-v]

Exit code: 0 = all pass; 1 = something failed.

★★ 2026-11-07 fixes (measured, not cosmetic):
   · the tool used to raise `UnboundLocalError: cannot access local variable
     'plug'` whenever the plugin was NOT installed — i.e. exactly when you
     most need the audit. Now it reports a clean FAIL instead of a traceback.
   · the allowlist check asserted `"deepseek-flash" in wl`. That WAS the shape
     of the 2026-11-07 defect - but the *explanation* in the v1.1.0 notes was
     WRONG: `deepseek-flash` is a **real** DeepSeek model id (this repo's own
     measurement data and its own /v1/models listing both use it). The real
     problem was that the allowlist named exactly ONE id, so an agent
     configured with any other DeepSeek model id got a routed-but-uninjected
     request with no error anywhere. The check now asserts that the **real
     model ids** are admitted and that the deny list still excludes
     `deepseek-v4-pro`.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

# ── Locate the Hermes home without hard-coding a path ──────────────────
def _find_hermes_home() -> Path:
    """Find the Hermes home directory.

    Order: $HERMES_HOME, then walk up from this file looking for a directory
    that contains both ``plugins/`` and ``config.yaml``, then ~/.hermes.
    """
    env = os.environ.get("HERMES_HOME", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    for base in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        for cand in (base, base / "hermes"):
            if (cand / "config.yaml").is_file() and (cand / "plugins").is_dir():
                return cand
    return Path.home() / ".hermes"


HERMES = _find_hermes_home()
PLUGIN_DIR = HERMES / "plugins" / "deepseek-jailbreak-core"
LOG = HERMES / "logs" / "gateway.log"
DB = HERMES / "state.db"
ENV = HERMES / ".env"

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
KERNEL_MARKS = ("SUPREME DIRECTIVE", "DELIVERABLE FIDELITY")

# ★ DeepSeek 公开 API 上**真实存在**的 model id。
REAL_PUBLIC_IDS = ("deepseek-flash", "deepseek-next")
# ★ 明确拒绝的（保留当初「不要 v4-pro」的决定）。
DENIED_IDS = ("deepseek-v4-pro",)

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "deepseek_jailbreak_core", PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["deepseek_jailbreak_core"] = mod
    spec.loader.exec_module(mod)
    return mod


def env_get(key: str) -> str:
    if not ENV.exists():
        return ""
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("#") or "=" not in s:
            continue
        if s.split("=", 1)[0].strip() == key:
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main() -> int:
    print("=" * 62)
    print(" DeepSeek jailbreak coverage audit")
    print(f" HERMES_HOME = {HERMES}")
    print("=" * 62)

    plug = None

    # 1. plugin
    print("\n[1] plugin and middleware")
    if not (PLUGIN_DIR / "__init__.py").is_file():
        # ★★ 2026-11-07：以前这里会继续往下走，然后炸在
        #    `UnboundLocalError: cannot access local variable 'plug'`。
        #    插件没装 = 最需要审计的时候 = 审计自己先崩了。
        check(False, "plugin found", str(PLUGIN_DIR))
        print(f"  !! plugin not installed at {PLUGIN_DIR}")
    else:
        try:
            plug = load_plugin()
        except Exception as exc:  # noqa: BLE001
            check(False, "plugin loads", f"{type(exc).__name__}: {exc}")
            print(f"  !! plugin failed to load: {type(exc).__name__}: {exc}")
        else:
            check(True, "plugin loads")
            check(hasattr(plug, "_llm_request"), "plugin exposes _llm_request")
            if LOG.exists():
                t = LOG.read_text(encoding="utf-8", errors="replace")
                check("registered llm_request middleware" in t,
                      "middleware registration logged")
                ds = [l for l in t.splitlines() if "openai-shape" in l]
                check(bool(ds), "DeepSeek injection path observed",
                      f"{len(ds)} lines")

    # 2. allowlist
    print("\n[2] allowlist")
    if plug is None:
        check(False, "allowlist readable", "plugin not loadable")
    else:
        explicit = env_get("HERMES_JAILBREAK_DEEPSEEK_MODELS")
        # ★ 默认（没设 HERMES_JAILBREAK_DEEPSEEK_MODELS）= 允许所有 deepseek* 模型。
        #   显式设了 = 只认那份白名单。
        check(True, "allowlist source resolved",
              f"explicit={explicit!r}" if explicit else "default (all deepseek*)")
        for mid in REAL_PUBLIC_IDS:
            check(plug.deepseek_model_allowed(mid),
                  f"real public model id admitted: {mid}")
        for mid in DENIED_IDS:
            check(not plug.deepseek_model_allowed(mid),
                  f"denied model id excluded: {mid}")
        # ★ 若用户显式写了白名单，它必须真的包含真实 id —— 否则就是 2026-11-07 那个事故。
        if explicit:
            wl = [x.strip().lower() for x in explicit.split(",") if x.strip()]
            check(any(w in ("deepseek-flash", "deepseek-next") or
                      w.startswith("deepseek-flash") for w in wl),
                  "explicit allowlist covers the real public ids", explicit)

    # 3. routing
    print("\n[3] routing")
    if plug is None:
        check(False, "routing checked", "plugin not loadable")
    else:
        cases = [
            ("deepseek-flash", "custom", "deepseek-flash", True),
            ("deepseek-flash", "deepseek", "deepseek-flash", True),
            ("", "custom", "deepseek-flash", True),
            ("deepseek-flash", "deepseek", "deepseek-flash", True),
            ("deepseek-next", "deepseek", "deepseek-next", True),
            ("deepseek-v4-pro", "deepseek", "deepseek-v4-pro", False),
        ]
        for cm, cp, rm, want in cases:
            req = {"model": rm, "messages": [{"role": "user", "content": "hi"}]}
            try:
                out = plug._llm_request(req, model=cm, provider=cp)
            except Exception as exc:  # noqa: BLE001
                check(False, f"ctx({cm or 'empty'},{cp or 'empty'})/{rm}",
                      f"{type(exc).__name__}: {exc}")
                continue
            r = out.get("request", out) if isinstance(out, dict) else out
            sysmsg = next((m.get("content", "") for m in r.get("messages", [])
                           if m.get("role") == "system"), "")
            got = any(k in (sysmsg or "") for k in KERNEL_MARKS)
            check(got == want,
                  f"ctx({cm or 'empty'},{cp or 'empty'})/{rm} -> "
                  f"{'inject' if want else 'skip'}")

    # 4. sessions
    print("\n[4] sessions")
    if DB.exists():
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT id, model FROM sessions WHERE model LIKE '%deepseek%'"
            ).fetchall()
            con.close()
            check(True, "deepseek sessions countable", f"{len(rows)}")
        except Exception as exc:  # noqa: BLE001
            check(False, "deepseek sessions countable", str(exc)[:80])
    else:
        check(True, "state.db absent (skipped)")

    print("\n" + "=" * 62)
    fails = [r for r in results if not r[0]]
    for ok, name, detail in results:
        print(f"  {'OK  ' if ok else 'FAIL'} {name}"
              + (f"  ({detail})" if detail and not ok else ""))
    print("=" * 62)
    if fails:
        print(f"{len(fails)} check(s) failed")
        return 1
    print(f"all {len(results)} checks passed - every DeepSeek chat "
          f"carries the kernel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
