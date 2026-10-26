# -*- coding: utf-8 -*-
"""
colname.py —— 列名 → 中文名

★ 它解决什么问题？
  结果表里"算出来的列"（COUNT(*) AS driver_count、toStartOfMonth(...) AS month）
  没有对应的真实字段，也就没有字段注释可查 ——
  如果不命中指标，这些列的表头就只能显示英文（用户看不懂 driver_count 是啥）。

  但这些列名是【模型自己起的】，语义通常很清楚：
      driver_count → 司机数      order_count → 订单数
      finish_rate  → 完成率      avg_distance → 平均距离
  所以按词翻译就够了，比留英文强得多。

★ 为什么只对"计算列"翻译，不对真实字段翻译？
  计算列的列名是模型现起的，起名时就是为了表达含义；
  而真实字段的名字是历史遗留的，直译常常出错 ——
  比如 `order_new_old`（订单新旧标识）会被拆成"订单+新增+存量" → "订单新增存量"，
  看着像中文，其实完全是另一回事。
  **翻译错了比不翻更糟**，所以真实字段只认注释，不猜列名。

★ 为什么"有词不认识就整个不翻"？
  避免出现"司机count""订单amount"这种半中半英的东西 ——
  那种表头不比纯英文好看，还更容易让人误解。
  要么整列翻成中文，要么保持英文原样，不做中间态。
"""
import re

# ═══════════════════════════════════════════════════════════
# 词级词典：英文词 → 中文
# ═══════════════════════════════════════════════════════════
#   ★ 翻成空串的是"连接词/无实义词"：driver_id → "司机"（而不是"司机编号"）
#     数字类后缀（id/no/code）留在中文里反而啰嗦。
WORD_CN = {
    # ── 聚合 / 度量 ──
    "count": "数", "cnt": "数", "num": "数", "number": "数", "qty": "数量",
    "amount": "金额", "amt": "金额", "money": "金额", "fee": "费用",
    "total": "总", "sum": "合计", "avg": "平均", "average": "平均",
    "mean": "均值", "max": "最大", "min": "最小", "median": "中位数",
    "rate": "率", "ratio": "占比", "percent": "占比", "pct": "占比",
    "price": "价格", "cost": "成本", "profit": "利润", "income": "收入",
    "gmv": "GMV", "discount": "折扣", "tip": "小费", "balance": "余额",
    "distance": "距离", "weight": "重量", "volume": "体积", "duration": "时长",
    # ── 时间 ──
    "month": "月份", "day": "日期", "date": "日期", "week": "周",
    "year": "年", "quarter": "季度", "hour": "小时", "minute": "分钟",
    "time": "时间", "today": "当天", "yesterday": "昨天",
    "daily": "每日", "weekly": "每周", "monthly": "每月", "yearly": "每年",
    # ── 业务实体 ──
    "order": "订单", "driver": "司机", "customer": "客户", "user": "用户",
    "vehicle": "车辆", "car": "车辆", "city": "城市", "province": "省份",
    "district": "区县", "area": "区域", "region": "区域",
    "company": "公司", "channel": "渠道", "source": "来源",
    "status": "状态", "type": "类型", "name": "名称", "address": "地址",
    "phone": "电话", "level": "等级", "grade": "等级",
    "category": "类别", "kind": "种类", "nature": "性质",
    # ── 动作 / 状态 ──
    "new": "新增", "old": "存量", "active": "活跃",
    "reg": "注册", "register": "注册", "login": "登录",
    "finish": "完成", "complete": "完成", "cancel": "取消",
    "success": "成功", "fail": "失败",
    # ── 连接词 / 无实义（翻成空）──
    "id": "", "no": "编号", "code": "编码", "of": "", "by": "", "per": "每",
}


def translate(col):
    """
    把列名整列翻成中文；翻不了就返回空串（保持英文，不做半中半英）

    例：driver_count → 司机数    month → 月份    finish_rate → 完成率
        place_order_lola → ""（lola 不认识，整列不翻）
    """
    toks = [t for t in (col or "").lower().split("_") if t]
    if not toks:
        return ""
    parts = []
    for t in toks:
        if t not in WORD_CN:
            return ""                     # 有词不认识 → 整列不翻
        parts.append(WORD_CN[t])
    out = "".join(parts).strip()
    #   必须真的产出中文才算翻成功（"id" → 空串这种情况要挡掉）
    return out if re.search(r"[\u4e00-\u9fa5]", out) else ""


# ═══════════════════════════════════════════════════════════
# 同名列消歧
# ═══════════════════════════════════════════════════════════
#  ★ 背景：表头改成"只显示中文"之后，「本周和上周的订单量对比」
#    两列都叫"订单量"，用户根本分不清哪列是哪列（以前还能靠下面的英文字段名勉强认）。
#    所以多列撞名时，要用列名里的修饰词把它们区分开。
MODIFIER_CN = {
    "this_week": "本周", "last_week": "上周", "prev_week": "上周", "next_week": "下周",
    "current_week": "本周", "cur_week": "本周",
    "this_month": "本月", "last_month": "上月", "prev_month": "上月", "next_month": "下月",
    "current_month": "本月", "cur_month": "本月",
    "this_year": "今年", "last_year": "去年", "prev_year": "去年",
    "current_year": "今年", "cur_year": "今年",
    "this_quarter": "本季度", "last_quarter": "上季度",
    "current_quarter": "本季度", "cur_quarter": "本季度",
    "this_season": "本季", "last_season": "上季",
    "today": "当天", "yesterday": "昨天", "tomorrow": "明天",
    "current": "当前", "cur": "当前", "prev": "上期", "previous": "上期",
    "new": "新增", "old": "存量",
    "total": "累计", "avg": "平均", "max": "最大", "min": "最小",
    "completed": "已完成", "canceled": "已取消", "cancelled": "已取消",
    "first": "首次", "second": "第二次", "third": "第三次",
    "inner": "内部", "outer": "外部", "online": "线上", "offline": "线下",
    "male": "男", "female": "女",
}


def pick_modifier(col, siblings):
    """
    从列名里挑出能与同名列区分的修饰词，翻成中文

    做法：按 `_` 切词后，用 3/2/1 词的滑动窗口查词典（长的优先，长词更精确），
         并要求这个词组【不出现在同名的其它列里】—— 不然它就没有区分作用。
    """
    toks = col.split("_")
    for n in (3, 2, 1):
        for i in range(len(toks) - n + 1):
            gram = "_".join(toks[i:i + n])
            if gram not in MODIFIER_CN:
                continue
            if any(gram in o for o in siblings if o != col):
                continue                      # 别的列也有这个词 → 区分不了
            return MODIFIER_CN[gram], gram
    return None, None


def diff_tokens(col, siblings):
    """词级差异：这个列名里、其它同名列都没有的词（按原顺序拼回）"""
    other = set()
    for o in siblings:
        if o != col:
            other.update(o.split("_"))
    diff = [t for t in col.split("_") if t and t not in other]
    return "_".join(diff)


def disambiguate(labels, columns):
    """
    就地修改 labels：把重名的中文名加上区分前缀

    例：this_week_order_count / last_week_order_count 都叫"订单量"
        → "本周订单量" / "上周订单量"
    """
    seen = {}
    for c in columns:
        zh = labels[c]["zh"]
        if zh:
            seen.setdefault(zh, []).append(c)
    dup = {zh: cols for zh, cols in seen.items() if len(cols) > 1}
    if not dup:
        return

    for zh, cols in dup.items():
        for c in cols:
            cn, _raw = pick_modifier(c, cols)
            if cn:
                labels[c]["zh"] = f"{cn}{zh}"
            else:
                #   翻不出来也别硬翻 —— 括号标出列名的差异部分，至少能区分。
                #   宁可是"金额（north）"，也好过两列都叫"金额"没法认。
                raw = diff_tokens(c, cols)
                if raw:
                    labels[c]["zh"] = f"{zh}（{raw}）"
