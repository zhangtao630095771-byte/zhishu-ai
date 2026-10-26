# -*- coding: utf-8 -*-
"""
ssh_tunnel.py —— 需要时自动拉起 SSH 隧道，连上内网 ClickHouse。

★ 为什么需要它：
    真实 CK 跑在内网 <内网CK_HOST>，Mac 直连不到，必须经跳板机转发。
    本项目已有一个交互友好的 tunnel.sh（用 expect 自动填密码）。
    这个模块只是"在 Python 里按需调用它"，做到『启动即连』。

使用（在 main.py / server.py 里）：
    import ssh_tunnel
    ssh_tunnel.ensure_tunnel()   # 端口已通就跳过，没通则拉起

行为：
    · DB_TYPE != clickhouse         → 直接返回（用 SQLite 不需要隧道）
    · 没配 SSH_HOST                  → 直接返回（用户走公网直连，自己管隧道）
    · 本地 CK_PORT 已在监听          → 跳过，不重复建
    · 否则调 tunnel.sh 拉起隧道，再确认端口
"""
import os
import socket
import subprocess
import time

_PROJECT = os.path.dirname(os.path.abspath(__file__))

# ★ 最近一次"CK 真实可用"的时间戳：避免每次请求都做 /ping 探测（节流）
_LAST_OK = {"at": 0.0}
_OK_TTL = 15            # 秒：15 秒内已确认可用则跳过重复探测


def _port_up(port, host="127.0.0.1", timeout=2):
    """本地端口是否已能连上"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _ck_ping(port, timeout=3):
    """
    探测 ClickHouse 是否真的响应（GET /ping 返回 Ok）。

    ★ 为什么不能只看端口？
      SSH 隧道进程还活着、本地端口还在监听，但底层 SSH 连接可能已断
      （表现为"端口通、CK 无响应"）。只看端口会误判为"隧道正常"，
      于是永远不会自动重建 —— 这正是用户反复遇到"隧道可能已断开"的根因。
    """
    import http.client
    try:
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
        conn.request("GET", "/ping")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "ignore")
        conn.close()
        return "Ok" in body
    except Exception:
        return False


def ensure_tunnel(verbose=True):
    """确保本地 CK_PORT 端口不仅"通"，而且 ClickHouse 真的能响应。

    返回 True = 隧道可用 / False = 没配 SSH 或拉起失败。
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_PROJECT, ".env"))
    except Exception:
        pass

    if os.getenv("DB_TYPE", "sqlite") != "clickhouse":
        return False
    ssh_host = os.getenv("SSH_HOST")
    if not ssh_host:
        return False  # 直连模式，不需要隧道

    port = os.getenv("CK_PORT", "8123")

    # ① 端口通 + 最近确认可用（节流窗口内）→ 直接放行
    if _port_up(port):
        if time.time() - _LAST_OK["at"] < _OK_TTL:
            return True
        # ② 端口通，但要在节流窗口外做一次真实 /ping
        if _ck_ping(port):
            _LAST_OK["at"] = time.time()
            if verbose:
                print(f"[tunnel] 端口 127.0.0.1:{port} 可用（CK 响应正常）")
            return True
        # ③ 端口通但 CK 无响应 → 隧道假死，重建
        script = os.path.join(_PROJECT, "tunnel.sh")
        if not os.path.exists(script):
            return False
        if verbose:
            print("[tunnel] ⚠️ 端口在但 ClickHouse 无响应，重建隧道 ...")
        try:
            subprocess.run(["bash", script, "stop"], cwd=_PROJECT, check=False)
            time.sleep(1)
            subprocess.run(["bash", script, "start"], cwd=_PROJECT, check=False)
        except Exception as e:
            if verbose:
                print(f"[tunnel] 重建隧道出错：{e}")
            return False
        ok = _port_up(port) and _ck_ping(port)
        if ok:
            _LAST_OK["at"] = time.time()
        if verbose:
            print("[tunnel] ✅ 隧道已重建" if ok else "[tunnel] ❌ 隧道重建失败，请检查 SSH_* 配置")
        return ok

    # ④ 端口不通 → 拉起隧道
    script = os.path.join(_PROJECT, "tunnel.sh")
    if not os.path.exists(script):
        if verbose:
            print("[tunnel] 未找到 tunnel.sh，跳过（请手动建隧道）")
        return False

    if verbose:
        print("[tunnel] 本地端口未通，自动拉起 SSH 隧道 ...")
    try:
        subprocess.run(["bash", script], cwd=_PROJECT, check=False)
    except Exception as e:  # 拉起失败不应拖垮主程序
        if verbose:
            print(f"[tunnel] 调用 tunnel.sh 出错：{e}")
        return False

    ok = _port_up(port) and _ck_ping(port)
    if ok:
        _LAST_OK["at"] = time.time()
    if verbose:
        print("[tunnel] ✅ 隧道已建立" if ok else "[tunnel] ❌ 隧道拉起失败，检查 SSH_* 配置")
    return ok
