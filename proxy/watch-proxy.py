# -*- coding: utf-8 -*-
"""破甲代理看护：检查 127.0.0.1:8790，掉了就拉起（幂等，立即返回）。

★ 不用 .cmd：cmd 的 `start` 会让子进程继承句柄 ⇒ 调用方等它退出 ⇒ 超时。
  这里用 DETACHED_PROCESS 完全脱离。

★ 解释器路径**不写死**：用当前解释器（sys.executable），或环境变量
  JB_PYTHON 指定。写死某台机器的 Python 路径 ⇒ 换台机器看护就静默失效。
"""
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = os.environ.get("JB_PYTHON") or sys.executable or "python"
URL = "http://127.0.0.1:8790/health"


def alive() -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    if alive():
        print("proxy alive")
        return 0
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        flags |= getattr(subprocess, name, 0)
    with open(HERE / "proxy.log", "a", encoding="utf-8") as log:
        subprocess.Popen([PY, str(HERE / "proxy.py"), "--port", "8790", "--verbose"],
                         cwd=str(HERE), stdout=log, stderr=subprocess.STDOUT,
                         creationflags=flags, close_fds=True)
    print("proxy restarted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
