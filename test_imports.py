# -*- coding: utf-8 -*-
"""
test_imports.py —— 导入冒烟测试（打包完整性 + 依赖齐全）

★ 为什么需要它？（代码审查报告 P0-1）
  曾经把代码打包时漏了 `colname.py`，而 `server.py` 顶部 `import colname` ——
  按包独立部署时 uvicorn 会在【导入阶段】就崩，但 `py_compile` 查不出来
  （它只验证语法，不验证模块是否存在、依赖是否齐全）。
  这个冒烟测试就是专门补这个盲区：把随包交付的模块逐个 import 一遍。

运行： .venv/bin/python test_imports.py
"""
import importlib
import traceback

# 随包交付的模块清单（应与代码包 / 部署清单保持一致）
MODULES = [
    "config", "config_store", "knowledge", "llm",
    "db", "db_clickhouse", "db_sqlite",
    "guard", "rag", "prompt", "nl_answer", "calendar_util",
    "query_understanding", "semantic_layer", "sql_validator",
    "result_validator", "ai_ingest", "admin", "gaps", "suggest",
    "exporter", "enum_parse", "colname",
    "agent", "server", "main",
]

PASSED, FAILED = [], []


def main():
    print()
    print("=" * 62)
    print("导入冒烟测试：逐个 import 随包交付的模块")
    print("=" * 62)
    for name in MODULES:
        try:
            importlib.import_module(name)
            PASSED.append(name)
            print(f"  ✅ {name}")
        except Exception as e:
            FAILED.append(name)
            print(f"  ❌ {name} —— {type(e).__name__}: {e}")
            traceback.print_exc()
    print("-" * 62)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部可导入（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ {len(FAILED)}/{total} 导入失败：{FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
