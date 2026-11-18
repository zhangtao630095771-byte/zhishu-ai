# -*- coding: utf-8 -*-
"""
_review_governance.py —— 「复核转 approved」工具 v2（选项①的稳妥路径）

作用：
  1. report：列出所有 candidate 知识，做四类检查并分类：
       ① 内容过短 / 缺主表或粒度            → 可疑（留人工）
       ② 占位式枚举（"其他类型41" / "无意义"）→ 可疑（留人工）
       ③ 跨表同名字段口径冲突               → 可疑（留人工）
       ④ 人工判定明确错误（MANUAL_REJECT）  → 直接 rejected（不进任何检索）
       其余 → 合格（clean），可批量核准为 approved
  2. apply ：把「合格」的 candidate 转为 approved，并：
       - 置 source='manual'（防止以后 AI 重跑覆盖已复核内容）
       - 写 approved_at/approved_by、重算 content_hash
       - 写 audit_log + knowledge_history（可追溯）
     同时把 MANUAL_REJECT 里的条目置为 rejected 并写审计。

用法：
  python3 _review_governance.py report     # 只看分类，不改数据
  python3 _review_governance.py apply      # 执行批量核准 + 标 rejected
环境变量：
  CONFIG_DB     配置库路径（默认脚本同目录 agent_config.db）
  GOV_APPROVER  核准人（默认 admin）
"""
import os
import re
import sys
import json
import hashlib
import sqlite3
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CONFIG_DB") or os.path.join(BASE_DIR, "agent_config.db")
APPROVER = os.environ.get("GOV_APPROVER", "admin")
TZ = timezone(timedelta(hours=8))

# ── 检查①：明显占位/无意义词（命中即判可疑，留人工）──
PLACEHOLDERS = ("todo", "待补", "待补充", "xxx", "暂无", "待定", "tbd")

# ── 检查②：占位式枚举 —— 形如 "41=其他类型41"、"0=无意义" ──
#    这类枚举键值对是 AI 凑数生成的，键与解释同义反复或直接说"无意义"，
#    进检索后会让模型误以为该取值有业务含义。
RE_PLACEHOLDER_ENUM = re.compile(
    r"其他类型\s*\d+|其他类型\s*[一二三四五六七八九十]|无意义|待补充取值|未知含义"
)

# ── 检查④：人工判定明确错误的条目（逐条读过原文后确认，非规则可判）──
#    (层级, 对象id) -> 原因；判错 → 置 rejected，不进任何检索（严格/非严格都排除）
MANUAL_REJECT = {
    ("column_comments", "ods_tu_driver_all.is_picked"):
        "语义矛盾：注释写「司机接单状态(1=正常,2=未认证)」，与字段名 is_picked 及"
        "同名字段口径(dwd_tu_order_all.is_picked=0否1是)冲突，疑为 AI 误读他表字段",
    ("column_comments", "ods_tu_order_pricing_record_all.carstate"):
        "整表串味：本表是小板询价表，carstate 应为车况（能启动/不能启动），"
        "注释却写成订单流转状态（待派单/已派单/服务中），与 dwd_tu_order_all.carstate 的"
        "车况口径冲突，属 AI 抽取时误用他表枚举",
    ("column_comments", "ods_tu_order_pricing_record_all.enterprise_customer_type"):
        "枚举自相矛盾：「企业客户类型」却写「1=个人」，0/1 两值无法构成企业类型口径，"
        "疑为错行写入",
}

# ── 人工确认放行：与判错项同名/同族但已核对无误的条目，避免被冲突检测误伤 ──
MANUAL_CONFIRM = {
    ("column_comments", "dwd_tu_order_all.carstate"):
        "已核对：本表 carstate 是车况（1=能启动/2=不能启动），与列名一致，是冲突对中正确的一方",
}

# ── 复核时就地修正：内容有瑕疵但骨架正确，改掉凑数枚举后即可核准 ──
#    (层级, 对象id) -> (新注释, 修正说明)
MANUAL_FIX = {
    ("column_comments", "ods_tu_large_handcart_order_all.order_type"):
        ("订单类型（0=大板询价，1=小板询价，2=大板专线，3=代驾询价，4=其他询价）",
         "删除 AI 凑数的「41=其他类型41，42=其他类型42」两项占位枚举"),
    ("column_comments", "dwd_tu_order_all.rescue_assign_status"):
        ("救援调度指派司机状态（0=未指派，2=已指派，3=已指派完成）",
         "「0=无意义」改为「0=未指派」，与 2/3 的语义序列对齐"),
}


def now_iso():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def bad_text(text, minlen):
    """内容过短或含占位词 → 返回原因；否则 None。"""
    t = (text or "").strip()
    if len(t) < minlen:
        return "内容过短(<%d字)" % minlen
    low = t.lower()
    for p in PLACEHOLDERS:
        if p in low:
            return "含占位词[%s]" % p
    return None


def parse_enum(comment):
    """
    从注释里抽出枚举键值对，用于跨表同名冲突检测。
    支持 "0=未支付" / "0-客户" / "空=非支付宝" 三种写法。
    返回 {key: desc}；无枚举则返回 {}。
    """
    out = {}
    # 键允许数字/字母/中文（≤4 字），因为枚举键通常是 0/1/2 或 空/alipay/个人 这类短值
    for m in re.finditer(r"([A-Za-z0-9\u4e00-\u9fa5]{0,4})\s*[=\-－]\s*([^，,；;）)]{1,20})", comment or ""):
        k = m.group(1).strip()
        v = m.group(2).strip()
        if not k or not v:
            continue
        # 只保留「短键」（数字或 <=4 字的中英文），避免把整句当成键
        if len(k) <= 4 and (k.isdigit() or re.match(r"^[A-Za-z0-9\u4e00-\u9fa5]+$", k)):
            if k not in out:
                out[k] = v
    return out


def cross_table_conflicts(cur):
    """
    检查③：同一 column_name 在 ≥2 张表出现，且枚举键相同但解释不同（≥2 处不同）
    → 说明至少有一张表的注释是错的（典型：carstate 在 A 表是车况、在 B 表写成派单状态）。
    返回 {oid: 原因}。
    """
    rows = cur.execute(
        "SELECT table_name, column_name, comment FROM column_comments WHERE status='candidate'"
    ).fetchall()
    by_col = {}
    for t, c, cm in rows:
        by_col.setdefault(c, []).append((t, cm or ""))

    out = {}
    for col, lst in by_col.items():
        if len(lst) < 2:
            continue
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                (t1, c1), (t2, c2) = lst[i], lst[j]
                e1, e2 = parse_enum(c1), parse_enum(c2)
                shared = set(e1) & set(e2)
                if len(shared) < 2:
                    continue
                diff = [k for k in shared if e1[k] != e2[k]]
                if len(diff) < 2:
                    continue
                # 差异够大 → 两表口径冲突，两张表都要人工确认
                oid1 = "%s.%s" % (t1, col)
                oid2 = "%s.%s" % (t2, col)
                msg = ("同名字段跨表口径冲突：与 %s 的取值解释有 %d 处不同"
                       "（键 %s），至少一方注释有误" % (t2, len(diff), ",".join(sorted(diff)[:4])))
                out.setdefault(oid1, msg)
                out.setdefault(oid2, ("同名字段跨表口径冲突：与 %s 的取值解释有 %d 处不同"
                                      "（键 %s），至少一方注释有误"
                                      % (t1, len(diff), ",".join(sorted(diff)[:4]))))
    return out


def collect(cur):
    """收集全部 candidate，返回 [{layer, oid, where_sql, where_args, raw, reason, verdict}]。"""
    items = []

    # ── 实体 ──
    for r in cur.execute("SELECT name, table_name, grain, comment, keywords, enabled, source "
                         "FROM entities WHERE status='candidate'"):
        d = dict(zip(["name", "table_name", "grain", "comment", "keywords", "enabled", "source"], r))
        reason = None
        if not (d["table_name"] or "").strip():
            reason = "缺主表"
        elif not (d["grain"] or "").strip():
            reason = "缺粒度"
        else:
            reason = bad_text(d["comment"], 10)
        items.append(dict(layer="entities", oid=d["name"], where_sql="name=?",
                          where_args=(d["name"],), raw=d, reason=reason))

    # ── 表注释 ──
    for r in cur.execute("SELECT table_name, comment, source FROM table_comments WHERE status='candidate'"):
        d = dict(zip(["table_name", "comment", "source"], r))
        reason = bad_text(d["comment"], 10)
        items.append(dict(layer="table_comments", oid=d["table_name"], where_sql="table_name=?",
                          where_args=(d["table_name"],), raw=d, reason=reason))

    # ── 字段注释 ──
    for r in cur.execute("SELECT table_name, column_name, comment, source FROM column_comments "
                         "WHERE status='candidate'"):
        d = dict(zip(["table_name", "column_name", "comment", "source"], r))
        reason = bad_text(d["comment"], 4)
        if not reason and (d["comment"] or "").strip() == d["column_name"]:
            reason = "注释与字段名相同"
        if not reason and RE_PLACEHOLDER_ENUM.search(d["comment"] or ""):
            reason = "占位式枚举（取值解释无业务含义）"
        oid = "%s.%s" % (d["table_name"], d["column_name"])
        items.append(dict(layer="column_comments", oid=oid,
                          where_sql="table_name=? AND column_name=?",
                          where_args=(d["table_name"], d["column_name"]), raw=d, reason=reason))

    # ── 检查③：跨表同名冲突（只对字段注释生效，走 oid 匹配）──
    conflicts = cross_table_conflicts(cur)
    for i in items:
        if i["layer"] != "column_comments":
            continue
        if not i["reason"] and i["oid"] in conflicts:
            i["reason"] = conflicts[i["oid"]]

    # ── 检查④：人工判定明确错误 → rejected ──
    keys_fix = {}
    for i in items:
        key = (i["layer"], i["oid"])
        if key in MANUAL_REJECT:
            i["reason"] = MANUAL_REJECT[key]
            i["verdict"] = "rejected"
        elif key in MANUAL_FIX and i["reason"]:
            # 骨架正确但含凑数枚举 → 记下修正方案，apply 时改写注释后再核准
            i["fix"] = MANUAL_FIX[key]
            i["orig_reason"] = i["reason"]
            i["reason"] = None
            i["verdict"] = "clean"
            keys_fix[key] = i["fix"][1]
        elif i["reason"]:
            i["verdict"] = "flagged"
        else:
            i["verdict"] = "clean"

    # ── 人工确认放行：优先级最高，覆盖冲突检测给出的 flagged ──
    for i in items:
        key = (i["layer"], i["oid"])
        if key in MANUAL_CONFIRM and i["verdict"] == "flagged":
            i["reason"] = None
            i["verdict"] = "clean"
            i["confirmed"] = MANUAL_CONFIRM[key]
    return items


def new_hash(layer, raw, status="approved"):
    """按各层口径重算 content_hash（source 将变为 manual）。"""
    if layer == "entities":
        return sha(canon({"name": raw["name"], "table_name": raw["table_name"], "grain": raw["grain"],
                          "comment": raw["comment"], "keywords": raw["keywords"],
                          "enabled": raw["enabled"], "source": "manual", "status": status}))
    if layer == "table_comments":
        return sha(canon({"table_name": raw["table_name"], "comment": raw["comment"],
                          "source": "manual", "status": status}))
    return sha(canon({"table_name": raw["table_name"], "column_name": raw["column_name"],
                      "comment": raw["comment"], "source": "manual", "status": status}))


def do_report(items, write_md=True):
    """打印分类结果，并可选写人工复核清单。"""
    clean = [i for i in items if i["verdict"] == "clean"]
    flagged = [i for i in items if i["verdict"] == "flagged"]
    rejected = [i for i in items if i["verdict"] == "rejected"]
    fixed = [i for i in clean if i.get("fix")]
    confirmed = [i for i in clean if i.get("confirmed")]
    print("待复核 candidate：%d 条 → 合格 %d（其中就地修正 %d、人工确认放行 %d），可疑 %d，判错 %d" %
          (len(items), len(clean), len(fixed), len(confirmed), len(flagged), len(rejected)))

    by = {}
    for i in items:
        by.setdefault(i["layer"], {"clean": 0, "flagged": 0, "rejected": 0})
        by[i["layer"]][i["verdict"]] += 1
    for layer, c in by.items():
        print("  %-16s 合格 %3d | 可疑 %3d | 判错 %3d" %
              (layer, c["clean"], c["flagged"], c["rejected"]))

    if fixed:
        print("\n就地修正后核准（骨架正确，仅修掉凑数枚举）：")
        for i in fixed:
            print("  [%s] %s → %s" % (i["layer"], i["oid"], i["fix"][1]))
    if confirmed:
        print("\n人工确认放行（冲突检测命中，但已核对无误）：")
        for i in confirmed:
            print("  [%s] %s → %s" % (i["layer"], i["oid"], i["confirmed"]))
    if flagged:
        print("\n可疑清单（保留 candidate，需人工确认）：")
        for i in flagged:
            print("  [%s] %s → %s" % (i["layer"], i["oid"], i["reason"]))
    if rejected:
        print("\n判错清单（将置为 rejected，不进检索）：")
        for i in rejected:
            print("  [%s] %s → %s" % (i["layer"], i["oid"], i["reason"]))

    if write_md:
        lines = ["# 知识复核清单（candidate → approved）\n",
                 "> 生成时间：%s　复核人：%s\n" % (now_iso(), APPROVER),
                 "> 判定规则：内容过短/占位词/占位式枚举/跨表同名口径冲突 → 留人工；"
                 "人工确认错误 → rejected；其余 → 可批量核准。\n",
                 "\n## 汇总\n",
                 "| 层级 | 待复核 | 合格(可核准) | 可疑(留人工) | 判错(已拒绝) |",
                 "|---|---:|---:|---:|---:|"]
        names = {"entities": "实体", "table_comments": "表注释", "column_comments": "字段注释"}
        for layer, c in by.items():
            lines.append("| %s | %d | %d | %d | %d |" %
                         (names.get(layer, layer), c["clean"] + c["flagged"] + c["rejected"],
                          c["clean"], c["flagged"], c["rejected"]))

        lines.append("\n## 就地修正项（改掉凑数枚举后核准）\n")
        if fixed:
            for i in fixed:
                lines.append("- `%s` %s\n  - 修正：%s" % (i["layer"], i["oid"], i["fix"][1]))
        else:
            lines.append("- 无")

        lines.append("\n## 人工确认放行项（冲突检测命中但核对无误）\n")
        if confirmed:
            for i in confirmed:
                lines.append("- `%s` %s\n  - 确认：%s" % (i["layer"], i["oid"], i["confirmed"]))
        else:
            lines.append("- 无")

        lines.append("\n## 可疑项（需人工确认，保留 candidate）\n")
        if flagged:
            for i in flagged:
                lines.append("- `%s` %s\n  - 原因：%s" % (i["layer"], i["oid"], i["reason"]))
        else:
            lines.append("- 无")

        lines.append("\n## 判错项（已置 rejected，不进任何检索）\n")
        if rejected:
            for i in rejected:
                lines.append("- `%s` %s\n  - 原因：%s" % (i["layer"], i["oid"], i["reason"]))
        else:
            lines.append("- 无")

        lines.append("\n## 后续动作\n")
        lines.append("1. 处理上表可疑项：修正后重新走复核，或确认无误后手工置 approved。")
        lines.append("2. 判错项需回源头修正（AI 抽取 Prompt / 人工录入），修正后重置为 candidate 再复核。")
        lines.append("3. 全部处理完毕后，再开启 `GOVERNANCE_STRICT_STATUS=1` 并重建索引。")

        path = os.path.join(BASE_DIR, "docs", "governance_review_queue.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("\n复核清单已写入：%s" % path)
    return clean, flagged, rejected


def _audit(cur, layer, oid, action, h, ts, detail):
    """写审计：audit_log + knowledge_history 双写，保证可追溯。"""
    aid = "review:%s:%s:%s" % (layer, oid, ts)
    cur.execute("""INSERT OR REPLACE INTO audit_log
        (audit_id, actor_id, action, object_type, object_id, version_no,
         before_hash, after_hash, request_id, occurred_at, detail)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (aid, APPROVER, action, layer, oid, None, None, h, None, ts, detail))


def do_apply(con, cur, clean, rejected):
    """把合格 candidate 批量核准；把判错条目置 rejected。均写审计。"""
    ts = now_iso()
    ok = 0
    for i in clean:
        layer = i["layer"]
        raw = dict(i["raw"])
        fix_sql, fix_args = "", ()
        if i.get("fix"):
            # 就地修正：改写 comment 后再核准（修正说明同时进审计）
            raw["comment"] = i["fix"][0]
            fix_sql = ", comment=?"
            fix_args = (i["fix"][0],)
        h = new_hash(layer, raw, "approved")
        cur.execute(
            "UPDATE %s SET status='approved', source='manual', approved_at=?, approved_by=?, "
            "content_hash=?%s WHERE %s AND status='candidate'" % (layer, fix_sql, i["where_sql"]),
            (ts, APPROVER, h) + fix_args + tuple(i["where_args"]))
        if cur.rowcount <= 0:
            continue
        ok += 1
        detail = {"review": "ai_precheck_batch_approve", "reason": "预检合格"}
        if i.get("fix"):
            detail = {"review": "review_fix_then_approve", "reason": i["fix"][1],
                      "before_comment": i["raw"].get("comment"), "after_comment": i["fix"][0]}
        _audit(cur, layer, i["oid"], "approve", h, ts, canon(detail))
        before = canon(i["raw"])
        after = canon(dict(raw, source="manual", status="approved"))
        cur.execute("""INSERT INTO knowledge_history
            (entity_type, entity_id, action, before_json, after_json, operator, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (layer, i["oid"], "approve", before, after, APPROVER, ts))

    rej = 0
    for i in rejected:
        layer = i["layer"]
        h = new_hash(layer, i["raw"], "rejected")
        cur.execute(
            "UPDATE %s SET status='rejected', content_hash=? WHERE %s AND status='candidate'"
            % (layer, i["where_sql"]),
            (h,) + tuple(i["where_args"]))
        if cur.rowcount <= 0:
            continue
        rej += 1
        _audit(cur, layer, i["oid"], "reject", h, ts,
               canon({"review": "manual_reject", "reason": i["reason"]}))
        before = canon(i["raw"])
        after = canon(dict(i["raw"], status="rejected", reject_reason=i["reason"]))
        cur.execute("""INSERT INTO knowledge_history
            (entity_type, entity_id, action, before_json, after_json, operator, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (layer, i["oid"], "reject", before, after, APPROVER, ts))
    return ok, rej


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "report").lower()
    if not os.path.exists(DB_PATH):
        print("✗ 配置库不存在：%s" % DB_PATH)
        sys.exit(1)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    items = collect(cur)
    clean, flagged, rejected = do_report(items)
    if mode == "apply":
        n, rej = do_apply(con, cur, clean, rejected)
        con.commit()
        print("\n✅ 已核准 %d 条（approved + source=manual + 审计）" % n)
        print("🚫 已拒绝 %d 条（rejected + 审计，不进检索）" % rej)
        print("\n=== 复核后状态分布 ===")
        for t in ("entities", "table_comments", "column_comments"):
            total = cur.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            rows = dict(cur.execute("SELECT status, COUNT(*) FROM %s GROUP BY status" % t).fetchall())
            print("  %-16s total=%d %s" % (t, total, rows))
    con.close()


if __name__ == "__main__":
    main()
