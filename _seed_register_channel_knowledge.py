# -*- coding: utf-8 -*-
"""
补种「注册渠道」知识：ods_user_register_detail_all 的字段注释 + 表注释（幂等）

★ 背景
  用户查询「APP新注册客户小板发单明细」时，模型生成的 SQL 退化成「全部新注册客户」，
  因为模型找不到注册渠道字段，直接放弃过滤。
  实测根因：
    ① 注册渠道字段在 ods_user_register_detail_all（channel_source），
       但客户表 ods_tu_customer_all 确实没有渠道字段；
    ② ods_user_register_detail_all 的 channel_source / app_entity_source /
       app_market_source 三个渠道字段【全都没有字段注释】，模型不认识；
    ③ 表注释没说明「注册渠道=channel_source + 编码规则 + user_id 关联客户表」。

★ 实测数据（ClickHouse，2026-11-06）
    ods_user_register_detail_all.channel_source 取值只有三位/四位编码，没有一级值：
      0 / 101 / 102 / 103 / 201 / 202 / 204 / 303 / 703 / 3016 / 3017
    → 编码规则：1xx=APP、2xx=小程序、3xx=H5页面、7xx=三方、3011-3022=官网站群。

运行： .venv/bin/python _seed_register_channel_knowledge.py
"""
import config_store as cs

OPERATOR = "admin"

TABLE = "ods_user_register_detail_all"

# ── ① 补关键字段注释 ──
COLUMNS = {
    "user_id": "用户ID，关联 ods_tu_customer_all.id（客户主表）。查「某渠道注册客户」时用本字段 JOIN 客户表。",
    "channel_source": (
        "注册渠道（对应「来源渠道」字典）。编码规则：1xx=APP（101安卓/102IOS/103鸿蒙）、"
        "2xx=小程序（201微信/202抖音/203快手/204支付宝）、3xx=H5页面、4xx=后台录入、"
        "5xx=服务台录入、6xx=PC、7xx=三方、3011-3022=官网站群。"
        "查「APP注册客户」= channel_source 百位为1（101/102/103）。"
    ),
    "app_entity_source": "应用主体（对应「应用主体」字典，如 101=车拖车、201=慧拉车、401=客户端、701=联盟拍）。",
    "app_market_source": "APP应用市场（对应「APP应用市场」字典，仅 APP 渠道有值，如 8=苹果应用市场、9=车拖车自有）。",
    "register_source": "注册来源。",
    "activity_source": "活动来源。",
    "application_market": "应用市场（字符串，如「华为应用市场」）。",
}

# ── ② 更新表注释：明确注册渠道字段 + 编码规则 + 关联关系 ──
TABLE_COMMENT = (
    "用户注册明细数据，记录客户与司机的注册时间、设备与IP信息、应用市场及活动渠道来源，并关联邀请人信息。"
    "★ 注册渠道字段 = channel_source（编码规则：1xx=APP、2xx=小程序、3xx=H5页面、4xx=后台录入、"
    "5xx=服务台录入、6xx=PC、7xx=三方、3011-3022=官网站群）。"
    "★ 通过 user_id 关联 ods_tu_customer_all.id（客户表本身无渠道字段，渠道只能在本表取）。"
    "★ 查「XX渠道注册客户」需 JOIN 本表并按 channel_source 过滤："
    "APP注册客户 = channel_source 百位为1（101/102/103），小程序注册客户 = 2xx（201/202/203/204）。"
)


def main():
    cs._ensure_ready()

    print("═" * 66)
    print("① 补字段注释")
    print("═" * 66)
    for cname, comment in COLUMNS.items():
        n = cs.set_column_comment(TABLE, cname, comment,
                                  source="manual", operator=OPERATOR)
        print(f"  {'✅' if n else '⚠️'} {TABLE}.{cname}  ({n} 行)")

    print()
    print("② 更新表注释")
    n = cs.set_table_comment(TABLE, TABLE_COMMENT, source="manual", operator=OPERATOR)
    print(f"  {'✅' if n else '⚠️'} {TABLE} 表注释已更新")

    print()
    print("完成。★ 需【重建索引 / 重启服务】后，字段/表注释才注入 prompt。")


if __name__ == "__main__":
    main()
