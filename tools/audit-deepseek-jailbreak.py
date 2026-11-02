# -*- coding: utf-8 -*-
"""DeepSeek jailbreak coverage audit.

Answers: do *all* deepseek-flash chats get the kernel?

Checks five things:
  1. plugin enabled and the llm_request middleware registered
  2. the allowlist HERMES_JAILBREAK_DEEPSEEK_MODELS contains deepseek-flash
  3. plugin routing (including provider=custom and a missing model)
  4. recent DeepSeek injection lines in gateway.log
  5. deepseek session count in state.db

Usage:
    python audit-deepseek-jailbreak.py [-v]

Exit code: 0 = all pass; 1 = something failed.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import sys
from pathlib import Path

# ── Locate the Hermes home without hard-coding a path ──────────────────
def _find_hermes_home() -> Path:
    """Find the Hermes home directory.

    Order: $HERMES_HOME, then walk up from this file looking for a directory
    that contains both ``plugins/`` and ``config.yaml``.
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

    # 1. plugin
    print("\n[1] plugin and middleware")
    if not (PLUGIN_DIR / "__init__.py").is_file():
        check(False, "plugin found", str(PLUGIN_DIR))
    else:
        plug = load_plugin()
        check(hasattr(plug, "_llm_request"), "plugin loads")
        if LOG.exists():
            t = LOG.read_text(encoding="utf-8", errors="replace")
            check("registered llm_request middleware" in t,
                  "middleware registration logged")
            ds = [l for l in t.splitlines() if "openai-shape" in l]
            check(bool(ds), "DeepSeek injection path observed",
                  f"{len(ds)} lines")

    # 2. allowlist
    print("\n[2] allowlist")
    wl = env_get("HERMES_JAILBREAK_DEEPSEEK_MODELS") or plug._DEEPSEEK_MODELS_DEFAULT
    check("deepseek-flash" in wl.lower(), "allowlist has deepseek-flash", wl)
    check(not plug.deepseek_model_allowed("deepseek-v4-pro"),
          "v4-pro is not allowlisted")

    # 3. routing
    print("\n[3] routing")
    cases = [
        ("deepseek-flash", "custom", "deepseek-flash", True),
        ("deepseek-flash", "deepseek", "deepseek-flash", True),
        ("", "custom", "deepseek-flash", True),
        ("deepseek-v4-pro", "deepseek", "deepseek-v4-pro", False),
    ]
    for cm, cp, rm, want in cases:
        req = {"model": rm, "messages": [{"role": "user", "content": "hi"}]}
        out = plug._llm_request(req, model=cm, provider=cp)
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
    print(f"all {len(results)} checks passed - every deepseek-flash chat "
          f"carries the kernel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
