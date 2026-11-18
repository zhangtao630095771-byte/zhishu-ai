# -*- coding: utf-8 -*-
"""
test_golden_cases.py —— 标准问题集管理 测试（P0-1）

★ 覆盖：
  ① 标准 SQL 安全验证（复用现有闸门：只读 / 表白名单 / 字段权限，管理员录入不豁免）
  ② CRUD + 版本留痕（改 expected_sql 自动 +1 版本）
  ③ 审核发布（approve 前强制再验一次 expected_sql）
  ④ 验证状态独立于审核状态
  ⑤ 停用归档（不物理删除）
  ⑥ 批量导入（逐条校验、失败不中断）
  ⑦ acceptance=auto 必须有 expected_result

★ 运行：.venv/bin/python test_golden_cases.py  （临时库，不碰真实 agent_config.db）
"""
import gc
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config_store            # noqa: E402
import golden_cases as G       # noqa: E402

_passed = 0
_failed = []


def ok(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name} {detail}")


def with_temp_db(fn):
    """把 config_store 指向临时库，跑完还原。不允许测试改动真实配置库。"""
    orig_db = config_store.CONFIG_DB
    orig_ready = dict(config_store._state)
    tmp = tempfile.mktemp(suffix=".db")
    try:
        config_store.CONFIG_DB = tmp
        config_store._state["ready"] = False
        config_store.init_store()
        config_store._state["ready"] = True
        fn()
    finally:
        config_store.CONFIG_DB = orig_db
        config_store._state.update(orig_ready)
        gc.collect()
        for suffix in ("", "-wal", "-shm"):
            p = tmp + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


ALLOWED = ["dwd_order_all", "dwd_order_fee"]


def test_validate_expected_sql():
    ver = G.validate_expected_sql("SELECT COUNT(*) AS c FROM dwd_order_all", allowed_tables=ALLOWED)
    ok(ver["ok"], "合法只读 SQL 通过", str(ver))
    ver = G.validate_expected_sql("DROP TABLE dwd_order_all", allowed_tables=ALLOWED)
    ok(not ver["ok"] and ver["hard"], "写操作被硬拦截", str(ver))
    ver = G.validate_expected_sql("SELECT * FROM secret_table", allowed_tables=ALLOWED)
    ok(not ver["ok"] and ver["hard"], "未授权表被拦截", str(ver))
    ver = G.validate_expected_sql("", allowed_tables=ALLOWED)
    ok(not ver["ok"], "空 SQL 拦截", str(ver))


def test_create_and_auto_requires_result():
    r = G.create_case({"id": "g001", "question": "查询订单量", "expected_sql": "SELECT COUNT(*) FROM dwd_order_all",
                       "acceptance": "auto", "expected_result": None},
                      allowed_tables=ALLOWED)
    ok(not r["ok"] and "expected_result" in r["msg"], "auto 验收缺 expected_result 拒绝", str(r))

    r = G.create_case({"id": "g001", "question": "查询订单量",
                       "expected_sql": "SELECT COUNT(*) AS `订单量` FROM dwd_order_all",
                       "expected_result": {"columns": ["订单量"], "rows": [[10]], "precision": 4},
                       "acceptance": "auto", "business_line": "demo"},
                      allowed_tables=ALLOWED)
    ok(r["ok"], "合法标准案例创建成功", str(r))

    r = G.create_case({"id": "g002", "question": "危险注入",
                       "expected_sql": "DROP TABLE dwd_order_all"}, allowed_tables=ALLOWED)
    ok(not r["ok"] and "验证" in r["msg"], "危险 expected_sql 被拒绝写入", str(r))

    c = G.get_case("g001")
    ok(c is not None and c["business_line"] == "demo", "能读回案例", str(c))


def test_review_requires_revalidation():
    G.create_case({"id": "g003", "question": "合法", "expected_sql": "SELECT COUNT(*) FROM dwd_order_all"},
                  allowed_tables=ALLOWED)
    # 审核通过前，偷偷把 SQL 改成越权的（绕过 create 校验）→ 审核时须再拦
    import sqlite3
    conn = sqlite3.connect(config_store.CONFIG_DB)
    conn.execute("UPDATE golden_cases SET expected_sql=? WHERE id=?", ("SELECT * FROM secret_table", "g003"))
    conn.commit()
    conn.close()
    r = G.set_review("g003", "approved", reviewer="admin", allowed_tables=ALLOWED)
    ok(not r["ok"], "approve 时重新验证，越权 SQL 被拒", str(r))
    # 修复后再审核
    conn = sqlite3.connect(config_store.CONFIG_DB)
    conn.execute("UPDATE golden_cases SET expected_sql=? WHERE id=?", ("SELECT COUNT(*) FROM dwd_order_all", "g003"))
    conn.commit()
    conn.close()
    r = G.set_review("g003", "approved", reviewer="admin", allowed_tables=ALLOWED)
    ok(r["ok"], "修复后审核通过", str(r))


def test_version_bump():
    G.create_case({"id": "g004", "question": "版本", "expected_sql": "SELECT COUNT(*) FROM dwd_order_all"},
                  allowed_tables=ALLOWED)
    r = G.update_case("g004", {"expected_sql": "SELECT COUNT(*) AS c FROM dwd_order_all"},
                      allowed_tables=ALLOWED)
    ok(r["ok"], "更新成功", str(r))
    c = G.get_case("g004")
    ok(c["version"] == 2, "改 SQL 后版本 +1", str(c.get("version")))
    vs = G.list_versions("g004")
    ok(len(vs) == 1 and vs[0]["version"] == 2, "有版本历史记录", str(vs))


def test_verify_independent_of_review():
    G.create_case({"id": "g005", "question": "独立", "expected_sql": "SELECT COUNT(*) FROM dwd_order_all"},
                  allowed_tables=ALLOWED)
    r = G.verify_case("g005", "verified")
    ok(r["ok"], "可先验证", str(r))
    c = G.get_case("g005")
    ok(c["verify_status"] == "verified" and c["review_status"] == "draft",
       "验证状态独立于审核状态", f"verify={c['verify_status']} review={c['review_status']}")


def test_archive():
    G.create_case({"id": "g006", "question": "归档", "expected_sql": "SELECT COUNT(*) FROM dwd_order_all"},
                  allowed_tables=ALLOWED)
    r = G.archive_case("g006", "archived")
    ok(r["ok"], "归档成功", str(r))
    c = G.get_case("g006")
    ok(c["status"] == "archived", "归档后状态正确（未物理删除）", str(c))


def test_import_batch():
    items = [
        {"id": "g007", "question": "a", "expected_sql": "SELECT COUNT(*) FROM dwd_order_all"},
        {"id": "g008", "question": "b", "expected_sql": "DROP TABLE dwd_order_all"},
    ]
    r = G.import_cases(items, allowed_tables=ALLOWED)
    ok(r["ok"] == 1 and len(r["failed"]) == 1, "批量导入逐条校验、失败不中断", str(r))


def test_overview():
    r = G.overview()
    ok("total" in r and "verified" in r and "unverified" in r, "概览字段齐全", str(r))


if __name__ == "__main__":
    print()
    print("╔" + "═" * 56 + "╗")
    print("║" + "标准问题集管理 · 专项测试（P0-1）".center(36) + "║")
    print("╚" + "═" * 56 + "╝")
    for fn in (test_validate_expected_sql, test_create_and_auto_requires_result,
               test_review_requires_revalidation, test_version_bump,
               test_verify_independent_of_review, test_archive,
               test_import_batch, test_overview):
        try:
            with_temp_db(fn)
        except Exception as e:
            import traceback
            _failed.append(f"{fn.__name__} 异常：{e}")
            traceback.print_exc()
    print()
    print("=" * 58)
    if not _failed:
        print(f"🎉 全部通过（{_passed} 项）")
    else:
        print(f"⚠️ 通过 {_passed} 项，失败：")
        for f in _failed:
            print(f"   ❌ {f}")
    print("=" * 58)
    raise SystemExit(1 if _failed else 0)
