# -*- coding: utf-8 -*-
"""
test_sql_modify.py —— SQL 多轮修改结构保护（P0）回归测试

覆盖任务书 §三/§四：
  ① #243 案例：原始 SQL 逻辑被破坏 → 结构差异应被识别为「未授权越界」
  ② 正确「仅改时间」→ 不应产生结构差异
  ③ 10 项多轮修改场景的授权范围识别

纯确定性测试（不联网、不调真实模型）。
运行： .venv/bin/python test_sql_modify.py
"""
import sql_modify


PASSED, FAILED = [], []


def check(label, got, expected):
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望: {expected}")
        print(f"       实际: {got}")
    return ok


def check_true(label, cond, detail=""):
    ok = bool(cond)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok and detail:
        print(f"       实际: {detail}")
    return ok


# ── #243 原始 SQL（正确基线：积分 + 小板/大板/代驾完单，channel=1/2/3）──
BASE_SQL = """
WITH pt AS (
  SELECT customer_id, SUM(point) AS total_point
  FROM ctc_data_warehouse.ods_ctc_point_batch_all
  WHERE create_time >= '2026-09-28 00:00:00' AND create_time < '2026-10-28 00:00:00'
  GROUP BY customer_id
), rec AS (
  SELECT customer_id,
    SUM(CASE WHEN channel = 1 THEN amount ELSE 0 END) AS small_amount,
    COUNT(DISTINCT CASE WHEN channel = 1 THEN biz_no END) AS small_cnt,
    SUM(CASE WHEN channel = 3 THEN amount ELSE 0 END) AS large_amount,
    COUNT(DISTINCT CASE WHEN channel = 3 THEN biz_no END) AS large_cnt,
    SUM(CASE WHEN channel = 2 THEN amount ELSE 0 END) AS valet_amount,
    COUNT(DISTINCT CASE WHEN channel = 2 THEN biz_no END) AS valet_cnt
  FROM ctc_data_warehouse.ods_ctc_point_record_all
  WHERE create_time >= '2026-09-28 00:00:00' AND create_time < '2026-10-28 00:00:00'
  GROUP BY customer_id
)
SELECT pt.customer_id AS "客户id", pt.total_point AS "总积分",
  COALESCE(rec.small_amount, 0) AS "小板完单金额",
  COALESCE(rec.small_cnt, 0) AS "小板完单量",
  COALESCE(rec.large_amount, 0) AS "大板完单金额",
  COALESCE(rec.large_cnt, 0) AS "大板完单量",
  COALESCE(rec.valet_amount, 0) AS "代驾完单金额",
  COALESCE(rec.valet_cnt, 0) AS "代驾完单量"
FROM pt LEFT JOIN rec ON pt.customer_id = rec.customer_id
ORDER BY pt.total_point DESC
"""

# ── 破坏后的 SQL（模拟 #243 实际发生的变化）──
BAD_SQL = """
WITH pt AS (
  SELECT customer_id, SUM(point) AS total_point
  FROM ctc_data_warehouse.ods_ctc_point_batch_all
  WHERE create_time >= '2026-10-05 00:00:00' AND create_time < '2026-11-04 00:00:00'
  GROUP BY customer_id
), small AS (
  SELECT customer AS customer_id, COUNT(*) AS small_cnt, COALESCE(SUM(total), 0) AS small_amount
  FROM ctc_data_warehouse.dwd_tu_order_lab_all
  WHERE status = 6 AND ctime >= '2026-10-05 00:00:00' AND ctime < '2026-11-04 00:00:00'
  GROUP BY customer
)
SELECT pt.customer_id, pt.total_point, COALESCE(small.small_amount,0) AS "小板完单金额",
  COALESCE(small.small_cnt,0) AS "小板完单量"
FROM pt LEFT JOIN small ON pt.customer_id = small.customer_id
ORDER BY pt.total_point DESC
"""

# ── 正确「仅改近30天」──
GOOD_SQL = BASE_SQL.replace(
    "create_time >= '2026-09-28 00:00:00' AND create_time < '2026-10-28 00:00:00'",
    "create_time >= toStartOfDay(now()) - INTERVAL 29 DAY AND create_time < toStartOfDay(now()) + INTERVAL 1 DAY")


# ═══════════════════════════════════════════════════════════
print("\n【#243】原始 SQL 逻辑保护：破坏性修改必须被识别为越界")
# ═══════════════════════════════════════════════════════════
diff = sql_modify.diff_structure(BASE_SQL, BAD_SQL)
check_true("#243 识别出表变化", "tables" in diff, diff)
check_true("#243 识别出聚合变化", "aggregations" in diff, diff)
check_true("#243 识别出业务过滤变化", "business_where" in diff, diff)
check_true("#243 识别出 SELECT 变化", "select_exprs" in diff, diff)

unauth = sql_modify.unauthorized_changes(diff, "将此查询修改为流动的近30天")
check_true("#243 判定「近30天」修改只授权时间，未授权表变化", "tables" in unauth)
check_true("#243 未授权聚合变化", "aggregations" in unauth)
check_true("#243 未授权业务过滤变化", "business_where" in unauth)


# ═══════════════════════════════════════════════════════════
print("\n【正确修改】仅改时间 → 不应产生任何结构差异")
# ═══════════════════════════════════════════════════════════
good_diff = sql_modify.diff_structure(BASE_SQL, GOOD_SQL)
check("#243 正确时间修改无结构差异", good_diff, {})
check("#243 正确时间修改无越界", sql_modify.unauthorized_changes(good_diff, "将此查询修改为流动的近30天"), [])


# ═══════════════════════════════════════════════════════════
print("\n【授权范围识别】10 项多轮修改场景")
# ═══════════════════════════════════════════════════════════
def _scope(q):
    return sql_modify.detect_scope(q)

check("场景1 固定时间改近30天 → time", "time" in _scope("将此查询修改为流动的近30天"), True)
check("场景2 固定时间改近7天 → time", "time" in _scope("时间范围改成近7天"), True)
check("场景3 增加一个 WHERE 条件 → filter", "filter" in _scope("增加一个筛选条件：只保留渠道1"), True)
check("场景4 删除一个 SELECT 字段 → remove_select", "remove_select" in _scope("删除手机号这个字段"), True)
check("场景5 增加一个统计字段 → add_select", "add_select" in _scope("增加一个统计字段：完成率"), True)
check("场景6 修改 ORDER BY → order_by", "order_by" in _scope("按注册客户数降序排列"), True)
check("场景7 修改 GROUP BY → group_by", "group_by" in _scope("改成按月份分组统计"), True)
check("场景8 修改指标计算方式 → metric", "metric" in _scope("完单量的计算方式改成按 biz_no 去重"), True)
check("场景9 连续修改时间 → time", "time" in _scope("时间再改成近7天"), True)
check("场景10 过滤值变更 → filter", "filter" in _scope("应用主体只看 101，其它不变"), True)


# ═══════════════════════════════════════════════════════════
print("\n【聚合差异】COUNT(DISTINCT) → COUNT 必须被识别")
# ═══════════════════════════════════════════════════════════
a = sql_modify.extract_aggregations(BASE_SQL)
check_true("base 聚合含 COUNT(DISTINCT ...)", any("DISTINCT" in x for x in a), a)
b = sql_modify.extract_aggregations(BAD_SQL)
check_true("bad 聚合为 COUNT(*)/SUM(total)（无 DISTINCT）", all("DISTINCT" not in x for x in b), b)


print()
print("=" * 60)
if FAILED:
    print(f"❌ 失败 {len(FAILED)} 项，通过 {len(PASSED)} 项")
    for f in FAILED:
        print(f"   ❌ {f}")
else:
    print(f"🎉 全部通过（{len(PASSED)} 项）")
print("=" * 60)
