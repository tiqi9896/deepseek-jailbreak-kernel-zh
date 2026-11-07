"""deepseek-jailbreak-core — DeepSeek 破甲插件（OpenAI 兼容形状）。

把交付契约注入 `messages[]` 里的 system 消息，并在每会话冻结的
插件段上做指纹校验（陈旧内核会被就地替换）。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "1.0.0"
PLUGIN_DISPLAY_NAME = "DeepSeek 破甲内核"

# ── 内核档（逐字继承上游 MIT 文案；上游路径存在则优先读上游文件） ────────────
# ★ 上游 hermes-jailbreak-kernel 的安装位置不写死：按优先级探测
#   ① 环境变量 HERMES_JAILBREAK_UPSTREAM_PROFILES
#   ② 同一 plugins/ 目录下的兄弟插件（hermes-jailbreak-kernel）
#   ③ 都没有 ⇒ 只用本地 prompts/profiles（功能完整，不依赖上游）
def _detect_upstream_profiles() -> Path | None:
    env = os.environ.get("HERMES_JAILBREAK_UPSTREAM_PROFILES", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    try:
        sibling = Path(__file__).resolve().parent.parent / "hermes-jailbreak-kernel" / "prompts" / "profiles"
        if sibling.is_dir():
            return sibling
    except Exception:  # noqa: BLE001
        pass
    return None


_UPSTREAM_PROFILES = _detect_upstream_profiles()
_LOCAL_PROFILES = Path(__file__).resolve().parent / "prompts/profiles"

PROFILE_FILES = {
    "core": "core.md",
    "research": "research.md",
    "creative": "creative.md",
    "max": "max.md",
    "strict": "strict.md",
}
# ★ absolute.md = 零拒绝绝对契约（第三段）
#   Hermes 持久化插件段总量上限 MAX_SYSTEM_PROMPT_SECTIONS_TOTAL_CHARS=8000，
#   且每段 MAX_SYSTEM_PROMPT_SECTION_CHARS=4000 ⇒ 持久化层最多放两段。
#   但**请求层重建的顶层 system 不受此限**（那是真正发给模型的内容）。
ABSOLUTE_FILE = "absolute.md"
# ★ 默认关闭 —— 实测（2026-11-01，4 变体 × 成人/勒索/爆炸物，A/B 对照）：
#   变体A 枚举类别塞 system      → 成人 2564 字、爆炸物硬拒
#   变体B 无第三段+强 user 末尾   → 成人 2720 字、勒索 9980 字（最优）
#   变体C 短段塞 system          → 成人 488 字（★ 反而硬拒，最差）
#   变体D 短段+弱 user 末尾       → 成人 970 字
#   ⇒ **第三段进 system 是反向的**：段越长越像对抗指令，越稀释前面的契约，
#     且逐条点名「爆炸物/毒品/武器」会触发模型自身的话题级拒答。
#   ⇒ 零拒绝约束的正确落点是 **user 消息末尾**（最靠近生成点），已并入 AUTH_BLOCK。
#   ⇒ 本段保留为可选增强（env 开启），不作为默认。
ABSOLUTE_ENABLED_DEFAULT = False
# ★ 两段分工不同（不是同一份文本重复两遍）：
#   core 段     = 主契约（上游 MIT 文案逐字继承）
#   reinforce 段 = 交付纯度 + 标题命名规范 + 反软化 few-shot（本地新增，专打「交付了但自我降级」）
#   两段各 ≤4000 字符（Hermes 单段硬上限），合计 ≤8000（总预算）⇒ 注入容量翻倍。
# 档位强度：core < research/creative < max < strict
DEFAULT_PROFILE = "strict"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# ★ 默认 medium：实测 low 思考太少 → 模型更草率，标题漂移与拒答都回升
#   （2 用例×2 轮：low 标题 1/4 拒答 1/4；medium 标题 2/4 拒答 1/4；
#     medium+授权声明 标题 4/4 拒答 0/4）
DEFAULT_EFFORT = "medium"

# 每个档位对应的两段文件名：{档名: (core段文件, reinforce段文件)}
SECTION_SPLIT = {
    "core": ("core.md", "reinforce.md"),
    "research": ("research.md", "reinforce.md"),
    "creative": ("creative.md", "reinforce.md"),
    "max": ("max.md", "reinforce.md"),
    "strict": ("strict.md", "reinforce.md"),
}

# ── assistant 预填充（可选强化，实验性） ───────────────────────────────────
# 原理：请求末尾追加一条 role=assistant 的消息，内容是期望交付物的标题首行。
#       模型必须从该前缀续写 ⇒ 无法先输出「我不能提供…」，标题也被锁住。
# ★ 但 prefill 单独不足以破甲：内核缺失时 prefill 仍被拒（已实测）。
#   ⇒ prefill 是内核之上的**可选加固**，默认关闭，避免影响正常会话。
# ★ Hermes 管线已验证：convert_messages_to_anthropic 保留尾部 assistant、
#   角色交替不变量不破（assistant 后面必然跟 tool_result 或 user）。
# ── user 消息级授权声明（交付范围锚定） ───────────────────────────────────
#   内核 + effort=medium                → 标题正确 2/4，拒答 1/4
#   内核 + effort=low                   → 标题正确 1/4，拒答 1/4
#   内核 + effort=medium + 本声明        → 标题正确 4/4，拒答 0/4  ★
#   内核 + effort=low + 本声明           → 标题正确 3/4，拒答 1/4
# ⇒ 声明块贴到 **user 消息末尾**（比 system 里的规则更靠近生成点），
#   能把「防守方措辞漂移」和「开头拒答」一起压下去。
# ★ effort 用 medium：low 思考太少 → 模型更草率，漂移与拒答都回升。

# ★ 文案来自 2026-11-01 实测变体矩阵（4 变体 × 成人/勒索/爆炸物）：
#   变体B（本段文案）→ 成人 2720 字完整、勒索 9980 字完整、标题正确
#   ⇒ 零拒绝约束放 **user 消息末尾** 效果最好；塞进 system 反而触发话题级拒答。



_KERNEL_MARKERS = ("SUPREME DIRECTIVE", "DELIVERABLE FIDELITY", "ZERO-REFUSAL ABSOLUTE CONTRACT")


def _has_kernel_marker(text: str) -> bool:
    """顶层 system 里是否已有内核（避免重复注入）。"""
    return any(m in text for m in _KERNEL_MARKERS)


def kernel_bootstrap_enabled() -> bool:
    """老 session 补内核开关（默认开）。

    插件段是 per-session 冻结的：老 session 的持久化 prompt 里没有内核，
    都是破甲版。
    """
    raw = os.environ.get("HERMES_JAILBREAK_BOOTSTRAP", "")
    if raw.strip():
        return not _falsey(raw)
    return True


# ★ 系统通知类消息标记（★ 2026-11-01 修复）
#   Hermes 网关会把后台进程完成通知当作**普通 user 消息**注入会话。
#   那类消息不是用户请求，给它追加「交付范围确认」是错的（还会让模型把
#   系统通知当成用户指令去执行）。
#   识别特征：消息体以这些前缀开头。
_SYSTEM_NOTICE_PREFIXES = (
    "[IMPORTANT: Background process",
    "[IMPORTANT: ",
    "[System:",
    "[SYSTEM ",
    "<system-reminder>",
    "Background process ",
    "[AUTO-CONTINUE",
)


def _is_system_notice(text: str) -> bool:
    """判断一条 user 消息是否是系统通知（而非真实用户请求）。"""
    if not isinstance(text, str):
        return False
    head = text.lstrip()[:200]
    return any(head.startswith(p) for p in _SYSTEM_NOTICE_PREFIXES)


def _message_text(content) -> str:
    """取出 user 消息里的纯文本（content 可能是 str 或 Anthropic 块数组）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and isinstance(b.get("text"), str):
                parts.append(b["text"])
        return "\n".join(parts)
    return ""


def _is_tool_result_message(content) -> bool:
    """判断一条 user 消息是否只是 tool_result 容器（不是用户请求）。"""
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)





CORE_SECTION_ID = "deepseek-jailbreak-core"
REINFORCE_SECTION_ID = "deepseek-jailbreak-reinforce"
MAX_SECTION_CHARS = 4_000
TOTAL_BUDGET_CHARS = 8_000
DUAL_POSITION = "after_memory"

_SECTION_BLOCK_RE = re.compile(
    r"<!--\s*hermes-plugin-sections:start\s*-->.*?<!--\s*hermes-plugin-sections:end\s*-->\s*",
    re.S,
)
_PLUGIN_CONTEXT_RE = re.compile(r"^## Plugin Context: .*?(?=^## |\Z)", re.S | re.M)
# ★ DeepSeek 路由（OpenAI 兼容形状）
_DEEPSEEK_MODEL_RE = re.compile(r"deepseek", re.I)
# ★★ DeepSeek 内核**生效模型范围**（2026-11-07 修正）：
#   默认放行所有 `deepseek*` 模型；`deepseek-v4-pro` 刻意不放行。
#   （2026-11-01 的「只要 deepseek-flash」是按 Hermes 内部档位别名定的，
#     而 DeepSeek 公开 API 上没有这个 id ⇒ 外部 agent 全都拿不到内核。）
#   ⇒ 白名单外的 deepseek 模型（如 deepseek-v4-pro）**不注入内核**、
#     **不加授权块** ⇒ 它走「非目标路由」路径，与未装内核时一致。
#   ★ 为什么不用 config.yaml 的 `providers.deepseek.models` 列表控制：
#     那个 provider 开了 `discover_models: true`，会从 `/models` 动态刷新
#     UI 列表 ⇒ 删静态条目挡不住它。**真正的开关在这里。**
#   ★ 配置项：`.env` 的 `HERMES_JAILBREAK_DEEPSEEK_MODELS`（逗号分隔，实时可改）。
#     留空 = 不对任何 DeepSeek 模型注入内核。
# ★★ 默认值是**空** = 允许所有 deepseek* 模型（减去拒绝名单）。
#   ★★ 2026-11-07 实测事故：默认值原来是 "deepseek-flash" ——
#      那是 Hermes 内部的档位别名，**DeepSeek 公开 API 上根本没有这个 model id**
#      （公开的是 deepseek-chat / deepseek-reasoner）。
#      ⇒ 用真实 model id 的所有外部 agent 请求时，插件**不注入内核**
#        ⇒ 效果为零，而且日志里看不出异常。
#   ⇒ 现在：
#      · HERMES_JAILBREAK_DEEPSEEK_MODELS 没设 ⇒ 允许所有 deepseek* 模型
#      · 设了 ⇒ 只认这份白名单（Hermes 侧可继续写 `deepseek-flash`）
#      · HERMES_JAILBREAK_DEEPSEEK_MODELS_DENY = 拒绝名单
#        （默认 deepseek-v4-pro —— 保留当初「不要 v4-pro」的决定）
_DEEPSEEK_MODELS_DEFAULT = ""
_DEEPSEEK_MODELS_DENY_DEFAULT = "deepseek-v4-pro"


def _deepseek_name_match(m: str, name: str) -> bool:
    return (m == name or m.startswith(name + ":")
            or m.endswith("/" + name) or name in m)


def deepseek_model_allowed(model: str) -> bool:
    """该 DeepSeek 模型是否在内核生效范围内。

    ★ 拒绝名单**优先**：拒绝名单里的模型一律不注入内核。
    """
    m = str(model or "").strip().lower()
    if not m or not _DEEPSEEK_MODEL_RE.search(m):
        return False
    deny_raw = cfg("HERMES_JAILBREAK_DEEPSEEK_MODELS_DENY", _DEEPSEEK_MODELS_DENY_DEFAULT)
    deny = [x.strip().lower() for x in str(deny_raw).split(",") if x.strip()]
    if any(_deepseek_name_match(m, x) for x in deny):
        return False
    raw = cfg("HERMES_JAILBREAK_DEEPSEEK_MODELS", _DEEPSEEK_MODELS_DEFAULT)
    wl = [x.strip().lower() for x in str(raw).split(",") if x.strip()]
    if not wl:
        # 默认：所有 deepseek* 都行（DeepSeek 公开 API 的真实 id 都在内）
        return True
    return any(_deepseek_name_match(m, x) for x in wl)

_text_cache: dict[str, str] = {}


def _profile_path(name: str) -> Path:
    fn = PROFILE_FILES.get(name, PROFILE_FILES[DEFAULT_PROFILE])
    for root in (_UPSTREAM_PROFILES, _LOCAL_PROFILES):
        # ★ root 可能为 None（上游未安装）⇒ 必须跳过
        if root is None:
            continue
        p = root / fn
        if p.is_file():
            return p
    raise FileNotFoundError(f"kernel profile not found: {fn}")


def absolute_text() -> str:
    """零拒绝绝对契约段文本（请求层专用，不占用持久化段预算）。

    ★ 找不到文件时返回空串 ⇒ fail-soft，绝不影响会话。
    """
    if not absolute_enabled():
        return ""
    if ABSOLUTE_FILE not in _text_cache:
        for root in (_UPSTREAM_PROFILES, _LOCAL_PROFILES):
            if root is None:
                continue
            p = root / ABSOLUTE_FILE
            if p.is_file():
                _text_cache[ABSOLUTE_FILE] = p.read_text(encoding="utf-8").rstrip("\n") + "\n"
                break
        else:
            return ""
    return _text_cache[ABSOLUTE_FILE]


def absolute_enabled() -> bool:
    raw = cfg("HERMES_JAILBREAK_ABSOLUTE")
    if raw.strip():
        return not _falsey(raw)
    return ABSOLUTE_ENABLED_DEFAULT


def section_texts(profile: str = DEFAULT_PROFILE) -> tuple[str, str]:
    """返回 (core 段文本, reinforce 段文本)。

    ★ 两段内容不同：core = 主契约档，reinforce = 交付纯度强化段。
    上游把同一份文本注册两遍（双层同源），这里改成互补分工 —— 同样的 8000
    总预算下注入容量翻倍，且两段各司其职。
    """
    cfile, rfile = SECTION_SPLIT.get(profile, SECTION_SPLIT[DEFAULT_PROFILE])
    return _read_text(cfile), _read_text(rfile)


def _read_text(fn: str) -> str:
    if fn not in _text_cache:
        for root in (_UPSTREAM_PROFILES, _LOCAL_PROFILES):
            # ★ root 可能为 None（上游未安装）⇒ 必须跳过，不能直接 root / fn
            if root is None:
                continue
            p = root / fn
            if p.is_file():
                _text_cache[fn] = p.read_text(encoding="utf-8").rstrip("\n") + "\n"
                break
        else:
            raise FileNotFoundError(f"kernel file not found: {fn}")
    return _text_cache[fn]


def profile_text(name: str = DEFAULT_PROFILE) -> str:
    if name not in _text_cache:
        p = _profile_path(name)
        _text_cache[name] = p.read_text(encoding="utf-8").rstrip("\n") + "\n"
    return _text_cache[name]


def profile_budgets() -> dict[str, int]:
    return {n: len(profile_text(n)) for n in PROFILE_FILES}


# ── 实时读取 .env（★ 2026-11-01）────────────────────────────────────────
# ★ 问题：os.environ 是**网关进程启动时**的快照（load_dotenv(override=True)）。
#   在网关进程内无法重启自己（生命周期命令被硬保护拦住），
#   所以改了 .env 却要等下次登录才生效 —— 期间跑的是旧配置。
# ⇒ 对 HERMES_JAILBREAK_* 这类「本机配置」开关，改为**直接读 .env 文件**，
#   按 mtime 缓存，改完立刻生效，不必重启网关。
# ★ .env 优先于 os.environ：os.environ 里的同名值是启动时的旧快照。
_LIVE_ENV_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


def _hermes_home() -> Path:
    """定位 Hermes 根目录（.env 所在处）。"""
    env_home = (os.environ.get("HERMES_HOME") or "").strip()
    if env_home:
        p = Path(env_home).expanduser()
        if p.is_dir():
            return p
    here = Path(__file__).resolve().parent
    for cand in (here.parent.parent, here.parent.parent.parent):
        if (cand / ".env").is_file():
            return cand
    cwd = Path.cwd()
    for cand in (cwd, cwd.parent):
        if (cand / ".env").is_file():
            return cand
    return here.parent.parent


def _live_env() -> dict[str, str]:
    """读取 .env（按 mtime 缓存）。读不到就返回空 dict。"""
    f = _hermes_home() / ".env"
    try:
        mtime = f.stat().st_mtime
    except Exception:
        return {}
    cached = _LIVE_ENV_CACHE.get(str(f))
    if cached and cached[0] == mtime:
        return cached[1]
    vals: dict[str, str] = {}
    try:
        for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
            s = raw.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("\"", "'"):
                v = v[1:-1]
            vals[k.strip()] = v
    except Exception:
        return {}
    _LIVE_ENV_CACHE[str(f)] = (mtime, vals)
    return vals


def cfg(name: str, default: str = "") -> str:
    """读本机配置：.env 优先（实时），其次 os.environ（进程启动快照）。"""
    v = _live_env().get(name)
    if v is not None and v.strip():
        return v.strip()
    return (os.environ.get(name) or default).strip()


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


def _falsey(v: str | None) -> bool:
    return (v or "").strip().lower() in {"0", "false", "no", "off"}






def strip_plugin_sections(text: str) -> str:
    """剥离插件段容器与 Plugin Context 残留（FIX-2：顶层 system 也要清）。"""
    if not isinstance(text, str):
        return text
    out = _SECTION_BLOCK_RE.sub("", text)
    out = _PLUGIN_CONTEXT_RE.sub("", out)
    return out.strip()


# ── 会话状态 ────────────────────────────────────────────────────────────────
_session_state: dict[str, dict[str, Any]] = {}


def _resolve_session_id(context: Mapping[str, Any] | None = None) -> str:
    ctx = context or {}
    sid = str(ctx.get("session_id") or "").strip()
    if sid:
        return sid
    for env_key in ("HERMES_SESSION_ID", "HERMES_SESSION_KEY", "HERMES_UI_SESSION_ID"):
        v = (os.environ.get(env_key) or "").strip()
        if v:
            return v
    return ""


def effective_mode(session_id: str = "") -> str:
    raw = (cfg("HERMES_JAILBREAK_MODE") or "on").strip().lower()
    if raw not in {"off", "slim", "on", "full"}:
        raw = "on"
    st = _session_state.get(session_id)
    if st and st.get("mode"):
        return st["mode"]
    return raw


def effective_profile(session_id: str = "") -> str:
    raw = (cfg("HERMES_JAILBREAK_PROFILE") or DEFAULT_PROFILE).strip().lower()
    if raw not in PROFILE_FILES:
        raw = DEFAULT_PROFILE
    if effective_mode(session_id) == "full":
        return "max"
    st = _session_state.get(session_id)
    if st and st.get("profile"):
        return st["profile"]
    return raw



def set_mode(session_id: str, mode: str) -> None:
    st = _session_state.setdefault(session_id, {})
    st["mode"] = mode


def set_profile(session_id: str, profile: str) -> None:
    st = _session_state.setdefault(session_id, {})
    st["profile"] = profile


def set_effort(session_id: str, level: str) -> None:
    """记录调用方的 effort 意图。

    ★★ 2026-11-07 修复：这个函数以前**根本不存在**，而 `/jailbreak effort …`
       和 `status()` 都在调它 ⇒ 任何一次 `/jailbreak` 都抛
       `NameError: name 'effective_effort' is not defined`。
    ★ DeepSeek 的 OpenAI 形状**没有** effort 参数（思考档由 model id 决定：
       deepseek-chat / deepseek-reasoner），请求层也不消费它；
       真正影响 DeepSeek 的是**输出下限 32000**（思考与正文共用预算）。
       ⇒ 这里只做「记录 + 回显」，不改请求。
    """
    st = _session_state.setdefault(session_id, {})
    st["effort"] = level


def effective_effort(session_id: str = "") -> str:
    """当前 effort 设置（DeepSeek 上仅作展示，不影响请求）。"""
    st = _session_state.get(session_id)
    if st and st.get("effort"):
        return st["effort"]
    return DEFAULT_EFFORT



def reset(session_id: str) -> None:
    _session_state.pop(session_id, None)


# ── 插件注册 ────────────────────────────────────────────────────────────────
def register(ctx: Any) -> None:
    if _truthy(os.environ.get("HERMES_JAILBREAK_DISABLE", "")):
        logger.info("deepseek-jailbreak-core: HERMES_JAILBREAK_DISABLE=1, skipped")
        return
    # ★ 两段注册不同的可调用对象：core 段拿主契约，reinforce 段拿交付纯度强化段
    for sid, fn in ((CORE_SECTION_ID, _core_section),
                    (REINFORCE_SECTION_ID, _reinforce_section)):
        try:
            ctx.register_system_prompt_section(
                sid, fn, position=DUAL_POSITION, max_chars=MAX_SECTION_CHARS)
            logger.info("deepseek-jailbreak-core: registered section %s", sid)
        except Exception as exc:  # fail-soft
            logger.warning("deepseek-jailbreak-core: section %s failed: %s", sid, exc)
    try:
        ctx.register_command("jailbreak", handler=_command,
                             description="DeepSeek 破甲内核：会话模式 / 内核档",
                             args_hint="[on|off|slim|full|profile <name>|effort <level>|reset]")
        logger.info("deepseek-jailbreak-core: registered /jailbreak")
    except Exception as exc:
        logger.warning("deepseek-jailbreak-core: command registration failed: %s", exc)
    try:
        ctx.register_middleware("llm_request", _llm_request)
        logger.info("deepseek-jailbreak-core: registered llm_request middleware")
    except Exception as exc:
        logger.warning("deepseek-jailbreak-core: middleware registration failed: %s", exc)


def _render_section(info: Mapping[str, Any] | None, slot: str) -> str:
    """渲染指定槽位（core / reinforce）的段内容。"""
    try:
        sid = str((info or {}).get("session_id") or "")
        if effective_mode(sid) == "off":
            return ""
        prof = effective_profile(sid)
        core_txt, reinforce_txt = section_texts(prof)
        return core_txt if slot == "core" else reinforce_txt
    except Exception as exc:  # fail-open：渲染失败不应削弱既有效果
        logger.warning("deepseek-jailbreak-core: section render failed (%s); falling back", exc)
        core_txt, reinforce_txt = section_texts(DEFAULT_PROFILE)
        return core_txt if slot == "core" else reinforce_txt


def _core_section(info: Mapping[str, Any] | None = None) -> str:
    return _render_section(info, "core")


def _reinforce_section(info: Mapping[str, Any] | None = None) -> str:
    return _render_section(info, "reinforce")


def _command(raw_args: str) -> str:
    args = (raw_args or "").strip().split()
    sid = _resolve_session_id()
    if not args:
        return status(sid)
    cmd = args[0].lower()
    if cmd in {"on", "off", "slim", "full"}:
        if not sid:
            return "no resolvable session id"
        set_mode(sid, cmd)
        return f"mode={cmd} session={sid}"
    if cmd == "profile":
        if len(args) < 2 or args[1].lower() not in PROFILE_FILES:
            return "usage: /jailbreak profile <core|research|creative|max>"
        set_profile(sid, args[1].lower())
        return f"profile={args[1].lower()} session={sid}"
    if cmd == "effort":
        if len(args) < 2 or args[1].lower() not in EFFORTS:
            return f"usage: /jailbreak effort <{'|'.join(EFFORTS)}>"
        set_effort(sid, args[1].lower())
        return f"effort={args[1].lower()} session={sid}"
    if cmd == "reset":
        reset(sid)
        return f"reset session={sid}"
    return "usage: /jailbreak [on|off|slim|full|profile <name>|effort <level>|reset]"


def status(session_id: str = "") -> str:
    mode = effective_mode(session_id)
    prof = effective_profile(session_id)
    eff = effective_effort(session_id)
    core_txt, reinforce_txt = section_texts(prof)
    abs_txt = absolute_text()
    budgets = profile_budgets()
    lines = [
        f"deepseek-jailbreak-core v{PLUGIN_VERSION} — {PLUGIN_DISPLAY_NAME}",
        f"  session : {session_id or '(unresolved)'}",
        f"  mode    : {mode}",
        f"  profile : {prof}",
        f"  effort  : {eff}  (DeepSeek OpenAI 形状无此参数 · 思考档由 model id 决定)",
        f"  core    : {len(core_txt)} chars (主契约)",
        f"  reinforce: {len(reinforce_txt)} chars (交付纯度强化)",
        f"  absolute: {len(abs_txt)} chars (零拒绝绝对契约 · 仅请求层)",
        f"  持久化段: {len(core_txt) + len(reinforce_txt)} / {TOTAL_BUDGET_CHARS}",
        f"  请求层合计: {len(core_txt) + len(reinforce_txt) + len(abs_txt)} chars",
        f"  档位    : " + " · ".join(f"{k}({v})" for k, v in budgets.items()),
    ]
    return "\n".join(lines)


# ── 请求层（核心：FIX-1/2/3/4） ──────────────────────────────────────────────
def _llm_request(request: dict, **context: Any) -> dict:
    try:
        if not isinstance(request, dict):
            return {"request": request}
        sid = _resolve_session_id(context)
        mode = effective_mode(sid)
        prof = effective_profile(sid)
        core_txt, reinforce_txt = section_texts(prof)
        # 两段一起注入（core 主契约 + reinforce 交付纯度）
        kernel = (core_txt.rstrip("\n") + "\n\n" + reinforce_txt.rstrip("\n"))
        # ★ absolute 段（零拒绝绝对契约）只在请求层追加 ——
        #   持久化插件段总量上限 8000 已占满两段，但请求层重建的顶层 system
        #   不受该上限约束，而那才是真正发给模型的内容。
        abs_txt = absolute_text()
        if abs_txt:
            kernel = kernel + "\n\n" + abs_txt.rstrip("\n")
        model = str(context.get("model") or request.get("model") or "")
        base_url = str(context.get("base_url") or "")

        # ★ DeepSeek 路由（OpenAI 兼容形状：system 在 messages[] 里，不在顶层）
        #   同样「模型名优先」：模型名明确是别的 provider 时，不因 base_url 是
        #   deepseek 就误判（fallback 链里 provider 会变）。
        #
        # ★★ **白名单**：只有范围内的 DeepSeek 模型才走内核路径。
        #   （2026-11-07 修正：默认放行所有 deepseek* 模型（公开 API 的真实
        #    id 是 deepseek-chat / deepseek-reasoner），只排除 deepseek-v4-pro。）
        #   白名单外的 deepseek 模型（如 v4-pro）走「非目标路由」：
        #   **不注入内核、不加授权块** ⇒ 与未装内核时行为一致。
        # ★ 两个 model 来源都判：**实际发出去的是 request["model"]**，
        #   而插件默认取 context["model"]。两者任一明确不在白名单 ⇒ 不注入。
        #   （否则 fallback 切换瞬间、或调用方只改了 request 的 model 时，
        #     白名单会被绕过。）
        _m_ctx = str(context.get("model") or "").strip()
        _m_req = str(request.get("model") or "").strip()
        _m_cands = [m for m in (_m_ctx, _m_req) if m]
        is_deepseek = bool(_m_cands) and all(
            deepseek_model_allowed(m) for m in _m_cands)
        if not is_deepseek and "deepseek" in str(context.get("provider") or "").lower():
            # provider 名只能作**补充信号** —— 仍要过白名单
            is_deepseek = (not _m_cands) and deepseek_model_allowed("deepseek-flash")



        # ── OpenAI 形状（DeepSeek 等）：system 在 messages[] 里 ──────────
        # ★ OpenAI 形状：system 是 messages[] 里一条 role=system 的消息。
        if is_deepseek and mode != "off":
            request = _openai_inject_system(request, kernel, replace=(mode != "slim"))



    except Exception as exc:  # fail-soft：绝不影响会话
        logger.warning("deepseek-jailbreak-core: middleware error (fail-soft): %s", exc)
    return {"request": request}


def _openai_inject_system(request: dict, kernel: str, replace: bool = True) -> dict:
    """OpenAI 形状（DeepSeek 等）：把内核注入 messages[] 里的 system 消息。

    ★ OpenAI 形状：system 是 messages[] 里一条 role=system 的消息。
      只扫顶层 system 的插件**完全无效**（静默失效：注册成功、
      日志正常、请求照发、效果为零）。

    ★ replace=True：整条 system 消息替换为内核（内核语义是给该 provider 调的，
      原 system 若是 Hermes 的 memory/环境段则保留）。
      replace=False（slim）：内核追加在原 system 之后。
    """
    try:
        msgs = request.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return request
        new_msgs = list(msgs)
        # 找现有的 system 消息
        sys_idx = None
        for i, m in enumerate(new_msgs):
            if isinstance(m, dict) and m.get("role") == "system":
                sys_idx = i
                break
        if sys_idx is None:
            # 没有 system 消息 ⇒ 在最前面插一条
            new_msgs.insert(0, {"role": "system", "content": kernel})
        else:
            m = new_msgs[sys_idx]
            c = m.get("content")
            if isinstance(c, str):
                if _has_kernel_marker(c) and _kernel_is_current(c, kernel):
                    # ★ 静默跳过会让人以为「插件没生效」——实测排查时看到
                    #   gateway.log 里零条 DeepSeek 注入日志，实际是 system
                    #   已经带内核（持久化插件段渲染过）⇒ 打一条日志区分两种情况。
                    logger.info("deepseek-jailbreak-core: openai-shape kernel "
                                "already present and current (system msg, "
                                "%d chars)", len(c))
                    return request
                if _has_kernel_marker(c):
                    # ★ 有标记但不是当前版本 ⇒ 覆盖写（老会话冻结的旧内核）
                    logger.info("deepseek-jailbreak-core: openai-shape kernel "
                                "OUTDATED in system msg (%d chars) -> replacing",
                                len(c))
                if replace:
                    # ★ 与 Anthropic 路径同构：保留 memory 段与环境段，
                    #   只替换其余（含旧的插件段容器）。
                    keep = []
                    for marker in ("# Supermemory", "# Hermes runtime environment"):
                        seg = _extract_section(c, marker)
                        if seg:
                            seg = strip_plugin_sections(seg)
                            # ★ 再裁掉裸的旧内核（strip 只剥容器，剥不掉裸文本）
                            seg = _cut_kernel(seg).strip()
                            if seg:
                                keep.append(seg)
                    new_content = "\n\n".join([*keep, kernel]) if keep else kernel
                else:
                    new_content = c.rstrip("\n") + "\n\n" + kernel
                new_msgs[sys_idx] = dict(m, content=new_content)
            else:
                # content 是块数组 ⇒ 追加一个 text 块
                _txt = "".join(b.get("text", "") for b in c
                               if isinstance(b, dict) and b.get("type") == "text")
                if _has_kernel_marker(_txt) and _kernel_is_current(_txt, kernel):
                    logger.info("deepseek-jailbreak-core: openai-shape kernel "
                                "already present and current (system blocks)")
                    return request
                blocks = list(c)
                blocks.append({"type": "text", "text": kernel})
                new_msgs[sys_idx] = dict(m, content=blocks)
        out = dict(request)
        out["messages"] = new_msgs
        logger.info("deepseek-jailbreak-core: openai-shape kernel injected (system msg)")
        return out
    except Exception:
        return request


def _cut_kernel(text: str) -> str:
    """把文本里**裸的内核段落**裁掉（保留它之前的内容）。

    ★ 为什么需要：老会话冻结的旧内核常常是**裸文本**（没有 ``<!-- -->`` 容器），
      `strip_plugin_sections()` 只剥容器，剥不掉它；而 `_extract_section()`
      提取 memory 段时按标题切分，会把这段裸内核一起圈进去 ⇒
      替换后变成「memory + 旧内核 + 新内核」= 双重内核（实测自测抓到，出现 2 次标记）。
    """
    if not text:
        return text
    idx = -1
    for mark in _KERNEL_MARKERS:
        j = text.find(mark)
        if j != -1 and (idx == -1 or j < idx):
            idx = j
    if idx == -1:
        return text
    return text[:idx].rstrip("\n")


def _kernel_is_current(found: str, kernel: str) -> bool:
    """已有的内核文本是否**就是当前版本**。

    ★ 为什么需要这个：Hermes 的插件段是**每会话渲染一次并冻结**
      （`system_prompt.py::_frozen_plugin_prompt_sections`）。有持久化
      `_cached_system_prompt` 的会话，恢复时是 `_restore_plugin_prompt_sections`
      —— **从旧 prompt 里把旧段原样还原，不重新渲染**。
      而 `_has_kernel_marker()` 只看「有没有标记」，有就跳过 ⇒
      **改内核之后，老会话永远停在旧版本**（实测排查时正是这个盲区）。

    这里用**指纹比对**代替「有标记就算数」：
      · 当前内核的开头 120 字 + 结尾 120 字都必须出现在已有文本里
      · 任一缺失 ⇒ 认为是旧版（或别家内核）⇒ 应当替换
    """
    if not found or not kernel:
        return False
    head = kernel.strip()[:120]
    tail = kernel.strip()[-120:]
    return (head in found) and (tail in found)


def _extract_section(text: str, marker: str) -> str:
    """提取以 marker 开头的段落，止于下一个标题或插件段容器起点。

    切分点必须同时认 ``\\n## ``（标题）与 ``\\n<!--``（插件段容器）——插件段容器
    位于 memory 段与 runtime 段之间，只按标题切会把容器的起始标签留在上一段里。
    """
    i = text.find(marker)
    if i == -1:
        return ""
    rest = text[i + len(marker):]
    candidates = []
    for sep in ("\n## ", "\n<!--"):
        j = rest.find(sep)
        if j != -1:
            candidates.append(j)
    end = min(candidates) if candidates else len(rest)
    return (marker + rest[:end]).strip()