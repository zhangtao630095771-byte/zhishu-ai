# -*- coding: utf-8 -*-
"""
enum_parse.py —— 从字段注释里解析「编码 → 含义」

★ 为什么需要它？
  库里的字段注释经常已经把枚举含义写清楚了：

      carstate     "车辆状况，1能启动，2不能启动"
      vehicle_type "板车分类，1无要求，2三吨板，3五吨板…11七位板"
      paymode      "支付模式，1全额支付，2付定金，3到付"

  但结果表里显示的还是 `1`、`2` —— 用户看不懂。
  既然注释里已经有这层信息，就该【自动用起来】，
  而不是要求每个枚举字段都去手工配一份数据字典。
  （本库实测：89 个枚举字段，只有 3 个配了字典 —— 剩下 86 个的编码全是裸数字。）

★ 它和数据字典什么关系？
  两者都是「编码 → 中文」，但来源不同、适用场景不同：
    数据字典 —— 人工独立配置的编码表，适合【跨表通用】的编码（如渠道 app_entity_source）
    注释解析 —— 就地取材，适合【这一列专属】的枚举（如 carstate 车辆状况）
  展示时合并使用：字典优先（人工明确配过），注释兜底。

★ 为什么要求"至少 2 对"才算数？
  单对的风险太高：注释里写"最近30天"会被解析成 {30: "天"}，
  于是一个值为 30 的列会被翻译成"30 天"—— 看着有道理，其实是错的。
  【翻译错了比不翻译更糟】，因为用户会信它。
  所以宁可少翻一点，也不给假信息。（本项目一贯原则：宁可留空，不贴错标签）
"""
import re


# 匹配「数字 + 可选分隔符 + 含义」
#   能覆盖真实注释里的各种写法：
#     "1能启动"      "1.线上收款"     "1=G车联"
#     "2：待接单"    "3、已送达"      "1 无要求"
#   编码限制 1~3 位：枚举值基本都是小数字，
#   这样 "最近30天" 里的 30 也不会被当成编码（它本身也过不了"至少 2 对"那一关）。
_PAIR = re.compile(
    r"(\d{1,3})\s*[=:：.、,，\-]?\s*([A-Za-z\u4e00-\u9fa5][A-Za-z0-9\u4e00-\u9fa5]{0,11})"
)

MIN_PAIRS = 2       # 至少解析出这么多对，才认为这段注释是在说明枚举取值


def parse_values(comment):
    """
    从注释里解析出 {编码: 含义}

    例：
        "订单状态：2待接单 3已接单 4配送中 5已送达 6已完成 7已取消"
            → {"2": "待接单", "3": "已接单", ...}
        "车辆状况，1能启动，2不能启动"
            → {"1": "能启动", "2": "不能启动"}
        "订单成交金额（客户支付总额，单位元）"
            → {}                      （没有枚举，正常）

    解析不出、或不够 MIN_PAIRS 对时返回空字典（宁可不翻，也别翻错）。
    """
    if not comment:
        return {}
    pairs = {}
    for m in _PAIR.finditer(comment):
        code, label = m.group(1), m.group(2).strip()
        if label and code not in pairs:
            pairs[code] = label          # 同一个编码只取第一次出现的含义
    if len(pairs) < MIN_PAIRS:
        return {}
    return pairs


def mentions_values(comment):
    """
    这段注释是否说明了取值含义（等价于"能解析出至少 2 对"）

    ★ 和 parse_values 的关系：
      它就是 `bool(parse_values(comment))`，单独留一个函数是因为
      "判断有没有"比"拿到映射"读起来更清楚（gaps.py 只需要判断）。

    ★ 注意适用范围：它只认【数字编码】，且要求至少 2 对。
      这是给"结果表翻译"用的保守规则，不适合用来判断
      "这条注释到底说清楚了没" —— 那种场景请用 covers_values()。
    """
    return bool(parse_values(comment))


def _components(value):
    """
    把一个取值拆成它需要被解释的【原子编码】

    例：  "0"            → ["0"]          （普通单值）
         "0,1"          → ["0", "1"]     （逗号分隔的多选字段）
         '["1","2"]'    → ["1", "2"]     （JSON 数组存的多选字段）

    ★ 为什么要拆？
      多选字段的真实取值会组合爆炸：pay_mode_type 存的是 '["1","2","3"]' 这种字符串，
      字面上永远不会出现在注释里（注释只写「1全额支付，2付定金，3到付」）。
      不拆的话这类字段会被永久判成缺口 —— 而它其实早就解释清楚了。
      噪音会让用户不再相信这份清单，这和漏报一样有害。
    """
    s = str(value).strip()
    if not s:
        return []
    # 去掉 JSON 数组的外壳：[...] / "..." 之后按逗号拆
    s = re.sub(r'^\[|\]$', "", s).replace('"', "").replace("'", "")
    return [p.strip() for p in s.split(",") if p.strip()]


def components(value):
    """公开别名：把组合取值拆成原子编码（见 _components，供 gaps 等复用，避免重复实现）"""
    return _components(value)


def mentioned_codes(comment):
    """
    注释里【声称存在】的所有数字编码（不要求 ≥2 对）

    ★ 和 parse_values 的区别：
      parse_values 为了"翻译不出别硬翻"要求至少 2 对 —— 那是给【结果表显示】的保守规则。
      这里反过来：注释里只要出现「数字=含义」的样子，就认为它在"声称"这个编码存在，
      哪怕只有一对。因为我们找的是"注释说有、数据没有"的【幽灵值】，漏掉一个就少拦一个。

    ★ 为什么只认数字编码？
      字符串枚举（如 alipay=支付宝）没法从注释可靠地反推出"它声称还有哪些值"，
      硬去抽容易误判；数字枚举的"N=含义"模式很规整，抽取可靠。
      宁可不报也不误报 —— 这条注释的真实风险（漏了值）已由 covers_values 管着。
    """
    if not comment:
        return set()
    return {m.group(1) for m in _PAIR.finditer(comment)}


def covers_values(comment, values):
    """
    注释是否【覆盖】了这些真实取值（每一个都提到才算）

    ★ 为什么不直接用 mentions_values / parse_values？
      那套规则只认数字编码（"2=待接单"）且要至少 2 对，是给【结果表翻译】
      刻意保守用的（翻错比不翻更糟）。
      但拿它判断"这条注释说清楚了没"会漏判 —— 真实踩到的例子：

          ex_mch_channel "外部机构渠道code（空=非外部机构渠道，alipay=支付宝渠道）"

      这条把仅有的两个取值解释得明明白白，可 parse_values 一个数字对都匹配不到
      （编码是「空」和「alipay」），于是 scans 说它是"缺口"，
      用户看到清单上挂着一条显然已经写清楚的字段，下次就不想看这份清单了。

      而 scanning 到这一步时【真实取值就在手边】，逐个比对是最直接也最可靠的判据 ——
      不需要再靠"这段注释长得像不像枚举说明"去猜。

    ★ 这个判据必须和 suggest 生成时的校验【共用同一份】：
      两处各写一遍，就会出现"AI 生成时判及格 → 落库 → 缺口扫描又说不及格"，
      用户会觉得系统在自相矛盾。

    ★ 空值特殊处理：'' in 任意文本 恒为 True，
      混在列表里等于没校验，必须要求注释里出现「空」这个字。
      同理，单个字符的值（如 '0'）容易误命中，
      但只要它是这个字段真实的取值，提到了就算数 —— 判据只对，不对臆测的值。
    """
    if not comment or not values:
        return False
    #   每个【原子编码】都要被提到（多选字段拆开后逐个查）
    needed, has_empty = set(), False
    for v in values:
        if str(v) == "":
            has_empty = True
            continue
        needed.update(_components(v))
    for code in needed:
        if code not in comment:
            return False
    if has_empty and "空" not in comment:
        return False
    return True
