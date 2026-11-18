# -*- coding: utf-8 -*-
"""
result_cache.py —— 取数结果缓存（同问同人同权限 → 直接复用上次结果）

★ 为什么需要它？
  一次取数平均 20~35 秒（LLM 生成 + 多次 ClickHouse 查询），且 LLM 按 token 计费。
  而真实使用中「同一批人反复问同几个问题」非常常见（日报、周报口径几乎固定）。
  每次都完整重跑一遍，等于持续烧钱 + 持续让人等。

★ 为什么缓存放在 server 层，而不是 agent 里？
  agent 是"算得对"的地方，evaluation / CLI / ablation 都直接调它。
  如果缓存藏在 agent 里，**评测会被缓存污染**（第二次跑同一道题直接命中，
  测出来的是缓存不是模型），所有对照实验都会失真。
  所以缓存属于"服务层"的优化，只在 /api/ask 这一条真实用户链路上生效。

★ 为什么 key 必须带用户和权限指纹？（这是本模块唯一真正危险的地方）
  取数结果里可能包含：
    · 行级权限过滤后的行（不同用户看到的行不同）
    · 字段级 MASK 脱敏后的值（客服看不到手机号，主管能看到）
  如果按"问题"缓存而不区分用户，A 用户查过的脱敏结果会给到 B，
  或者 B 的越权查询直接命中 A 的合法结果 —— **缓存会变成权限旁路**。
  因此 key 必须包含：用户名 + 角色 + 当前可见表指纹。

★ 为什么 key 还要带知识指纹？
  改了指标口径 / 补了字段注释 / 发布了新经验，答案就应该变。
  只按问题缓存会让"我刚改了口径，怎么还答老样子"变成新的排障黑洞。

★ 只缓存成功结果。
  失败结果（拒答/拦截/报错）往往与瞬时状态有关（LLM 抖动、DB 超时），
  缓存失败等于把一次偶发故障固化成稳定故障。
"""
import hashlib
import json
import threading
import time

import config

_LOCK = threading.Lock()
_STORE = {}          # key -> {"v": value, "at": ts, "hits": int}
_STAT = {"hit": 0, "miss": 0, "put": 0, "evict": 0, "skip": 0}
_KFP = {"v": "", "at": 0.0}      # 知识指纹缓存（避免每次请求都查 6 张表）


# ═══════════════════════════════════════════════════════════
# 知识指纹
# ═══════════════════════════════════════════════════════════
def _knowledge_fp():
    """知识指纹：任何一条知识被改，指纹就变（= 相关缓存自动失效）。

    ★ 用「条数 + 最大 updated_at」而不是全表哈希：
      6 张表各一次聚合，SQLite 上不到 1ms，且能覆盖"新增/删除/修改"三种变更。
      全表哈希要把所有正文读出来算 md5，知识库一大就变成新的性能瓶颈。

    ★ 结果缓存 30 秒：这 30 秒内知识被改，最多延迟 30 秒才生效，
      而 TTL 本身就有限，用户感知不到这个量级的延迟。
    """
    ttl = getattr(config, "RESULT_CACHE_KFP_TTL", 30)
    now = time.time()
    if _KFP["v"] and now - _KFP["at"] < ttl:
        return _KFP["v"]
    try:
        import config_store
        conn = config_store._connect()
        try:
            row = conn.execute("""
                SELECT
                  (SELECT COUNT(*) || ':' || IFNULL(MAX(updated_at), '') FROM metrics)
                || '|' || (SELECT COUNT(*) || ':' || IFNULL(MAX(updated_at), '') FROM entities)
                || '|' || (SELECT COUNT(*) || ':' || IFNULL(MAX(updated_at), '') FROM dict_entries)
                || '|' || (SELECT COUNT(*) || ':' || IFNULL(MAX(updated_at), '') FROM kb_docs)
                || '|' || (SELECT COUNT(*) || ':' || IFNULL(MAX(updated_at), '') FROM relationships)
                || '|' || (SELECT COUNT(*) FROM prompts)
            """).fetchone()
            v = str(row[0]) if row else ""
        finally:
            conn.close()
    except Exception as e:
        # ★ 取不到指纹就【不允许缓存】：宁可每次都重算，也不能猜一个 key 出来。
        #   猜错 key 的后果是"知识改了但用户还看到旧答案"，且完全无法排查。
        print(f"⚠️ 知识指纹计算失败，本次跳过缓存：{e}")
        return ""
    _KFP["v"] = v
    _KFP["at"] = now
    return v


# ═══════════════════════════════════════════════════════════
# key 构造
# ═══════════════════════════════════════════════════════════
def _norm(question):
    """问题归一化：只压空白和大小写，不做同义改写

    ★ 为什么不做得更"聪明"（去掉语气词、同义词映射）？
      归一化本身可能改变语义（"不含退款" vs "含退款"），
      一旦把两个不同问题归一成同一个 key，就会返回错误答案。
      这里只做**绝对安全**的变换：多余空白、大小写。
    """
    return " ".join((question or "").split()).strip().lower()


def _fp(items):
    """列表指纹（表清单等），排序后取短哈希"""
    s = "|".join(sorted(str(x) for x in (items or [])))
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def make_key(index, question, user):
    """构造缓存 key。返回 None 表示"本次不允许缓存"。"""
    if not getattr(config, "RESULT_CACHE_TTL", 0):
        return None
    kfp = _knowledge_fp()
    if not kfp:
        return None
    user = user or {}
    parts = {
        "q": _norm(question),
        # ★ 用户身份：行级过滤与字段级脱敏都依赖它，绝不能省
        "u": user.get("username", ""),
        "r": user.get("role", ""),
        # ★ 可见表指纹：ACL 变了就要重新算（否则可能命中越权/缺表的结果）
        "acl": _fp(index.get("allowed_tables") or []),
        "k": kfp,
        "d": getattr(config, "SQL_DIALECT", ""),
        "m": getattr(config, "LLM_MODEL", ""),
        # ★ 经验开关：A/B/C/D 对照时不同开关必须分开缓存
        "x": [bool(getattr(config, "EXP_SUCCESS_RECALL", False)),
              bool(getattr(config, "EXP_FAIL_PREVENT", False)),
              bool(getattr(config, "EXP_RULES", False)),
              bool(getattr(config, "EXP_EXACT_REUSE", False))],
    }
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════
# 读写
# ═══════════════════════════════════════════════════════════
def _now():
    return time.time()


def get(key):
    if not key:
        return None
    ttl = getattr(config, "RESULT_CACHE_TTL", 0)
    with _LOCK:
        it = _STORE.get(key)
        if not it:
            _STAT["miss"] += 1
            return None
        if _now() - it["at"] > ttl:
            _STORE.pop(key, None)
            _STAT["evict"] += 1
            _STAT["miss"] += 1
            return None
        it["hits"] += 1
        _STAT["hit"] += 1
        return it["v"]


def put(key, value):
    if not key:
        return
    _STAT["put"] += 1
    maxn = getattr(config, "RESULT_CACHE_MAX", 200)
    with _LOCK:
        if len(_STORE) >= maxn and key not in _STORE:
            # ★ 淘汰策略：先清过期，再淘汰最久未用的。
            #   不用 LRU 计数器是因为这里只存 200 条，简单够用。
            ttl = getattr(config, "RESULT_CACHE_TTL", 0)
            now = _now()
            for k in [k for k, v in _STORE.items() if now - v["at"] > ttl]:
                _STORE.pop(k, None)
                _STAT["evict"] += 1
            if len(_STORE) >= maxn:
                oldest = min(_STORE.items(), key=lambda kv: kv[1]["at"])[0]
                _STORE.pop(oldest, None)
                _STAT["evict"] += 1
        _STORE[key] = {"v": value, "at": _now(), "hits": 0}


def clear(reason=""):
    with _LOCK:
        n = len(_STORE)
        _STORE.clear()
        _KFP["v"] = ""
        _KFP["at"] = 0.0
    if n:
        print(f"🧹 取数结果缓存已清空（{n} 条）{('：' + reason) if reason else ''}")
    return n


def stats():
    with _LOCK:
        total = _STAT["hit"] + _STAT["miss"]
        return {
            "enabled": bool(getattr(config, "RESULT_CACHE_TTL", 0)),
            "ttl": getattr(config, "RESULT_CACHE_TTL", 0),
            "size": len(_STORE),
            "max": getattr(config, "RESULT_CACHE_MAX", 200),
            "hit": _STAT["hit"],
            "miss": _STAT["miss"],
            "hit_rate": round(_STAT["hit"] / total, 4) if total else None,
            "put": _STAT["put"],
            "evict": _STAT["evict"],
        }


def should_cache(result):
    """什么样的结果值得缓存？

    ★ 只缓存"真的取到数"的成功结果：
      · ok=False（拒答/拦截/报错）不缓存 —— 否则一次偶发故障被固化成稳定故障；
      · 0 行的结果缓存 —— "这个条件下确实没数据"是确定结论，重复跑也是 0 行；
      · 带 result_warnings 的照常缓存 —— warning 是结果的一部分，缓存它才一致。
    """
    if not isinstance(result, dict):
        return False
    if not result.get("ok"):
        return False
    # ★ 降级结果不缓存：本次是 legacy 兜底出的答案，说明 V2 当时有问题，
    #   缓存下来会让"V2 恢复了"这件事永远体现不出来。
    if result.get("degraded"):
        return False
    return True
