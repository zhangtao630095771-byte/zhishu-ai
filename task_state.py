# -*- coding: utf-8 -*-
"""
task_state.py —— 问数任务状态注册表 + SSE 事件协议常量

★ 定位（配合「问数交互 / SSE 流式响应」改造）
  统一"一次取数任务"的生命周期状态，供 SSE 断线重连 / 取消 / 刷新恢复使用。
  仅内存态（单进程 uvicorn 部署）：进程重启即失效，但「刷新恢复」这类场景足够。
  不落库 —— 历史 / 审计仍走 config_store.query_records（与这里解耦，互不替代）。

★ 状态机（方案三.2 规定的五种状态）
  RUNNING ──→ WAITING_CLARIFICATION ──→ RUNNING ──→ COMPLETED / FAILED / CANCELLED

★ 铁律
  本文件【零项目依赖】，只做"状态 + 事件名"的约定，避免与 agent/server 产生循环 import。
"""
import threading
import time

# ── 统一任务状态 ──
RUNNING = "RUNNING"                                # 执行中
WAITING_CLARIFICATION = "WAITING_CLARIFICATION"    # 等待用户确认澄清
COMPLETED = "COMPLETED"                            # 完成
FAILED = "FAILED"                                  # 失败
CANCELLED = "CANCELLED"                            # 已取消

# ── SSE 事件名（方案三.2 统一事件协议）──
EV_TASK_STARTED = "task_started"                   # 任务开始
EV_UNDERSTANDING = "understanding"                 # 正在理解问题
EV_RETRIEVAL = "retrieval"                         # 正在检索企业知识
EV_CLARIFICATION_REQUIRED = "clarification_required"  # 需要澄清（等待用户确认）
EV_CLARIFICATION_UPDATED = "clarification_updated"    # 澄清已更新（多轮）
EV_SQL_GENERATING = "sql_generating"               # 正在生成 SQL
EV_SQL_VALIDATING = "sql_validating"               # 正在校验 SQL
EV_SQL_EXECUTING = "sql_executing"                 # 正在执行查询
EV_RESULT = "result"                               # 结果（完整 payload）
EV_ERROR = "error"                                 # 错误
EV_DONE = "done"                                   # 结束


class TaskCancelled(Exception):
    """用户主动取消任务时抛出（由 agent 抛出，server 捕获后转为 CANCELLED 状态）。"""
    pass


_lock = threading.Lock()
_tasks = {}                                        # {task_id: {...}}
_MAX_TASKS = 200                                   # 内存态只留最近 200 个，防长跑进程无限增长


def new_task(task_id, username="", question=""):
    """注册一个新任务，返回其快照。"""
    with _lock:
        _tasks[task_id] = {
            "task_id": task_id,
            "username": username,
            "question": question,
            "status": RUNNING,
            "result": None,                        # 完成后缓存的最终 payload（供刷新恢复）
            "clarification": None,                 # WAITING_CLARIFICATION 时的结构化澄清
            "query_id": None,                      # 关联澄清会话 query_id
            "created_at": time.time(),
            "updated_at": time.time(),
            "_cancel": threading.Event(),           # 取消信号（内部用）
        }
        if len(_tasks) > _MAX_TASKS:               # 简单回收：超出上限清掉最老的
            oldest = sorted(_tasks, key=lambda k: _tasks[k]["created_at"])[
                : len(_tasks) - _MAX_TASKS]
            for k in oldest:
                _tasks.pop(k, None)
        return dict(_tasks[task_id])


def get(task_id):
    """取任务快照（不含内部 _cancel 对象）。"""
    with _lock:
        t = _tasks.get(task_id)
        return {k: v for k, v in t.items() if not k.startswith("_")} if t else None


def set_status(task_id, status):
    with _lock:
        t = _tasks.get(task_id)
        if t:
            t["status"] = status
            t["updated_at"] = time.time()


def set_result(task_id, payload):
    with _lock:
        t = _tasks.get(task_id)
        if t:
            t["result"] = payload
            t["updated_at"] = time.time()


def set_clarification(task_id, clarification, query_id=None):
    with _lock:
        t = _tasks.get(task_id)
        if t:
            t["clarification"] = clarification
            t["query_id"] = query_id
            t["updated_at"] = time.time()


def request_cancel(task_id):
    """请求取消：置取消信号，返回是否命中。"""
    with _lock:
        t = _tasks.get(task_id)
        if t:
            t["_cancel"].set()
            t["updated_at"] = time.time()
            return True
    return False


def is_cancelled(task_id):
    with _lock:
        t = _tasks.get(task_id)
        return bool(t and t["_cancel"].is_set())
