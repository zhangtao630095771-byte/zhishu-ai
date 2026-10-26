# -*- coding: utf-8 -*-
"""
calendar_util.py —— 日历 / 节假日感知（Time & Festival Awareness）

★ 解决的问题：
  用户问题里常出现「25号前7天」「去年中秋节前7天」「国庆后3天」这类口径。
  模型本身【不知道今天几号】，也不认识农历节日，只能拒绝回答或瞎猜。
  本模块把这类表达解析成【明确的公历日期区间】，注入 Prompt，
  让模型直接按给定区间写 SQL，而不是自己去推算。

★ 数据来源：festivals.json（节日 → 公历日期，可人工校核 / 扩展）
★ 设计原则：
  ① 能解析出明确区间的，直接把区间喂给模型（权威，不让模型再算）；
  ② 解析不出的，至少告诉模型「今天几号」，让它能推算；
  ③ 查不到的年份/节日，明确说明，绝不静默算错日期。

★ 区间口径（务必与业务确认）：
  「节前 N 天」= [节日 - N, 节日 - 1]（不含节日当天）
  「节后 N 天」= [节日 + 1, 节日 + N]（不含节日当天）
  「节日当天」  = [节日, 节日]
  ★「节日假期/黄金周」= 该年的【放假区间】（如国庆假期 = 10-01~10-07）。
    注意这是【区间】不是单日 —— 用户问「国庆假期期间的订单」要的是整段假期。
    区间来自 festivals.json 的 holidays（逐年官方安排）；查不到则按 holiday_defaults
    常规天数估算，并在注入文本里明确标注"估算"，绝不假装权威。
"""
import os
import re
import json
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
_FEST_PATH = os.path.join(HERE, "festivals.json")
_cached = None


def _load():
    """加载节日表（带缓存）"""
    global _cached
    if _cached is None:
        try:
            with open(_FEST_PATH, encoding="utf-8") as f:
                _cached = json.load(f)
        except Exception:
            _cached = {"aliases": {}, "solar": {}, "by_year": {}}
    return _cached


def _alias_map():
    """返回 {别名 → 规范名}；规范名自身也映射到自己"""
    d = _load()
    m = {}
    for canon, alist in (d.get("aliases") or {}).items():
        m[canon] = canon
        for a in (alist or []):
            m[a] = canon
    for n in (d.get("solar") or {}):
        m.setdefault(n, n)
    for n in (d.get("by_year") or {}):
        m.setdefault(n, n)
    return m


def all_festival_aliases():
    """所有节日别名，按长度降序（长词优先，避免「中秋」抢走「中秋节」）"""
    return sorted(_alias_map().keys(), key=len, reverse=True)


def is_festival(word):
    """判断一个词是不是节日名/别名"""
    return word in _alias_map()


def get_festival_date(name, year):
    """取某年某节日的公历 date；查不到返回 None"""
    canon = _alias_map().get(name, name)
    d = _load()
    # ① 公历固定节日
    s = (d.get("solar") or {}).get(canon)
    if s:
        mm, dd = s.split("-")
        return date(year, int(mm), int(dd))
    # ② 逐年农历节日
    v = ((d.get("by_year") or {}).get(canon) or {}).get(str(year))
    if v:
        y, mm, dd = v.split("-")
        return date(int(y), int(mm), int(dd))
    return None


# ═══════════════════════════════════════════════════════════
# ★ 放假区间（"国庆" 是点，"国庆假期" 是区间）
# ═══════════════════════════════════════════════════════════
#  【为什么必须单独处理？】
#    「国庆」= 10-01 一天；但用户问「国庆假期期间的订单」时，要的是整段假期。
#    只按节日表解析会得到一个单日 —— 这正是线上反馈暴露的问题：
#    系统把「国庆假期」理解成 10-01 当天，用户看到日期范围不对。
#
#  【数据从哪来？】
#    ① holidays：逐年【官方放假安排】（festivals.json，人工校核项）
#    ② 查不到 → holiday_defaults 按"常规放假天数"估算，
#       并在返回的 source 里标成 "default" —— 调用方必须把"这是估算"讲清楚，
#       绝不假装权威（本模块一贯原则：宁可说"我不确定"，不可静默算错）。
def get_holiday_range(name, year):
    """
    取某年某节日的放假区间。
    返回 (start, end, source)：
        source="table"   —— 来自 festivals.json 的 holidays（逐年官方安排）
        source="default" —— 按 holiday_defaults 常规天数估算
        查不到（该节日没有放假概念）→ 返回 None
    """
    canon = _alias_map().get(name, name)
    d = _load()

    # ① 逐年官方安排（形如 "2025-10-01~2025-10-08"）
    v = ((d.get("holidays") or {}).get(canon) or {}).get(str(year))
    if v and "~" in v:
        try:
            a, b = v.split("~")
            return date.fromisoformat(a.strip()), date.fromisoformat(b.strip()), "table"
        except ValueError:
            pass                                   # 数据写错就当没查到，走下面的估算

    # ② 常规天数估算
    tpl = (d.get("holiday_defaults") or {}).get(canon)
    if not tpl:
        return None                                # 这个节日本来就没有"假期"一说
    anchor = (tpl.get("anchor") or "festival").strip()
    days = int(tpl.get("days") or 0)
    if days <= 0:
        return None
    if anchor == "festival":
        start = get_festival_date(canon, year)      # 从节日当天起（农历节日用）
    else:
        mm, dd = anchor.split("-")
        start = date(year, int(mm), int(dd))
    if not start:
        return None
    return start, start + timedelta(days=days - 1), "default"


def holiday_source_note(source):
    """把 source 翻译成给用户/模型看的一句话"""
    if source == "table":
        return "按当年放假安排"
    return "按常规放假天数估算，如与当年国务院安排不一致请以官方为准"


def _fmt(d):
    return d.strftime("%Y-%m-%d")


def _range(a, b):
    return f"{_fmt(a)} ~ {_fmt(b)}"


def _weekday_cn(d):
    return "一二三四五六日"[d.weekday()]


# ═══════════════════════════════════════════════════════════
# 时间表达解析
# ═══════════════════════════════════════════════════════════
# 相对年份词 → 相对今天的年偏移
_YEAR_WORDS = {"去年": -1, "上年": -1, "前年": -2, "今年": 0, "本年": 0,
               "同年": 0, "明年": 1, "下年": 1, "后年": 2}


def _resolve_festival_year(canon, year_word, year_num, today):
    """决定节日取哪一年：显式年份 > 相对年份词 > 离今天最近的一次"""
    if year_num:
        return year_num
    if year_word in _YEAR_WORDS:
        return today.year + _YEAR_WORDS[year_word]
    # 未指明：取今年；若今年已过且过去较久，仍按今年（业务常问“今年中秋”）
    return today.year


def parse_time_expressions(question, today=None):
    """
    解析问题中的时间表达，返回 list[dict]：
      {expr, desc, start(date), end(date)}
    已解析过的片段会从待匹配文本中移除，避免重复命中。
    """
    today = today or date.today()
    q = question or ""
    results = []

    def _add(expr, desc, start, end):
        for r in results:
            if r["expr"] == expr:
                return
        results.append({"expr": expr, "desc": desc, "start": start, "end": end})

    # ── ① 节日（含「去年/今年/明年 + 节日 + 前/后 N 天」，以及「XX假期」这类区间表达）──
    alias_alt = "|".join(re.escape(a) for a in all_festival_aliases())
    fest_re = re.compile(
        r"(?P<yw>去年|上年|前年|今年|本年|明年|下年|后年)?"
        r"(?P<yn>\d{4})?年?"
        r"(?P<fest>" + alias_alt + r")"
        # ★「假期/假日/黄金周/期间」等后缀 → 说明用户要的是【整段假期】而不是节日当天
        r"(?P<hol>(?:假期|假日|放假|长假|小长假|黄金周|期间)(?:期间)?)?"
        r"(?P<dir>前|后|之前|之后|以前|以后)?"
        r"(?P<n>\d+)?\s*(?P<unit>天|日)?"
    )
    consumed = []
    for m in fest_re.finditer(q):
        canon = _alias_map().get(m.group("fest"), m.group("fest"))
        yw = m.group("yw") or ""
        yn = int(m.group("yn")) if m.group("yn") else None
        year = _resolve_festival_year(canon, yw, yn, today)
        fdate = get_festival_date(canon, year)
        expr = m.group(0).strip()
        if not fdate:
            _add(expr, f"{year}年{canon}的日期不在节日表中（无法解析）", None, None)
            consumed.append(m.span())
            continue
        direction = m.group("dir") or ""
        n = int(m.group("n")) if m.group("n") else None
        # 「假期」意图：显式写了 假期/假日/黄金周，或别名本身就是"十一黄金周"
        hol = m.group("hol") or ""
        holiday_mode = bool(hol) or ("黄金周" in m.group("fest"))

        if holiday_mode and not n and not direction:
            #   ★ 用户要的是【放假区间】，不是节日当天。
            rng = get_holiday_range(canon, year)
            if rng:
                start, end, src = rng
                desc = f"{year}年{canon}放假区间（{holiday_source_note(src)}）"
            else:
                start = end = fdate
                desc = f"{year}年{canon}当天（该节日无放假安排，按当天处理）"
        elif n and direction in ("前", "之前", "以前"):
            start, end = fdate - timedelta(days=n), fdate - timedelta(days=1)
            desc = f"{year}年{canon}({_fmt(fdate)})前{n}天（不含节日当天）"
        elif n and direction in ("后", "之后", "以后"):
            start, end = fdate + timedelta(days=1), fdate + timedelta(days=n)
            desc = f"{year}年{canon}({_fmt(fdate)})后{n}天（不含节日当天）"
        else:
            start = end = fdate
            desc = f"{year}年{canon}当天"
        _add(expr, desc, start, end)
        consumed.append(m.span())
    # 从文本中移除已解析的节日片段（替换等长空格，保持其它匹配位置不乱）
    for s, e in consumed:
        q = q[:s] + " " * (e - s) + q[e:]

    # ── ② 「N 号 / N 日」→ 本月该日（含「N号前M天」）──
    # (?<![\d月])：排除「X月N日」与「18日」中的子串，避免把「6月1日」拆成「1日」、
    #              「9月18日」拆成「8日」这类误匹配
    day_re = re.compile(r"(?<![\d月])(?P<d>\d{1,2})\s*[号日](?:(?P<dir>前|后)\s*(?P<n>\d+)\s*[天日])?")
    for m in day_re.finditer(q):
        dnum = int(m.group("d"))
        if not (1 <= dnum <= 31):
            continue
        try:
            base = today.replace(day=dnum)
        except ValueError:
            continue
        if m.group("n"):
            n = int(m.group("n"))
            if m.group("dir") == "前":
                start, end = base - timedelta(days=n), base - timedelta(days=1)
                desc = f"{base.month}月{dnum}号前{n}天（不含当天）"
            else:
                start, end = base + timedelta(days=1), base + timedelta(days=n)
                desc = f"{base.month}月{dnum}号后{n}天（不含当天）"
        else:
            start = end = base
            desc = f"{base.month}月{dnum}号当天"
        _add(m.group(0).strip(), desc, start, end)

    # ── ③ 「近 N 天 / 最近 N 天 / 前 N 天」──
    for m in re.finditer(r"(?:近|最近|过去)\s*(\d+)\s*[天日]", q):
        n = int(m.group(1))
        _add(m.group(0).strip(), f"最近{n}天（含今天）",
             today - timedelta(days=n - 1), today)
    for m in re.finditer(r"(?<![节号\d])前\s*(\d+)\s*[天日]", q):
        n = int(m.group(1))
        _add(m.group(0).strip(), f"今天前{n}天（不含今天）",
             today - timedelta(days=n), today - timedelta(days=1))

    # ── ④ 相对日 / 周 / 月 / 年 ──
    specials = {
        "今天": (today, today), "今日": (today, today),
        "昨天": (today - timedelta(days=1), today - timedelta(days=1)),
        "昨日": (today - timedelta(days=1), today - timedelta(days=1)),
        "前天": (today - timedelta(days=2), today - timedelta(days=2)),
        "明天": (today + timedelta(days=1), today + timedelta(days=1)),
        "后天": (today + timedelta(days=2), today + timedelta(days=2)),
    }
    for word, (s, e) in specials.items():
        if word in q:
            _add(word, word, s, e)
    if "本周" in q or "这周" in q:
        s = today - timedelta(days=today.weekday())
        _add("本周", "本周（周一至周日）", s, s + timedelta(days=6))
    if "上周" in q:
        s = today - timedelta(days=today.weekday() + 7)
        _add("上周", "上周（周一至周日）", s, s + timedelta(days=6))
    if "本月" in q or "当月" in q:
        s = today.replace(day=1)
        nxt = (s + timedelta(days=32)).replace(day=1)
        _add("本月", "本月（1号至月末）", s, nxt - timedelta(days=1))
    if "上月" in q or "上个月" in q:
        first = today.replace(day=1)
        last_prev = first - timedelta(days=1)
        _add("上月", "上月（1号至月末）", last_prev.replace(day=1), last_prev)
    if "今年" in q or "本年" in q:
        _add("今年", f"{today.year}年全年", date(today.year, 1, 1), date(today.year, 12, 31))
    if "去年" in q and not any(r["expr"] == "去年" for r in results):
        y = today.year - 1
        _add("去年", f"{y}年全年", date(y, 1, 1), date(y, 12, 31))

    # ── ⑤ 日期字面量：YYYY年M月D日 / M月D日 / M月 / YYYY年 ──
    def _open_tail(m):
        """日期后紧跟「开始/起/以来」→ 区间为 [该日, 今天]（起，至当前）"""
        return any(w in q[m.end():m.end() + 4] for w in ("开始", "起", "以来"))

    for m in re.finditer(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", q):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dt = date(y, mo, d)
            if _open_tail(m):
                _add(m.group(0).strip(), f"{y}年{mo}月{d}日起（至今天）", dt, today)
            else:
                _add(m.group(0).strip(), f"{y}年{mo}月{d}日当天", dt, dt)
        except ValueError:
            pass
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", q):
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            dt = date(today.year, mo, d)
            if _open_tail(m):
                _add(m.group(0).strip(), f"{today.year}年{mo}月{d}日起（至今天）", dt, today)
            else:
                _add(m.group(0).strip(), f"{today.year}年{mo}月{d}日当天", dt, dt)
        except ValueError:
            pass
    for m in re.finditer(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?!\d)", q):
        y, mo = int(m.group(1)), int(m.group(2))
        s = date(y, mo, 1)
        nxt = (s + timedelta(days=32)).replace(day=1)
        _add(m.group(0).strip(), f"{y}年{mo}月", s, nxt - timedelta(days=1))
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*月(?!\s*\d)", q):
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            s = date(today.year, mo, 1)
            nxt = (s + timedelta(days=32)).replace(day=1)
            _add(m.group(0).strip(), f"{today.year}年{mo}月", s, nxt - timedelta(days=1))

    # 去掉无法解析（start 为 None）的项里纯装饰的，保留提示项
    return results


# ═══════════════════════════════════════════════════════════
# 生成注入 Prompt 的「时间口径」文本
# ═══════════════════════════════════════════════════════════
_KEY_FESTS = ["元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节", "除夕"]


def festival_reference(today=None):
    """列出近年关键节日的公历日期，供模型核对（防瞎猜）"""
    today = today or date.today()
    lines = []
    for name in _KEY_FESTS:
        parts = []
        for y in (today.year - 1, today.year, today.year + 1):
            fd = get_festival_date(name, y)
            if fd:
                parts.append(f"{y}年 {_fmt(fd)}")
        if parts:
            lines.append(f"  - {name}：" + "；".join(parts))
    return "\n".join(lines)


def build_time_context(question, today=None):
    """
    生成「时间口径」注入文本（权威段）。
    始终包含当前日期；能解析出区间的直接给出区间。
    """
    today = today or date.today()
    lines = []
    lines.append("# 时间口径（系统已解析，权威，必须直接采用）")
    lines.append(f"当前日期：{_fmt(today)}（星期{_weekday_cn(today)}）")

    exprs = [e for e in parse_time_expressions(question, today)]
    ok = [e for e in exprs if e["start"]]
    bad = [e for e in exprs if not e["start"]]

    if ok:
        lines.append("本次问题中识别到的时间表达，已解析为公历区间（左闭右闭，含首尾）：")
        for e in ok:
            lines.append(f"  - 「{e['expr']}」 = {_range(e['start'], e['end'])}（{e['desc']}）")
        lines.append("  ★ 请把上述区间【直接写进 WHERE】，不要再根据“今天”自行推算；"
                     "若为同比/环比对比，请按对应多个区间分别统计并分列输出。")
    else:
        lines.append("（本次问题未识别到明确的时间表达。若问题隐含相对时间，"
                     "请基于上面的“当前日期”推算，并在 SQL 注释中写明所采用的区间。）")
    for e in bad:
        lines.append(f"  ⚠ 「{e['expr']}」无法解析为日期（{e['desc']}），如需该口径请补充年份或改用公历日期。")

    ref = festival_reference(today)
    if ref:
        lines.append("")
        lines.append("近年关键节日的公历日期（仅供参考核对，勿与上方解析结果冲突）：")
        lines.append(ref)
    return "\n".join(lines)


def summary_line(question, today=None):
    """一行摘要（用于返回结果里的 info 展示）"""
    today = today or date.today()
    ok = [e for e in parse_time_expressions(question, today) if e["start"]]
    if not ok:
        return ""
    parts = [f"{e['expr']}={_range(e['start'], e['end'])}" for e in ok]
    return "时间口径：" + "；".join(parts)


if __name__ == "__main__":
    q = "查询25号前7天同比去年中秋节前7天发单完单率"
    print(build_time_context(q, date(2026, 9, 24)))
    print()
    print("summary:", summary_line(q, date(2026, 9, 24)))
