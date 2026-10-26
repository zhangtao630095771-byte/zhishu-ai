# -*- coding: utf-8 -*-
"""临时探针：核对司机风险画像 SQL 涉及的 13 张表是否存在，并打印现状（ACL / 注释来源）。"""
import json
import config_store
import db

TABLES = [
    "dwd_driver_base_profile_all",
    "dwd_tu_order_lab_all",
    "ods_violation_info_all",
    "ods_violation_punish_config_all",
    "ods_offline_order_driver_location_statistical_all",
    "ods_tu_pay_log_all",
    "ods_violation_resell_order_all",
    "ods_ctc_evaluate_driver_all",
    "dws_day_user_login_activity_all",
    "ods_tu_driver_annual_fee_activation_log_all",
    "ods_tu_mall_order_all",
    "ods_tu_mall_order_goods_all",
    "ods_tu_mall_order_log_all",
]

print("DB impl:", db.IMPL_NAME)

# 一次性取全部表名，做存在性判断（比逐张连库快）
try:
    all_tables = set(db.list_tables())
    print("全库表数:", len(all_tables))
except Exception as e:
    all_tables = None
    print("list_tables 失败:", repr(e))

comments = config_store.get_table_comments()
sources = config_store.get_table_comment_sources()
acl = config_store.get_table_acl()

result = {}
for t in TABLES:
    exists = (t in all_tables) if all_tables is not None else None
    acl_info = acl.get(t)
    result[t] = {
        "exists": exists,
        "acl": acl_info,
        "comment_len": len(comments.get(t) or ""),
        "source": sources.get(t),
    }
    print(f"{t:60s} exists={exists} acl={acl_info} comment_len={len(comments.get(t) or '')} src={sources.get(t)}")

with open("/tmp/driver13_probe.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("saved /tmp/driver13_probe.json")
