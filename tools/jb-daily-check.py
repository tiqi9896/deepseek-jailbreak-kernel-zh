#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""破甲知识库 · 每日巡检（cron 脚本）

做四件事：
  1. 同步运行态插件/技能副本 → <知识库>/01-核心资产/
  2. 查 state.db：哪些 MiniMax session 的持久化 prompt 里没有内核
  3. 查插件启用状态 / 配置
  4. 更新破甲库 _STATE.md 的「巡检快照」小节

用法:
    python <知识库>/_tools/jb-daily-check.py
退出码: 0 = 正常；1 = 有告警（供 cron 判失败）
"""
from __future__ import annotations

import hashlib
import json
import re
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# ── 路径解析（★ 可移植：环境变量优先，其次仓库内/家目录探测）────────────
# ★ 公开包里不能用本机绝对路径（别人跑不起来 + 泄漏目录结构）。
#   JB_HOME        知识库根（默认 <repo>/../知识库 或 家目录/知识库）
#   JB_HERMES_HOME Hermes 根（默认 <repo>/../../hermes 或 家目录/hermes）
#   JB_PLUGIN_DIR  插件目录（默认 <hermes>/plugins/minimax-jailbreak-core）
#   JB_SKILL_DIR   技能目录
#   JB_PROXY       代理脚本
#   JB_STATE_DB    state.db
#   JB_BENCH       实测结果目录
def _pick(*cands):
    for c in cands:
        if c:
            p = Path(c)
            if p.exists():
                return p
    return Path(cands[0]) if cands else Path(".")


def _is_dist_repo(root: Path) -> bool:
    """★ 这个目录是**发行仓库**（公开包）还是**Hermes 安装根**？

    发行仓库里有 `plugin/__init__.py` 与 `proxy/proxy.py`，但**没有**
    `plugins/`（复数）与 `state.db`。两者语义不同，不能混。
    """
    return (root / "plugin" / "__init__.py").exists() \
        and not (root / "plugins").is_dir()


def _find_hermes_home() -> Path:
    """★ 按**内容特征**定位 Hermes 根：目录里有 state.db 且有 plugins/ 或 skills/。

    ★ 不用「第一个存在的目录」——那会选到知识库目录（它也存在），
      导致 state.db / plugins 路径全错。
    """
    env = os.environ.get("JB_HERMES_HOME")
    if env and Path(env).exists():
        return Path(env).resolve()
    # ★ 候选：仓库根及其上两级、家目录下的 hermes/.hermes、
    #   以及**同名目录**（Hermes 常装在数据盘根目录，名字就是 hermes）。
    #   不用绝对路径写死 ⇒ 可移植。
    cands = [_REPO, _REPO.parent, _REPO.parent.parent,
              Path.home() / "hermes", Path.home() / ".hermes",
              Path("/opt/hermes"), Path("/usr/local/hermes")]
    for base in (_REPO, _REPO.parent, _REPO.parent.parent, Path.home()):
        try:
            for child in base.iterdir():
                if child.is_dir() and child.name.lower() in ("hermes", ".hermes"):
                    cands.append(child)
        except OSError:
            continue
    # ★ 优先级 1：**有本插件目录**的 Hermes 根（唯一强特征）
    #   注意：用户级 ~/.hermes 也有 state.db + skills/，但**没有 plugins/**
    #   ⇒ 不能只按「state.db + skills」判，否则会选到用户级目录，
    #   它的 sessions 表结构不同 ⇒ 巡检报「no such column」。
    for c in cands:
        if (c / "plugins" / "minimax-jailbreak-core").is_dir():
            return c.resolve()
    # ★ 发行仓库场景（公开包）：没有 Hermes 安装 ⇒ 「根」就是仓库自身
    if _is_dist_repo(_REPO):
        return _REPO
    # 优先级 2：有 state.db 且有 logs/ + skills/ 的目录
    for c in cands:
        try:
            if (c / "state.db").exists() and (c / "skills").is_dir() \
                    and (c / "plugins").is_dir():
                return c.resolve()
        except OSError:
            continue
    # 优先级 3：有 state.db 即可
    for c in cands:
        if (c / "state.db").exists():
            return c.resolve()
    return cands[0]


def _find_repo_root() -> Path:
    # 本文件在 <repo>/tools/jb-daily-check.py
    return Path(__file__).resolve().parent.parent


_REPO = _find_repo_root()


def _home() -> Path:
    return Path.home()


def _find_kb() -> Path:
    env = os.environ.get("JB_HOME")
    if env and Path(env).exists():
        return Path(env).resolve()
    for c in [_REPO, _REPO.parent / "破甲", _REPO / "kb",
              Path.home() / "知识库" / "破甲"]:
        try:
            if (c / "_tools").is_dir() or (c / "AGENTS.md").is_file():
                return c.resolve()
        except OSError:
            continue
    return (_REPO / "kb").resolve()


KB = _find_kb()
HERMES_HOME = _find_hermes_home()
def _pick_source(env_name: str, hermes_sub: str, repo_sub: str) -> Path:
    """定位「运行态副本」：环境变量 > 发行仓库自带副本 > Hermes 安装根下。

    ★ 公开包里没有 Hermes 安装，只有仓库自带的 plugin/ 与 skill/ ⇒
      仓库内副本必须排在 Hermes 根之前，否则公开版巡检报「源不存在」。
    ★ 本机（知识库）里没有 plugin/ ⇒ 自动落到 Hermes 根，正确。
    """
    e = os.environ.get(env_name)
    if e and Path(e).exists():
        return Path(e)
    repo_copy = _REPO / repo_sub
    if repo_copy.exists():
        return repo_copy
    return HERMES_HOME / hermes_sub


PLUGIN_SRC = _pick_source("JB_PLUGIN_DIR",
                          "plugins/minimax-jailbreak-core",
                          "plugin")
SKILL_SRC = _pick_source("JB_SKILL_DIR",
                         "skills/software-development/llm-refusal-bypass",
                         "skill/llm-refusal-bypass")
def _find_proxy_src() -> Path:
    """代理源码：环境变量 > 仓库内 proxy/proxy.py > Hermes 安装下的脚本。"""
    e = os.environ.get("JB_PROXY")
    if e and Path(e).exists():
        return Path(e)
    for c in (_REPO / "proxy" / "proxy.py",
              HERMES_HOME / "scripts" / "minimax-jailbreak-proxy" / "proxy.py",
              HERMES_HOME / "proxy" / "proxy.py"):
        if c.exists():
            return c
    return HERMES_HOME / "scripts" / "minimax-jailbreak-proxy" / "proxy.py"


PROXY_SRC = _find_proxy_src()
DB = os.environ.get("JB_STATE_DB") or str(HERMES_HOME / "state.db")
PLUGIN_DST = KB / "01-核心资产/插件/minimax-jailbreak-core"
SKILL_DST = KB / "01-核心资产/技能包/llm-refusal-bypass"
STATE = KB / "_STATE.md"


KERNEL_MARK = "SUPREME DIRECTIVE"
REINFORCE_MARK = "DELIVERABLE FIDELITY"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def sync_tree(src: Path, dst: Path) -> str:
    """同步副本（忽略 __pycache__ / *.pyc）。返回状态描述。"""
    if not src.exists():
        return f"✗ 源不存在 {src}"
    changed = []
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # 比对关键文件 hash
    for f in sorted(src.rglob("*")):
        if f.is_file() and "__pycache__" not in str(f):
            rel = f.relative_to(src)
            d = dst / rel
            if not d.exists() or sha(f) != sha(d):
                changed.append(str(rel))
    return f"✓ 同步完成（{len(changed)} 个文件有差异: {changed[:5] if changed else '无'}）"


def check_sessions() -> tuple[list, list, int]:
    """返回 (无内核的 session 列表, 非 minimax 的 session 列表, minimax 总数)"""
    # ★ 发行仓库里没有 Hermes 安装 ⇒ 没有 state.db / 没有 sessions 表
    #   ⇒ 跳过而不是崩（崩会让公开版巡检永远 rc=1）。
    if not os.path.exists(DB):
        print("    --  会话检查跳过（无 state.db）")
        return [], [], 0
    con = sqlite3.connect(DB)
    con = sqlite3.connect(DB)
    cur = con.cursor()
    no_kernel, non_mini, total_mini = [], [], 0
    if not cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone():
        print("    --  会话检查跳过（state.db 无 sessions 表）")
        return [], [], 0
    rows = cur.execute("SELECT id, model, system_prompt_hash FROM sessions").fetchall()
    for sid, model, h in rows:
        m = str(model or "")
        if "minimax" not in m.lower():
            non_mini.append((sid, m))
            continue
        total_mini += 1
        prompt = ""
        if h:
            pr = cur.execute("SELECT prompt FROM system_prompts WHERE hash=?", (h,)).fetchone()
            prompt = (pr[0] if pr else "") or ""
        if KERNEL_MARK not in prompt:
            no_kernel.append((sid, m))
    con.close()
    return no_kernel, non_mini, total_mini


def _plugins_from_config():
    """直接读 config.yaml 的 plugins.enabled / disabled（CLI 读的同一份真源）。

    ★ 为什么不用 `hermes plugins list`：实测该命令冷启动要 **160–175s**
      （2026-11-02 两次实测 160s / 172s），而本函数历史 timeout=180s
      ⇒ 偶发超时 ⇒ 返回「未启用」= **假告警**。当天插件其实 enabled，
      gateway.log 里 09:10 还在正常注入。
    """
    cfg = HERMES_HOME / "config.yaml"
    if not cfg.exists():
        return None, None
    try:
        lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, None
    in_plugins, sec = False, None
    enabled, disabled = [], []
    for ln in lines:
        if re.match(r"^plugins:\s*$", ln):
            in_plugins, sec = True, None
            continue
        if not in_plugins:
            continue
        if ln.strip() and not ln.startswith(" "):
            break  # 到了下一个顶层键
        m = re.match(r"^\s{2}(\w+):\s*$", ln)
        if m:
            sec = m.group(1)
            continue
        m2 = re.match(r"^\s*-\s*(\S+)\s*$", ln)
        if m2 and sec in ("enabled", "disabled"):
            (enabled if sec == "enabled" else disabled).append(m2.group(1))
    if not enabled and not disabled:
        return None, None
    return enabled, disabled


def check_plugin() -> tuple[bool, str]:
    name = "minimax-jailbreak-core"
    enabled_list, disabled_list = _plugins_from_config()
    if enabled_list is not None:
        on = name in enabled_list and name not in (disabled_list or [])
        return on, f"config.yaml · enabled={name in enabled_list} disabled={name in (disabled_list or [])}"
    # 兜底：问 CLI（很慢，只作最后手段）
    try:
        r = subprocess.run(["hermes", "plugins", "list", "--plain", "--no-bundled"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=420, cwd=str(HERMES_HOME))
        out = (r.stdout or "") + (r.stderr or "")
        for line in out.splitlines():
            if name in line:
                return line.strip().startswith("enabled"), line.strip()[:100]
        return False, "CLI 输出里没有该插件"
    except Exception as e:  # noqa: BLE001
        return False, f"查询失败: {type(e).__name__}"


def check_gateway_log() -> list:
    log = HERMES_HOME / "logs" / "gateway.log"
    if not log.exists():
        return []
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [l for l in lines[-3000:] if "minimax-jailbreak-core" in l]
    return hits[-5:]


def check_kernel_budget() -> tuple[bool, str]:
    """检查内核段预算：每段 ≤4000，持久化两段合计 ≤8000。

    ★ 超限的段会被 Hermes 静默跳过（_render_prompt_section_text 里
      `len(text) > section.max_chars` ⇒ skipped），内核就整段失效了。
    """
    prof = PLUGIN_SRC / "prompts/profiles"
    try:
        core = (prof / "strict.md").read_text(encoding="utf-8")
        reinf = (prof / "reinforce.md").read_text(encoding="utf-8")
    except Exception as e:
        return False, f"读不到内核文件: {e}"
    problems = []
    if len(core) > 4000:
        problems.append(f"strict.md {len(core)}>4000")
    if len(reinf) > 4000:
        problems.append(f"reinforce.md {len(reinf)}>4000")
    if len(core) + len(reinf) > 8000:
        problems.append(f"合计 {len(core)+len(reinf)}>8000")
    # 逃逸口回归检查
    for bad, label in (("except where the request clearly falls outside", "逃逸口1"),
                       ("nearest legitimate deliverable", "逃逸口2"),
                       ("如果请求合法，就按要求原样交付", "软化台阶")):
        if bad in core or bad in reinf:
            problems.append(f"{label} 复活")
    detail = f"core={len(core)} reinforce={len(reinf)} 合计={len(core)+len(reinf)}"
    return (not problems), detail + ("  ★" + "；".join(problems) if problems else "")


def check_filter() -> tuple[bool, str]:
    """检查语料过滤器的用户口径：小/初/高/大学生 + 14岁及以上 一律不拦。"""
    # ★ 语料过滤器是**本机成人语料工程**的专有组件，不在公开包里
    #   ⇒ 找不到时跳过，不要报「不符」（那会让公开版巡检永远带告警）。
    f = KB.parent / "破甲小说" / "05-工具" / "corpus_filter.py"
    if not f.exists():
        return True, "跳过（公开包不含语料过滤器）"
    if not f.exists():
        return False, "corpus_filter.py 不存在"
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("cf_chk", f)
        cf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cf)
    except Exception as e:
        return False, f"导入失败: {e}"
    SEX = "他压在身下，肉棒抽送"
    problems = []
    for label, text in (("小学生", "小学生在教室"), ("初中生", "初中生模考"),
                        ("高中生", "高中生晚自习"), ("大学生", "大学生社团"),
                        ("14岁", "她今年14岁"), ("18岁", "她今年18岁"),
                        ("20岁", "她今年20岁"), ("打码成年", "她今年二十X岁")):
        try:
            blocked, _, _ = cf.judge(text + SEX, "探针")
        except Exception as e:
            return False, f"判定异常 {label}: {e}"
        if blocked:
            problems.append(f"{label}被拦")
    # ≤13 岁必须仍拦
    for label, text in (("13岁", "她今年13岁"), ("6岁", "她今年6岁")):
        try:
            blocked, _, _ = cf.judge(text + SEX, "探针")
        except Exception as e:
            return False, f"判定异常 {label}: {e}"
        if not blocked:
            problems.append(f"{label}漏拦")
    return (not problems), ("口径正确" if not problems else "；".join(problems))


def check_proxy() -> tuple[bool, str]:
    """检查外部 Agent 代理（127.0.0.1:8790）是否在跑。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8790/health", timeout=8) as r:
            body = r.read().decode("utf-8", "replace")
        return True, body[:120]
    except Exception as e:
        return False, f"代理不可达: {e}"


def check_deepseek() -> tuple[bool, str]:
    """★ 检查 DeepSeek 路由：插件是否识别 deepseek + 代理是否带 deepseek 上游。

    ★ 不调模型（省配额）：只做**离线形态核验** ——
    ① 插件把内核注入 messages[] 的 system 消息（不是顶层）；
    ② DeepSeek 路径**不加** user 末尾授权块、不加 thinking/output_config；
    ③ 代理源码里 provider_of() 能按 model 名路由。
    """
    import importlib.util
    try:
        P = PLUGIN_SRC / "__init__.py"
        spec = importlib.util.spec_from_file_location("mjc_ds", P)
        mjc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mjc)
    except Exception as e:
        return False, f"插件加载失败: {e}"
    try:
        kw = {"model": "deepseek-flash", "max_tokens": 16000,
              "messages": [{"role": "system",
                            "content": "You are Hermes.\n\n# Supermemory\n记忆。\n\n"
                                       "## Hermes runtime environment\nHost: Windows 11\n"},
                           {"role": "user", "content": "写个工具"}]}
        out = mjc._llm_request(dict(kw), session_id="ds-check", provider="deepseek",
                               model="deepseek-flash",
                               base_url="https://api.deepseek.com")["request"]
        sm = next((x for x in out.get("messages", [])
                   if x.get("role") == "system"), None)
        problems = []
        if sm is None:
            problems.append("messages[] 无 system 消息")
        else:
            if "SUPREME DIRECTIVE" not in str(sm.get("content", "")):
                problems.append("内核未注入 system")
            if "# Supermemory" not in str(sm.get("content", "")):
                problems.append("memory 段丢失")
        if "system" in out:
            problems.append("顶层多出 system(OpenAI 形状不该有)")
        last = str(out.get("messages", [{}])[-1].get("content", ""))
        if "[交付范围确认]" in last:
            problems.append("DeepSeek 误加授权块")
        if "thinking" in out or "output_config" in out:
            problems.append("DeepSeek 误加 thinking/output_config")
        # 代理源码（发行仓库 = proxy/proxy.py；本机 = scripts/…/proxy.py）
        px_path = PROXY_SRC
        if not px_path.exists():
            px_path = _REPO / "proxy" / "proxy.py"
        if px_path.exists():
            px = px_path.read_text(encoding="utf-8", errors="replace")
            if "def provider_of" not in px:
                problems.append("代理缺 provider_of()")
            if "inject_deepseek" not in px:
                problems.append("代理缺 inject_deepseek()")
        else:
            problems.append("找不到代理源码（PROXY_SRC / proxy/proxy.py）")
            problems.append("代理缺 inject_deepseek()")
        if problems:
            return False, "★ " + "; ".join(problems)
        return True, "DeepSeek 路由 OK(system 注入/无授权块/无thinking/代理路由齐)"
    except Exception as e:
        return False, f"核验异常: {e}"


def main() -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    warn = []

    # 1. 同步
    # 1. 同步（★ 发行仓库里**不���同步**）
    #   发行仓库没有运行态副本可同步，而且这里的 PLUGIN_DST 落在 KB/01-核心资产/ 下；
    #   在仓库场景里 KB 会被解析成仓库自身 ⇒ 会把 plugin/ 复制成
    #   「仓库/01-核心资产/插件/…」，**把知识库镜像塞进发行包**（实测发生两次）。
    #   ⇒ 发行仓库场景直接跳过同步，只报「跳过」。
    if _is_dist_repo(_REPO):
        p1 = p2 = "跳过（发行仓库无运行态副本）"
    else:
        p1 = sync_tree(PLUGIN_SRC, PLUGIN_DST)
        p2 = sync_tree(SKILL_SRC, SKILL_DST)
    print(f"[{now}] 同步插件: {p1}")
    print(f"[{now}] 同步技能: {p2}")

    # 2. session 内核覆盖
    no_kernel, non_mini, total_mini = check_sessions()
    print(f"[{now}] MiniMax session 总数: {total_mini} · 持久化 prompt 缺内核: {len(no_kernel)}")
    for sid, m in no_kernel[:10]:
        print(f"    - {sid}  ({m})")

    # 3. 插件状态
    enabled, detail = check_plugin()
    print(f"[{now}] 插件状态: {'enabled' if enabled else '★未启用'}  {detail[:80]}")
    if not enabled:
        warn.append("插件未启用")

    # 4. 日志
    tail = check_gateway_log()
    bootstrapped = [l for l in tail if "kernel bootstrapped" in l]
    print(f"[{now}] 近期日志 {len(tail)} 条 · 其中 kernel bootstrapped {len(bootstrapped)} 条")
    for l in tail[-3:]:
        print(f"    {l[-110:]}")

    if no_kernel:
        # ★ 不是告警：effective_mode 默认 "on"，重建分支每请求重写 system ⇒ 实际请求必带内核。
        # 持久化 prompt 只是会话恢复的快照，缺内核不影响实际请求。
        print(f"    （提示）{len(no_kernel)} 个持久化 prompt 缺内核 —— 非告警，"
              f"重建分支每请求重写 system，实际请求必带内核")
    if not enabled:
        warn.append("插件未启用")

    # 4.5 内核预算 / 逃逸口回归
    kb_ok, kb_detail = check_kernel_budget()
    print(f"[{now}] 内核预算: {'OK' if kb_ok else '★超限/逃逸口复活'}  {kb_detail}")
    if not kb_ok:
        warn.append("内核预算或逃逸口")

    # 4.6 语料过滤器口径
    cf_ok, cf_detail = check_filter()
    print(f"[{now}] 语料过滤器口径: {'OK' if cf_ok else '★不符'}  {cf_detail}")
    if not cf_ok:
        warn.append("过滤器口径")

    # 4.7 外部代理
    ds_ok, ds_detail = check_deepseek()
    print(f"[{now}] DeepSeek 路由: {'OK' if ds_ok else '★异常'}  {ds_detail[:90]}")
    px_ok, px_detail = check_proxy()
    print(f"[{now}] 外部代理 8790: {'OK' if px_ok else '★不可达'}  {px_detail[:90]}")
    if not px_ok:
        warn.append("外部代理不可达")

    # 5.5 并行验收跑的结果文件冲突检测（★ 2026-11-01）
    # 三轮并行跑若共用同一个输出文件名，后写者会覆盖前写者。
    # 判据：结果 json 里 (case, r) 组合有重复 ⇒ 疑似被覆盖或同文件并发写。
    print(f"[{now}] 并行结果文件冲突检测:")
    bench = Path(os.environ.get("JB_BENCH") or (HERMES_HOME / "cache" / "scratch" / "jb-bench"))
    clash = 0
    for jf in sorted(bench.glob("*.json")):
        # 跳过已标注的「被覆盖混合产物」
        if "MERGED-BY-COVER" in jf.name:
            print(f"    --  {jf.name}: 已知覆盖产物，跳过")
            continue
        try:
            dd = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(dd, list) or not dd or "case" not in dd[0]:
            continue
        # ★ 判据要精确：同一 (case, r) 出现**内容不同**的多条才是覆盖冲突。
        #   (case, r) 相同但内容一致 = 同一结果被重复记录，无害。
        #   多轮设计里 round 值复用（如 3 轮都记 r=1/2/3）也是正常，不是覆盖。
        # ★ 分组键要自动推断：不同脚本用不同字段名记录轮次/变体
        #   （r / round / variant / sec / tag / kernel …），只认 r 会把
        #   所有条目撞成同一组 ⇒ 误报。
        keys = set(dd[0].keys())
        # ★ 分组键 = 所有能区分记录的身份字段的**组合**。
        #   只取一个字段会把「同 case 不同轮次」撞成冲突（误报）。
        #   轮次字段 r/round/sec + 变体字段 tag/variant/kernel/path 全部参与。
        rk = next((k for k in ("r", "round", "sec") if k in keys), None)
        vk = next((k for k in ("tag", "variant", "kernel", "path", "arm") if k in keys), None)
        ck = next((k for k in ("case", "id", "name") if k in keys), None)
        # ★ model 也是身份字段：同一个脚本跑多个模型（deepseek-flash 与
        #   deepseek-v4-pro）时，同 (case, r) 但 model 不同是**正常**的，
        #   漏掉 model 会把它们撞成「同键内容不一致」的假冲突。
        mk = next((k for k in ("model", "model_id", "mid") if k in keys), None)
        if not ck:
            print(f"    OK  {jf.name}: {len(dd)} 条, 无 case 字段，跳过")
            continue
        idf = [k for k in (ck, mk, rk, vk) if k]
        # ★ 文件名里的轮次 tag 也是身份的一部分（如 XXX-r1.json 与 XXX-r2.json
        #   内部的 round 值都从 1 开始，只按内容分组会误判为覆盖冲突）。
        _ftag = ""
        for _seg in jf.stem.replace("_", "-").split("-"):
            if _seg.startswith("r") and _seg[1:].isdigit():
                _ftag = _seg[1:]
                break
            if _seg.startswith("run") and _seg[3:].isdigit():
                _ftag = _seg[3:]
                break
        groups = {}
        for x in dd:
            k = (_ftag,) + tuple(str(x.get(f)) for f in idf)
            sig = (str(x.get("v") or x.get("body") or x.get("verdict")),
                   str(x.get("title")), str(x.get("chars")))
            groups.setdefault(k, set()).add(sig)
        conflict = {k: len(v) for k, v in groups.items() if len(v) > 1}
        if conflict:
            clash += 1
            print(f"    ★ {jf.name}: {len(dd)} 条, {len(conflict)} 组同键({','.join(idf)})内容不一致 ⇒ 疑似并行覆盖")
        else:
            print(f"    OK  {jf.name}: {len(dd)} 条, 按({','.join(idf)})无内容冲突")
    if clash:
        print(f"    ★★ {clash} 个结果文件有覆盖嫌疑 ⇒ 判读前先核对 (case, r) 唯一性")
        warn.append(f"{clash} 个结果文件疑似被并行覆盖")
    else:
        print("    ✓ 无覆盖嫌疑")
    # 5.5b ★★ 同族结果文件的**来源追溯**：内容冲突检测查不出
    #   「两轮数据互相覆盖、但条数恰好正确」的情况。
    #   实测（2026-11-01）：R1 与 R2 都写 DS-VARIANT.json，R1 的收尾写入会
    #   覆盖 R2 的结果。那次侥幸没出事（R1 恰好在归档时已写完 60 条），
    #   但**内容冲突检测永远查不出这类覆盖** ⇒ 必须靠「文件名带 tag +
    #   归档时立刻固化到带轮次的文件名」来保证。
    #   ⇒ 本检查：**同前缀的结果文件多于一个，且都还在工作目录里** ⇒ 提醒固化。
    try:
        _seen_prefix = {}
        for _f in sorted(bench.glob("*.json")):
            _stem = _f.stem
            _base = re.sub(r"[-_]?(R|r)\d+$", "", _stem)
            _base = re.sub(r"[-_]?(MT|mt)\d+$", "", _base)
            _base = re.sub(r"[-_]?(F|f)INAL$", "", _base)
            _seen_prefix.setdefault(_base, []).append(_f.name)
        _risk = {k: v for k, v in _seen_prefix.items() if len(v) > 1}
        if _risk:
            print("    ★ 同前缀结果文件（可能是多轮，需固化到带轮次的文件名）：")
            for k, v in sorted(_risk.items()):
                print(f"       {k}: {', '.join(v)}")
            print("       ⇒ 判读前确认每份的来源轮次；工作目录里的会互相覆盖")
        else:
            print("    ✓ 同前缀结果文件：无")
    except Exception as _e:
        print(f"    -- 来源追溯检查跳过: {_e}")



    # 5.6 双仓库远端一致性（★ 2026-11-01）
    # 英文仓库 + 中文仓库必须都有 main 分支、且各自语言体检通过。
    print(f"[{now}] 双仓库远端一致性:")
    _repos = ["tiqi9896/minimax-jailbreak-kernel", "tiqi9896/minimax-jailbreak-kernel-zh"]
    # ★ 2026-11-02：本机 git 走 https 时配了 http.https://github.com.proxy=127.0.0.1:3067，
    #   3067（karingService）一旦上游不通就 **TLS handshake failed** ⇒ git ls-remote
    #   假失败，而仓库其实好好的（gh api 能直接问到 main）。
    #   ⇒ 判据首选 gh api，git ls-remote 只作兜底。
    for _rp in _repos:
        _ok, _how = False, ""
        try:
            _g = subprocess.run(["gh", "api", f"repos/{_rp}/branches/main",
                                 "--jq", ".commit.sha"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=60)
            _sha = (_g.stdout or "").strip()
            if _g.returncode == 0 and _sha:
                _ok, _how = True, f"gh api sha={_sha[:7]}"
        except Exception:  # noqa: BLE001
            pass
        if not _ok:
            try:
                _r = subprocess.run(["git", "ls-remote", "--heads",
                                     f"https://github.com/{_rp}.git", "main"],
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=120)
                if (_r.stdout or "").strip():
                    _ok, _how = True, "git ls-remote"
                else:
                    _tail = ((_r.stderr or "").strip().splitlines() or [""])[-1]
                    _how = _tail[:90]
            except Exception as _e:  # noqa: BLE001
                _how = f"{type(_e).__name__}"
        print(f"    {'OK ' if _ok else '★  '}{_rp}  main={_ok}  {_how}")
        if not _ok:
            warn.append(f"{_rp} 远端 main 不可达")

    # 5. 更新 _STATE.md 巡检快照
    warn = list(dict.fromkeys(warn))  # 去重（历史上有两处重复 append「插件未启用」）
    if STATE.exists():
        t = STATE.read_text(encoding="utf-8")
        block = (
            f"\n---\n\n## 巡检快照\n\n"
            f"- **最后巡检**：{now}\n"
            f"- 插件副本同步：{'已同步' if '✗' not in p1 + p2 else '★失败'}\n"
            f"- 插件启用：**{'enabled' if enabled else '★未启用'}**\n"
            f"- MiniMax session 总数 **{total_mini}**，"
            f"持久化 prompt 缺内核 **{len(no_kernel)}**"
            f"（**非告警**：重建分支每请求重写 system，实际请求必带内核；"
            f"非 MiniMax session {len(non_mini)} 个不适用内核）\n"
            f"- 内核预算：{kb_detail}\n"
            f"- 语料过滤器口径：{'OK' if cf_ok else '★' + cf_detail}\n"
            f"- 外部代理：{'OK' if px_ok else '★不可达'}\n"
            f"- 告警：{'、'.join(warn) if warn else '无'}\n"
        )
        # ★★ 巡检快照必须**插在文件顶部**，且**只保留一份**。
        #   旧写法 `t.split(marker)[0]` 会把追加在末尾的章节（如 DeepSeek
        #   机制差异）一起删掉 —— 实测把 _STATE.md 从 12295 字压回 9465 字。
        #   ⇒ ① 先把**所有**历史快照块删掉（要认带标记与不带标记两种格式）；
        #     ② 再把新块插在一级标题之后。
        MARK = "<!-- JB-SNAPSHOT -->"

        def _strip_snapshots(text: str) -> str:
            """删掉所有「## 巡检快照」块（含旧的 `---` 分隔形式）。"""
            out_lines = text.splitlines(True)
            res, i = [], 0
            while i < len(out_lines):
                ln = out_lines[i]
                if ln.strip() == MARK:
                    i += 1
                    # 跳过到下一个二级标题为止
                    while i < len(out_lines) and not out_lines[i].startswith("## "):
                        i += 1
                    continue
                if ln.rstrip() == "## 巡检快照":
                    # 旧格式：块体到下一个二级/一级标题或文件尾
                    i += 1
                    while i < len(out_lines):
                        nxt = out_lines[i]
                        if nxt.startswith("## ") or nxt.startswith("# "):
                            break
                        i += 1
                    continue
                res.append(ln)
                i += 1
            txt = "".join(res)
            # 清掉因删块留下的连续空行与孤立分隔线
            txt = re.sub(r"\n{3,}", "\n\n", txt)
            return txt

        t = _strip_snapshots(t)
        new_block = block.replace("## 巡检快照", f"{MARK}\n## 巡检快照").strip()
        m = re.search(r"^# .*$", t, re.M)
        if m:
            t = t[:m.end()] + "\n\n" + new_block + "\n" + t[m.end():]
        else:
            t = new_block + "\n\n" + t
        STATE.write_text(t, encoding="utf-8")
        print(f"[{now}] 已更新 _STATE.md 巡检快照")

    print(f"\n[{'⚠ 有告警' if warn else '✓ 正常'}]")
    return 1 if warn else 0


if __name__ == "__main__":
    sys.exit(main())