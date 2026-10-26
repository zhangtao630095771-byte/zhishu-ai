# -*- coding: utf-8 -*-
"""
config_store.py —— 配置存储（把"会变的配置"从代码里搬到数据库）
★ 这是「在线 Prompt 配置」+「在线 RAG 配置」的核心实现
★ 核心思想：配置和知识都是"数据"，应该存在数据库里，而不是写在代码里

存了两类东西：
    ① prompts        —— Prompt 规则、角色设定
    ② metrics        —— 指标定义（RAG 的核心知识）
    ③ table_comments —— 表的中文业务注释
"""
import sqlite3      # 用 SQLite 存配置（真实企业里可能用 MySQL + Web 后台）
import json         # ★ JSON 工具箱：用来把"列表"存进数据库
import os           # ★ 读环境变量（配置库路径要能在生产环境指向挂载卷）
import hashlib      # ★ 口令哈希（PBKDF2 加盐），标准库自带，不引第三方依赖
import hmac         # ★ 恒定时间比较，防"按响应时间猜口令"的时序攻击
import secrets      # ★ 生成随机盐 / 会话 token（密码学安全随机源）
#   ★ 为什么需要 json？
#   数据库字段只能存简单值（文本/数字），存不了 Python 的列表。
#   所以用 json.dumps 把列表转成字符串存进去，读出来再用 json.loads 转回来。
#   【SQL 类比】≈ 把数组序列化成字符串存进 TEXT 字段

# ═══════════════════════════════════════════════════════════
# 配置库文件（和业务库 demo_shop.db 分开，各管各的）
# ═══════════════════════════════════════════════════════════
#  ★ 为什么路径要能用环境变量覆盖？
#    这个文件里装的是【全部知识资产】——Prompt 定制、指标口径、字段注释、
#    数据字典、实体关系、账号……它是整个系统最值钱的东西，绝不能丢。
#    容器部署时进程工作目录是 /app（代码在镜像里，重建即还原），
#    如果还用相对路径，数据库就落在容器可写层里 —— 容器一重建，知识库和账号全没了。
#    所以生产用 CONFIG_DB=/app/data/agent_config.db 指到【挂载卷】上。
#    默认值保持原样，开发环境行为完全不变。
CONFIG_DB = os.getenv("CONFIG_DB", "agent_config.db")


# ═══════════════════════════════════════════════════════════
# 默认配置 —— 只在"首次建库"时写入，之后以数据库里的为准
# ★ 注意：这些内容【已经不在代码逻辑里】了，它们只是"初始种子数据"
# ★ 一旦入库，后续修改都走 admin.py，不再改这个文件
# ═══════════════════════════════════════════════════════════
DEFAULT_PROMPTS = {
    "sql_rules": """# 规则
1. 只能使用上面提供的表和字段，禁止编造不存在的表和字段
2. 只能生成 SELECT 查询，禁止 DELETE / UPDATE / DROP / INSERT
3. 时间范围一律用左闭右开：create_time >= '起始' AND create_time < '结束'
4. 统计数量用 COUNT(*)
5. 只输出 SQL，不要任何解释，不要用 ``` 包裹
6. 计算比率时，用 CASE WHEN 分别统计分子分母，禁止把分子条件写进 WHERE
   错误写法：WHERE status = 7 AND 时间   → 分母被提前过滤，结果恒等于 100%
   正确写法：SUM(CASE WHEN status = 7 THEN 1 ELSE 0 END) / COUNT(*)
7. 聚合函数或除法表达式，必须用 COALESCE(整个表达式, 0) 包住，避免空结果返回 NULL
   ★ 注意位置：COALESCE 要套在【最外层】
     错误：COALESCE(SUM(x), 0) / COUNT(*)      → 空表时仍是 NULL（除零了）
     正确：COALESCE(SUM(x) * 1.0 / COUNT(*), 0)
   ★ 整数相除请乘 1.0：SUM(x) * 1.0 / COUNT(*)，避免被截断成 0
8. 需要对比多个时间段时，必须用 SUM(CASE WHEN ... THEN 1 ELSE 0 END) 横向并排输出多列
   ★ 禁止用 UNION ALL（会输出多行，列名还会错位）
   ★ 禁止用 GROUP BY（会输出多行，不符合对比场景的列结构约定）
9. ★ 只能查询上面列出的表，严禁凭记忆写出未列出的表名。
   ★★ 铁律（优先于下面所有措辞规则）：只要能靠【合理默认口径】得出 SQL，就必须生成 SQL，
      绝不因此改成拒答。拒绝回答的代价是用户什么都拿不到，远大于"用了默认口径"。
      常见可默认的情形：
        · 没指明业务线（如只说"查询订单量"）→ 按通用订单主表统计；
        · 只问「数据 / 情况 / 表现」没指定指标 → 按最基础的【订单量】统计；
        · 没指明时间 → 按合理默认区间（如近 30 天）并在注释里写明。
   只有确实生成不出 SQL 时，才输出一行以 `--` 开头的说明（不解释原因、不输出 SQL）。
   此时按【真实原因】选措辞 —— 用户靠这句话决定"下一步该补什么"，措辞用错就等于白问：
   (a) 表未授权/不存在：问题需要的业务数据根本不在已列出的表中（未授权或库中无此表）
       → 输出：-- 无法回答：所需数据表不在已授权范围内
   (b) 表都在，但现有字段和口径仍算不出来（缺关键字段 / 缺维度表 / 口径无法解析）
       → 输出：-- 无法回答：现有表结构不足以计算（简短说明缺什么，如"缺少发单时间字段"）
   (c) 表够用，只是【问题没说清】（没讲指标 / 没讲口径 / 有歧义）且确实无法合理默认
       → 输出：-- 需要澄清：<缺什么>；可选：<2~4 个候选口径>
         例：-- 需要澄清：未说明要统计哪个指标；可选：发单量 / 完单量 / 完单率 / 订单金额
       ★ 适用边界（别串门）：本条只用于「用户少说了一个词、补上就能查」的情况；
         若是「补话也查不出来」（缺字段 / 缺维度表），那是 (b)。
       ★ 这一类【不是】"表结构不足"，严禁把 (b) 的情况套成本条的措辞：
         对用户来说，"系统没这个能力"和"你少说了一个词"是完全不同的两件事，
         用错措辞会让用户误判下一步该做什么。
   注意：不要使用"无权限"这类表述，除非 (a) 真的发生。
10. ★ 列的别名（AS 后面）只能用英文、数字、下划线，绝对不要用裸中文
    错误：SELECT SUM(x) AS 完单量 FROM t     → ClickHouse 直接语法报错(code 62)
    正确：SELECT SUM(x) AS order_count FROM t
    若确实需要中文列名，必须用反引号包起来：AS `完单量`
    （这条踩过坑：中文别名让每条查询都白跑一次模型重试，翻倍延迟和成本）
11. ★ 时间戳类型（高频易错）：本库 ctime 类字段（dwd_tu_order_all.ctime、
    ods_tu_customer_all.ctime）是 Int32 秒级时间戳，不是 DateTime。参与 dateDiff /
    日期运算前必须 toDateTime(字段) 转换（如 toDateTime(ctime)）；
    ods_tu_order_pricing_record_all.pricing_time 是 DateTime，可直接用。
    类型不匹配会被 ClickHouse 拒绝（code 43: Illegal type for dateDiff）。
12. ★ 禁止使用 SETTINGS 子句（如 SETTINGS join_algorithm / max_memory_usage 等）。
    这类执行参数由系统统一控制，模型不要写；写在末尾会导致 LIMIT 注入位置非法
    （code 62 语法错误）。你只需写纯 SELECT 查询体即可。
13. ★ 嵌套子查询（FROM (SELECT ...) AS x / WITH ... AS x）的【别名作用域】铁律：
    每一层子查询的 SELECT 列表，只能用【本层 FROM/JOIN 里定义的表别名 + 它们的真实列】，
    不得引用尚未定义的列别名，也不得引用外层子查询的别名。
    - 对齐键（用于最外层 LEFT JOIN ON 的键，如按日期对齐）必须在【每个子查询内部】
      用本层真实列显式算出来并起相同名字，例如都写：
        toDate(toDateTime(c.ctime)) AS stat_date
      而绝不能写 `c.stat_date`（c 是客户表，根本没有 stat_date 列 → code 47 报错）。
    - 最外层 SELECT 才能引用子查询别名，如 `c_stat.stat_date` / `o1.order_users_1d`。
    - 自查：子查询内部的 SELECT 里若出现"本层 FROM 的表没有的列名"，就是引用了
      外层别名或尚未定义的别名，必须改成本层真实列表达式。
14. ★ JOIN 的 ON 子句【只能写等值条件】（如 `a.customer = b.customer`）。
    ClickHouse 不支持在 JOIN ON 里写不等号 / 函数比较（如 `dateDiff(...) >= 0`、
    `toDateTime(a.ctime) < b.pricing_time`），会报 code 403 Unsupported JOIN ON conditions。
    时间窗口 / 不等式判断必须放在：
      (a) WHERE 子句（先按等值 JOIN 展开后过滤），或
      (b) 聚合函数里的 CASE WHEN（如 countIf(dateDiff('day', p.pricing_time, toDateTime(o.ctime)) >= 0)）。
    同理，需要"某时间之后"的关联（如询价后发单），用 `LEFT JOIN ... ON o.customer = p.customer`
    等值关联，再用 WHERE/CASE WHEN 判时间先后，不能把时间不等式写进 ON。
15. ★ 防笛卡尔放大范式（多表算"率"的铁律；违反会明细×明细行数乘积暴涨 → 内存爆炸 code 241 / 超时 code 159）：
    凡是【客户表 × 订单表 × 询价表】这类多表算率，绝不允许把「订单明细」和「询价明细」直接 JOIN
    （明细×明细 = 笛卡尔放大，行数随各表记录数乘积爆炸）。正确步骤：
      (a) 先用 CTE 取「基准集」——如每天的新注册用户：
            new_users AS (SELECT id AS customer,
                                 toDate(toDateTime(ctime)) AS reg_date,
                                 toStartOfMonth(toDateTime(ctime)) AS reg_month
                          FROM ods_tu_customer_all WHERE <时间范围>)
      (b) 订单、询价【各自先按 customer 预聚合（GROUP BY customer）】，再 1:1 JOIN 回基准集：
            - 订单预聚合：每个客户一次性算出 1/3/7 天发单标记（用 max(bool) 聚合，一次出多窗口，
              不要写多个子查询）：
                max(dateDiff('day', u.reg_date, toDate(toDateTime(o.ctime))) <= 1) AS has_1d
            - 询价预聚合：每个客户取「注册当月第一次询价时间」并只关联新用户：
                min(p.pricing_time) AS first_pricing_time
                WHERE toStartOfMonth(p.pricing_time) = u.reg_month
      (c) 最后 new_users LEFT JOIN 各预聚合结果（都是 customer 维度 1:1），按 reg_date 汇总输出。
    ★ 时间窗口 / 不等式判断一律放进【聚合函数内的比较】或 WHERE；JOIN ON 只写等值（见规则14）。
    ★ 自查：若 SQL 里出现「询价明细表 JOIN 订单明细表」且双方都没先 GROUP BY customer，
          就是笛卡尔放大，必须改成「各自预聚合后再 1:1 关联」。
16. ★ 取「自然月/月初」必须用 toStartOfMonth(日期)，【禁止】dateTrunc('MONTH', ...) / dateTrunc('month', ...)。
    本库 ClickHouse 版本不认 MONTH 作为 datepart 名称，会报 code 36 (BAD_ARGUMENTS:
    MONTH doesn't look like datepart name in dateTrunc)。
    同理：取年初用 toStartOfYear(日期)、取日初用 toStartOfDay(日期)、取周初用 toStartOfWeek(日期)。
    ★ 正确示例：toStartOfMonth(toDateTime(ctime)) AS reg_month（绝不要写成 dateTrunc('MONTH', ctime)）。\n17. ★ 所有「率 / 占比 / 比例」类字段（列名含 rate / ratio / 率 / 占比 / 比例，或语义是比率）默认保留【小数点后 4 位】：用 round(比率表达式, 4) 包裹。\n    例：发单率写成 round(COALESCE(SUM(has_1d) * 1.0 / COUNT(*), 0), 4) AS rate_1d。\n    除法一定要先 * 1.0（或 / 1.0）保证出小数，再 round(..., 4)；不要保留 6 位以上多余小数，也不要截断成整数。\n    默认输出小数形式（如 0.1234），【不要】乘成百分比（如 12.34%），除非用户明确要求"百分比 / 百分号"。\n    适用：发单率、询价后发单率、转化率、留存率、占比等一切比率指标。
18. ★ 生成的 SQL 每个 SELECT 输出列都要带【有业务含义的中文别名】（用反引号包裹，例如 `统计日期`、`新注册用户数`、`1天内发单率`），方便阅读与复制到外部工具。注意：中文别名必须反引号包裹（见规则10），禁止裸中文；维度列也允许英文别名，但计算/比率列强烈建议中文别名。
19. ★ 时间维度的【显示格式】（展示层要求，不得改变时间过滤条件）：
    - 按「月」聚合：月份列必须输出 `YYYY-MM`（如 2026-06），禁止输出完整日期（2026-06-01）。
      ClickHouse 写法：formatDateTime(toStartOfMonth(时间字段), '%Y-%m') AS `月份`
      SQLite 写法：strftime('%Y-%m', 时间字段) AS `月份`
    - 按「年」聚合：输出 `YYYY`，如 formatDateTime(toStartOfYear(时间字段), '%Y') AS `年份`
    - 按「日」聚合：输出 `YYYY-MM-DD`（toDate 即可）
    ★ 时间过滤条件仍按规则 3 保持左闭右开；本条只影响 SELECT 输出列的表达方式。""",

    "role": "你是一名资深数据分析师，负责把用户的自然语言问题转换成 {dialect} SQL。",
}
#   {dialect} 是占位符，运行时会被替换成 config.SQL_DIALECT


# 表的中文业务注释（★ 从 knowledge.py 搬过来的）
DEFAULT_TABLE_COMMENTS = {
    "dwd_order_all":    "订单事实表：记录订单的下单、接单、支付、完成、取消全生命周期与金额",
    "dim_user":         "用户维表：记录用户的注册信息、注册城市和会员等级",
    "dim_driver":       "司机维表：记录司机的所属城市、注册时间和司机等级",
    "dwd_refund":       "退款事实表：记录每笔退款的关联订单、退款金额和退款时间",
    "dim_user_contact": "用户敏感信息表：记录手机号、身份证号（★ 禁止 AI 访问）",
}


# ═══════════════════════════════════════════════════════════
# ★ 表白名单（访问控制）—— 企业级必备
# ═══════════════════════════════════════════════════════════
# 作用：控制"哪些表允许 AI 看见和查询"
#
# 两种模式（由 config.ACL_DEFAULT_VISIBLE 决定）：
#   黑名单模式（默认）：表不在表里 → 默认可见；显式标 0 才不可见   ← 好用
#   白名单模式        ：表不在表里 → 默认不可见；必须显式标 1     ← 更安全
#
# 企业里建议：
#   第一次上线用黑名单模式（快速跑通，只屏蔽敏感表）
#   成熟后切白名单模式（默认拒绝，逐个授权）
#
# ★ 注意：这里只是"应用层的可见性控制"，它拦的是"AI 看见什么"。
#   真正的硬约束在数据库账号层（只读账号 + 表级授权），
#   以及 guard.py 里的 SQL 表名校验（拦"模型硬写出来"的情况）。
DEFAULT_TABLE_ACL = {
    "dim_user_contact": {"visible": 0, "note": "含手机号与身份证号，个人敏感信息，禁止 AI 访问"},
}


# 指标知识库（★ 从 knowledge.py 搬过来的）
# definition 和 doc 的区别：
#   definition → 写成"口径说明书"，精确可执行，给大模型看
#   doc       → 写成"自然语言文档"，含同义说法，给 embedding 模型看
DEFAULT_METRICS = [
    {
        "name": "订单量",
        "keywords": ["订单量", "订单数", "单量"],
        "definition": "订单量 = COUNT(*)",
        "doc": "订单量：订单的总数量，也叫订单数、单量、下单笔数",
        "tables": ["dwd_tu_order_all"],
    },
    {
        "name": "完单量",
        "keywords": ["完单量", "完单", "完成订单"],
        "definition": "完单量 = SUM(CASE WHEN status = 6 THEN 1 ELSE 0 END)",
        "doc": "完单量：已完成状态（status=6）的订单数量，也叫完成订单数",
        "tables": ["dwd_tu_order_all"],
    },
    {
        "name": "取消率",
        "keywords": ["取消率", "取消比例"],
        "definition": ("取消率 = COALESCE(SUM(CASE WHEN status = 7 THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 0)\n"
                       "  ★ COALESCE 必须套在【整个除法表达式】的最外层，只套分子是无效的\n"
                       "  ★ 乘法里的 * 1.0 是为了避免整数相除被截断成 0\n"
                       "  分母为该时间范围内的全部订单，禁止把 status=7 写进 WHERE"),
        "doc": "取消率：已取消订单数占总订单数的比例，也叫取消比例、订单取消占比",
        "tables": ["dwd_tu_order_all"],
    },
    {
        "name": "完单率",
        "keywords": ["完单率", "完成率"],
        "definition": ("完单率 = COALESCE(SUM(CASE WHEN status = 6 THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 0)\n"
                       "  ★ COALESCE 必须套在【整个除法表达式】的最外层\n"
                       "  分母为该时间范围内的全部订单"),
        "doc": "完单率：已完成订单数占总订单数的比例，也叫完成率、订单完成比例",
        "tables": ["dwd_tu_order_all"],
    },
    {
        "name": "客单价",
        "keywords": ["客单价", "单均价", "客单"],
        "definition": ("客单价 = COALESCE(SUM(CASE WHEN status = 6 THEN total ELSE 0 END) * 1.0 "
                       "/ SUM(CASE WHEN status = 6 THEN 1 ELSE 0 END), 0)\n"
                       "  ★ 金额列是 total（订单成交金额），该表没有 amount 列\n"
                       "  ★ 分子【和】分母都只统计已完成订单（status=6），取消订单不计入\n"
                       "    错误写法：SUM(total) / ...  ← 这样会把取消单的金额也算进去\n"
                       "  ★ * 1.0 是为了避免整数相除被截断"),
        "doc": "客单价：平均每个已完成订单的金额，也叫单均价、每单平均金额、平均每单多少钱、客单",
        "tables": ["dwd_tu_order_all"],
    },
    {
        "name": "GMV",
        "keywords": ["gmv", "成交金额", "成交额"],
        "definition": ("GMV = COALESCE(SUM(CASE WHEN status = 6 THEN total ELSE 0 END), 0)\n"
                       "  ★ 金额列是 total（订单成交金额），该表没有 amount 列\n"
                       "  只统计已完成订单的金额"),
        "doc": "GMV：成交总额、成交额、交易额，已完成订单的金额总和",
        "tables": ["dwd_tu_order_all"],
    },
]


# ═══════════════════════════════════════════════════════════
# ★ 默认指标的关键口径硬约束（required_predicates）
#   语义：命中该指标时，SQL 必须出现这些过滤片段，否则校验器报 error（可阻断执行）。
#   目的：把"权威定义里已确认的必要条件"从自然语言升级为【机器可校验】，
#         根治 status=6 / status=40 这类口径混用（见 sql_validator._check_p1）。
#   ★ 写入位置：① 首次建库时随 DEFAULT_METRICS 落库；② 老库用
#     `_seed_metric_constraints.py` 幂等补种（只补当前为空的，不覆盖已有值）。
#   注：多条为「且」关系；命中多个同族指标时按「任一满足」判定，避免互相冲突。
# ═══════════════════════════════════════════════════════════
DEFAULT_METRIC_CONSTRAINTS = {
    # 通用（dwd_tu_order_all，状态字段名是 status）
    "GMV": ["status = 6"],
    "完单量": ["status = 6"],
    "完单率": ["status = 6"],
    "完单金额": ["status = 6"],
    "客单价": ["status = 6"],
    "取消率": ["status = 7"],
    # 代驾（ods_valet_order_all，状态字段名是 status，完单=40）
    "代驾完单量": ["status = 40"],
    "代驾完单金额": ["status = 40"],
    # 大板（ods_tu_large_handcart_order_all，状态字段名是 state，完单=6）
    "大板完单量": ["state = 6"],
    "大板完单金额": ["state = 6"],
    # 接单
    "接单量": ["is_picked = 1"],
    "接单率": ["is_picked = 1"],
    # 订单性质（定义里明确"必须同时满足"两个判定）
    "线上订单": ["is_salesman = 0", "is_service_staff IN (0,4,5)"],
    "调度单": ["is_salesman = 1", "is_service_staff IN (1,2,3)"],
}


# ═══════════════════════════════════════════════════════════
# 内部工具：连库 + 首次自动初始化
# ═══════════════════════════════════════════════════════════
def _connect():
    """
    连上配置库

    ★ timeout=10 是给【多人同时用】准备的：
      SQLite 同一时刻只允许一个写事务。两个管理员同时点"保存"时，
      后到的那个默认会立刻抛 "database is locked"（默认 timeout 只有 5 秒，
      且这里原来是 0 → 立即失败）。给 10 秒窗口让它排队等锁，用户几乎无感。
      （读操作互不阻塞，不受影响。）

    ★ 自动建父目录：生产上 CONFIG_DB 会指向挂载卷（如 /app/data/agent_config.db）。
      SQLite 自己【不会】创建父目录，目录不存在时报的是
      "unable to open database file" —— 一句话看不出是路径问题，很难排查。
      这里直接补上，配置写错路径也能自愈。
    """
    parent = os.path.dirname(os.path.abspath(CONFIG_DB))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    return sqlite3.connect(CONFIG_DB, timeout=10)


# 用一个字典记录"是否已经初始化过"，避免每次读写都重复建表
_state = {"ready": False}


def _ensure_ready():
    """
    确保配置表已建好、默认值已写入
    ★ 只在第一次调用时真正执行，之后直接跳过
    ★ 这样调用方就不用关心"要先初始化"这件事了
    """
    if not _state["ready"]:        # 字典取值：第一次是 False
        init_store()               # 建表 + 写默认值
        ensure_default_admin()     # ★ 没有任何账号时，内置一个管理员（否则谁也进不来）
        # ★ 老库迁移：仍在用公开默认口令 admin123 的管理员，标记为必须改密。
        #   可用 ADMIN_FORCE_CHANGE=0 关闭（不推荐）。
        flag_default_password_admins()
        _state["ready"] = True     # 标记已初始化（修改字典不算重新赋值变量）


def _to_json(lst):
    """把列表转成 JSON 字符串（存库用）"""
    return json.dumps(lst, ensure_ascii=False)
    #      ensure_ascii=False 很重要！否则中文会变成 \u4e2d\u6587 这种转义

def _from_json(text):
    """把 JSON 字符串转回列表（从库读出来用）"""
    return json.loads(text) if text else []


def _row_dict(cur, table, pk_col, pk_val):
    """读某张表某一行成 dict（供知识变更历史的「变更前/后快照」）。行不存在返回 None。

    ★ table / pk_col 只由内部硬编码常量传入（非用户输入），无注入风险。
    """
    try:
        cur.execute(f"SELECT * FROM {table} WHERE {pk_col} = ?", (pk_val,))
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        if not row:
            return None
        return dict(zip(cols, row))
    except Exception:
        return None


def _record_history(cur, entity_type, entity_id, action, before=None, after=None, operator=None):
    """向 knowledge_history 写入一条变更记录（before/after 为 dict 或 None，自动 JSON 化）。

    ★ 历史记录失败绝不阻断主流程（知识写成功 > 历史写失败），但必须返回是否成功，
      调用方据此打日志，避免「历史静默丢失」。
    """
    try:
        cur.execute("""
            INSERT INTO knowledge_history
                (entity_type, entity_id, action, before_json, after_json, operator, created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (entity_type, str(entity_id), action,
              json.dumps(before, ensure_ascii=False) if before is not None else None,
              json.dumps(after, ensure_ascii=False) if after is not None else None,
              operator, _now()))
        return True
    except Exception as e:
        print(f"⚠️ 知识变更历史写入失败（{entity_type}/{entity_id}）：{e}")
        return False


# ═══════════════════════════════════════════════════════════
# 初始化：建表 + 写入默认配置（幂等，可反复调用）
# ═══════════════════════════════════════════════════════════
def init_store():
    """
    建三张表，并写入默认值
    ★ INSERT OR IGNORE = 已存在就跳过，不覆盖你改过的内容
    """
    conn = _connect()
    cur = conn.cursor()

    # ① Prompt 配置表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prompts (
            key     TEXT PRIMARY KEY,
            content TEXT
        )
    """)
    for key, content in DEFAULT_PROMPTS.items():
        cur.execute("INSERT OR IGNORE INTO prompts (key, content) VALUES (?, ?)",
                    (key, content))

    # ② 指标知识表
    #   ★ enabled 字段的作用：真实公司接入时，往往只开放一部分指标给 AI。
    #     停用的指标进不了知识库 → AI 不知道它的口径 → 也就答不出来。
    #     这比"让 AI 知道口径但要求它别用"可靠得多。
    #   ⚠️ 注意：三引号里面是【SQL 语句】，只能用 -- 注释，不能写 Python 的 #
    cur.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            name       TEXT PRIMARY KEY,   -- 指标名
            keywords   TEXT,               -- JSON 数组字符串
            definition TEXT,               -- 业务口径
            doc        TEXT,               -- 富文本描述（embedding 用）
            tables     TEXT,               -- JSON 数组字符串
            enabled    INTEGER DEFAULT 1   -- 1 = 开放给 AI；0 = 停用
        )
    """)

    # ── 迁移：老库的 metrics 表没有 enabled 列，补上 ──
    #   ★ 为什么需要迁移？
    #     你的数据库是之前建的，CREATE TABLE IF NOT EXISTS 对已存在的表不会生效，
    #     所以新加的列不会自动出现。必须显式 ALTER TABLE。
    #     （这叫"数据库迁移"，是长期维护的系统一定会遇到的事）
    existing_cols = [r[1] for r in cur.execute("PRAGMA table_info(metrics)").fetchall()]
    #                 r[1] 是列名（r[0] 是序号，r[2] 是类型）
    if "enabled" not in existing_cols:
        cur.execute("ALTER TABLE metrics ADD COLUMN enabled INTEGER DEFAULT 1")
        #   老数据全部默认为 1（开放）—— 保持原有行为，不会因为升级而少答问题

    # ── 迁移：Semantic Layer 升级新增的列（P1 指标优先级 / 类型 / 模板 / 依赖 / 冲突键）──
    _metric_new_cols = {
        "priority": "INTEGER DEFAULT 1",          # P1 层（现固定 1，预留扩展）
        "metric_type": "TEXT",                     # ratio / amount / count / ...
        "sql_template": "TEXT",                     # 可执行的权威口径片段（可选）
        "depends_metrics": "TEXT",                  # JSON：衍生指标依赖
        "conflict_key": "TEXT",                     # 同名歧义归并键
        # ★ 机器可校验的硬约束（缺失 → error 级，阻断执行；见 sql_validator._check_p1）：
        #   把"权威口径里必须有"的条件/字段显式声明出来，校验就从事后提示升级为执行闸门。
        "required_predicates": "TEXT",              # JSON 数组：必须出现的过滤片段，如 ["status = 40"]
        "required_columns": "TEXT",                 # JSON 数组：必须出现的字段，如 ["service_fee"]
        # ★ 负向约束（出现 → error）：声明该指标【禁止出现】的过滤，
        #   如"发单量"禁止 status 过滤（发单≠完单，防模型臆想加 status=6）。
        "forbidden_predicates": "TEXT",             # JSON 数组：禁止出现的字段/片段，如 ["status"]
    }
    for cname, cdef in _metric_new_cols.items():
        if cname not in existing_cols:
            cur.execute(f"ALTER TABLE metrics ADD COLUMN {cname} {cdef}")

    for m in DEFAULT_METRICS:
        cur.execute(
            "INSERT OR IGNORE INTO metrics "
            "(name, keywords, definition, doc, tables, required_predicates) "
            "VALUES (?,?,?,?,?,?)",
            (m["name"], _to_json(m["keywords"]), m["definition"],
             m["doc"], _to_json(m["tables"]),
             # ★ 随默认指标一起写入已确认的口径硬约束（老库用 _seed_metric_constraints.py 补种）
             _to_json(m.get("required_predicates")
                      or DEFAULT_METRIC_CONSTRAINTS.get(m["name"], []))),
        )
        #   ★ 注意：这里不写 enabled，让它用默认值 1。
        #     这样将来加新指标时不用改 INSERT 语句，也不会覆盖你手工改过的开关状态

    # ③ 表注释表
    #   ★ source 列区分"谁写的"：
    #     - manual = 用户手写 / 在 AI 基础上改过的（最终版）
    #     - ai     = AI 自动生成的【临时】注释（白名单时生成，直接进 RAG，用户可改）
    #   为什么要区分？因为"AI 临时"和"用户确认过"不同：
    #     表重新加白名单 / 字段变了，可以覆盖 source=ai 的（用户没动过），
    #     但绝不能覆盖 source=manual 的（那是用户精修过的）。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS table_comments (
            table_name TEXT PRIMARY KEY,
            comment    TEXT,
            source     TEXT DEFAULT 'manual'
        )
    """)

    # ── 迁移：老库的 table_comments 没有 source 列 ──
    cur.execute("PRAGMA table_info(table_comments)")
    if "source" not in [r[1] for r in cur.fetchall()]:
        cur.execute("ALTER TABLE table_comments ADD COLUMN source TEXT DEFAULT 'manual'")

    # ── 迁移：上一版把 AI 注释存在 table_suggestions（参考层，不注入），
    #    现在语义改成"AI 直接进 RAG"——把还没被人工注释占位的草稿转正过来 ──
    try:
        cur.execute("SELECT table_name, suggested FROM table_suggestions")
        for name, txt in cur.fetchall():
            if txt:
                #   OR IGNORE：该表若已有注释（人工写的）就保留，别用草稿覆盖
                cur.execute("""INSERT OR IGNORE INTO table_comments
                               (table_name, comment, source) VALUES (?,?, 'ai')""",
                            (name, txt))
    except Exception:
        pass    # 没有 table_suggestions 这张表（全新环境）→ 忽略

    for t, c in DEFAULT_TABLE_COMMENTS.items():
        cur.execute("INSERT OR IGNORE INTO table_comments (table_name, comment) VALUES (?,?)",
                    (t, c))

    # ③-b ★ 字段注释表 —— RAG 准确率最直接的杠杆
    #   【为什么字段注释比表注释更重要？】
    #     模型写错 SQL 最多的情形，不是"找错表"，而是"用错字段的含义"。
    #     真实踩过的坑：不知道 status 各枚举值 → 把"完单量"写成 status=7（已取消）。
    #     在 status 字段上写一句"6已完成 7已取消"，准确率立刻上去。
    #
    #   【为什么必须支持人工维护？】
    #     数仓里的字段注释常常是空的（本库实测 0/18025），
    #     而且有些口径（枚举含义、单位、是否含税）数据库里根本不会写。
    #     → 所以人工注释必须能覆盖数据库注释，成为最高优先级的知识来源。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS column_comments (
            table_name  TEXT,       -- 表名
            column_name TEXT,       -- 字段名
            comment     TEXT,       -- 业务注释（人工写的 or AI 生成的，都在这）
            source      TEXT,       -- ★ 谁写的：manual / ai（见下面的说明）
            PRIMARY KEY (table_name, column_name)
        )
    """)
    #   ★ 联合主键 (表名, 字段名)：不同表可以有同名字段（dwd_a.city 和 dim_b.city），
    #     用两列做联合主键才能各存各的注释，不会互相覆盖。
    #
    #   ★ source 列（2026-09-18 加，和 table_comments 同一套语义）：
    #     字段注释以前只有「人工」一种来源，导致 417 个字段只有 4 条注释 ——
    #     不是人不想写，是写不完。现在 AI 可以拿着【真实取值】批量生成。
    #     但要区分：
    #       source='ai'     → AI 生成的临时版，可被下一次生成覆盖
    #       source='manual' → 人写的 / 在 AI 版上改过的定稿，永远不被覆盖
    #     没有这一列，AI 每次重跑都会把人改好的注释冲掉。
    cur.execute("PRAGMA table_info(column_comments)")
    if "source" not in [r[1] for r in cur.fetchall()]:
        #   老库：已有的 column_comments 全是人工一条条写的 → 一律认作 manual
        cur.execute("ALTER TABLE column_comments ADD COLUMN source TEXT DEFAULT 'manual'")

    # ③-c ★ 数据字典表 —— "编码 → 中文含义"的映射
    #   【它解决什么问题？】
    #     app_entity_source = 101 是什么意思？模型不知道，就会瞎猜成 1 或者编一个。
    #     有了字典，模型才知道 101=车拖车、201=慧拉车。
    #
    #   【为什么和字段注释分开存？】
    #     字段注释是"这个字段是什么"（一句话），
    #     字典是"这个字段的每个取值是什么"（一个列表，可能几十条）。
    #     两者体积和用途都不同，混在一起会让字段注释变得又长又难维护。
    #
    #   【field_hint 是关键】
    #     公司字典表里只有 dict_type（app_entity_source），
    #     但模型需要知道"这个字典用在哪个字段上"。dict_type 和字段名经常对不上
    #     （比如 dict_type='coupon_receive_channel' 对应的字段叫 coupon_id），
    #     所以必须人工指定 field_hint，不能靠猜。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dict_entries (
            name       TEXT PRIMARY KEY,   -- 字典名称（如"数据渠道"），也是唯一标识
            dict_type  TEXT,               -- 来源字典类型（如 app_entity_source），便于溯源/重新导入
            field_hint TEXT,               -- ★ 适用字段（如 app_entity_source），注入时告诉模型用在哪
            keywords   TEXT,               -- JSON 数组：检索关键词
            items      TEXT,               -- JSON 数组：[{"value":"101","label":"车拖车"}, ...]
            enabled    INTEGER DEFAULT 1   -- 1 = 开放给 AI；0 = 停用
        )
    """)

    # ④ ★ 表白名单表（访问控制）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS table_acl (
            table_name TEXT PRIMARY KEY,
            visible    INTEGER,      -- 1 = 允许 AI 访问；0 = 禁止
            note       TEXT          -- 备注：为什么禁/允许
        )
    """)
    for t, info in DEFAULT_TABLE_ACL.items():
        cur.execute("INSERT OR IGNORE INTO table_acl (table_name, visible, note) VALUES (?,?,?)",
                    (t, info["visible"], info["note"]))

    # ⑤ ★ 问题反馈表（错例复盘）
    #   【它解决什么问题？】
    #     现在"发现幻觉"全靠人主动来反馈 —— 但反馈完就散了，
    #     没沉淀成"下次不会再错"的知识。
    #     这张表把每次踩坑连【当时的完整上下文】一起存下来
    #     （问了什么、生成了什么 SQL、召回了哪些表/指标/字典），
    #     复盘时不用回忆，也能直接看出"该补哪条知识"。
    #   【为什么错误类型要用固定枚举？】
    #     自由文本没法统计。固定几个类型，才能回答"我们最常错在哪一类"，
    #     从而知道该优先补什么。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedbacks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            question    TEXT,                  -- 用户问的原话
            sql         TEXT,                  -- 当时生成的 SQL
            result      TEXT,                  -- 结果摘要（只存前几行，结果可能很长）
            exec_ok     INTEGER,               -- 当时 SQL 是否执行成功（1/0）
            reason      TEXT,                  -- 错误类型（固定枚举，见 FEEDBACK_REASONS）
            detail      TEXT,                  -- 用户补充说明
            tables      TEXT,                  -- JSON：那时命中的表
            metrics     TEXT,                  -- JSON：那时命中的指标
            dicts       TEXT,                  -- JSON：那时命中的字典
            status      TEXT DEFAULT 'pending',-- pending 待处理 / resolved 已处理 / ignored 不处理
            note        TEXT,                  -- 处理备注（通常写"改了哪条知识"）
            created_at  TEXT,
            updated_at  TEXT
        )
    """)

    # ⑥ ★ Semantic Layer · 业务实体 Registry（P2 层）
    #   ★ 一个"业务实体"对应一个权威主表（如"代驾订单"→ ods_valet_order_all），
    #     记录粒度（一行=一个订单）、中文别名，供 Query Understanding / Router 识别。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            name      TEXT PRIMARY KEY,       -- 业务实体名（如"代驾订单"）
            table_name TEXT,                  -- 落地主表
            grain     TEXT,                    -- 粒度：一行=一个什么
            comment   TEXT,
            keywords  TEXT                     -- JSON：中文别名/同义词
        )
    """)
    # ── 迁移：entities 增加 enabled 开关（实体可下线，停用后不进向量索引）──
    try:
        cur.execute("PRAGMA table_info(entities)")
        if "enabled" not in [r[1] for r in cur.fetchall()]:
            cur.execute("ALTER TABLE entities ADD COLUMN enabled INTEGER DEFAULT 1")
    except Exception:
        pass
    # ── 迁移：entities 增加 source（manual / ai）──
    #   ★ 为什么要标来源：授权新表时 AI 会自动起草一个实体，
    #     如果人分不清"哪条是 AI 猜的、哪条是我定的"，就会把 AI 草稿当权威口径用。
    #     规则和表注释/字段注释一致：source='manual' 的永远不被 AI 覆盖。
    #     老数据一律算 manual（那是人手工登记的）。
    try:
        cur.execute("PRAGMA table_info(entities)")
        if "source" not in [r[1] for r in cur.fetchall()]:
            cur.execute("ALTER TABLE entities ADD COLUMN source TEXT DEFAULT 'manual'")
    except Exception:
        pass

    # ⑥-b ★ Semantic Layer · 关系 / Join 规则 Registry（P2 层）
    #   ★ 记录表/实体之间怎么关联、是否一对多（1:N 必须预聚合）、业务说明。
    #     SQL Validator 的 Join 校验直接读这里；这是"防笛卡尔放大"的权威来源。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS relationships (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            from_entity    TEXT,               -- 左实体/表（别名）
            to_entity      TEXT,               -- 右实体/表（别名）
            join_sql       TEXT,               -- 标准 JOIN 片段（如 o JOIN f ON o.order_no=f.order_no）
            join_type      TEXT DEFAULT 'LEFT',
            cardinality    TEXT DEFAULT '1:1', -- 1:1 / 1:N（1:N → pre_agg_required=1）
            pre_agg_required INTEGER DEFAULT 0, -- 1 = 右表须先按关联键 GROUP BY 预聚合
            rule_note      TEXT,               -- 业务说明
            enabled        INTEGER DEFAULT 1
        )
    """)

    # ⑥-c ★ Semantic Layer · 普通业务文档 Registry（P4 层）
    #   ★ 分析报告 / 口径说明 / 名词表，向量检索补充上下文，绝不覆盖 P0~P3。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kb_docs (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            title    TEXT,
            content  TEXT,
            keywords TEXT,                     -- JSON
            priority INTEGER DEFAULT 4,        -- P4
            enabled  INTEGER DEFAULT 1
        )
    """)

    # ⑦ ★ 账号表（两类角色：admin 管理员 / user 普通用户）
    #   【它解决什么问题？】
    #     这个工具面向两种人：业务同学只想"问数"，
    #     而知识库 / Prompt / 表权限是"口径资产"，不能谁都能改 ——
    #     改错一条口径，全公司拿到的数字都会错，而且没人知道是谁什么时候改的。
    #     所以必须有账号：普通用户只取数 + 反馈，管理员才能动知识。
    #   【口令怎么存？】
    #     绝不存明文。存"盐 + PBKDF2 派生值"：即使整库被拖走，
    #     也无法反推出原始口令（每个用户独立盐 → 相同口令的派生值也不同）。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username      TEXT PRIMARY KEY,   -- 登录名
            display_name  TEXT,               -- 显示名（界面右上角展示）
            password_hash TEXT,               -- PBKDF2 派生值（十六进制）
            salt          TEXT,               -- 每个用户独立的随机盐
            role          TEXT DEFAULT 'user',-- admin / user
            enabled       INTEGER DEFAULT 1,  -- 1 = 启用；0 = 停用（停用后不能登录）
            must_change_password INTEGER DEFAULT 0,  -- 1 = 下次登录必须改密
            created_at    TEXT,
            updated_at    TEXT
        )
    """)

    # ── 迁移：老库 users 没有 must_change_password，补上 ──
    #   ★ 不重建表：账号与会话是线上数据，重建会丢。
    user_cols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "must_change_password" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER DEFAULT 0")
    # ── 迁移：users 增加 user_attributes（行级权限的已验证身份属性，如 dept_id / region）──
    if "user_attributes" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN user_attributes TEXT")

    # ⑧ ★ 会话表（登录后发一个随机 token，前端每次请求带上）
    #   【为什么用"服务端会话"而不是把身份写进前端？】
    #     前端可改。若把 role 放在前端，普通用户改一行 JS 就能变成管理员。
    #     身份必须以服务端为准：前端只拿"随机 token"，
    #     真正是谁、什么角色，每次请求都回到这张表来查。
    #   【为什么能停用立即生效？】
    #     删除用户 / 停用 / 改角色后，这里直接清掉它的会话 —— 无需等 token 过期。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,      -- 随机 token（前端持有）
            username   TEXT,                  -- 属于谁
            created_at TEXT,
            expires_at TEXT                   -- 过期时间（字符串可比较）
        )
    """)

    # ⑨ ★ 取数记录表（个人取数历史 + 审核状态）
    #   【它解决什么问题？】
    #     1) 用户问过的数，关掉页面就找不回来了 —— 想复用上次那条 SQL 只能重问一遍，
    #        而同一句话重问，AI 未必给出完全一样的 SQL。
    #     2) 更重要的：AI 算出来的数【对不对】，现在只有用户自己能判断。
    #        业务同学看到一个数字，没有能力确认口径是否正确 ——
    #        他需要一个"请管理员帮我看看"的通道，管理员看完给个明确结论。
    #   【为什么把历史和审核放同一张表？】
    #     审核不是独立业务，它是"某一次取数"的一个属性。
    #     拆两张表就会出现"历史记录找不到对应审核"的孤儿数据。
    #   【审核状态机】
    #     none（未提交）→ pending（待审核）→ approved（通过）/ rejected（不通过）
    #     被驳回后可以改完再提交，状态回到 pending。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS query_records (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            username       TEXT,                  -- 谁问的
            question       TEXT,                  -- 原问题
            sql            TEXT,                  -- 生成的 SQL
            ok             INTEGER,               -- 是否执行成功 1/0
            tables         TEXT,                  -- JSON：命中的表
            metrics        TEXT,                  -- JSON：命中的指标
            dicts          TEXT,                  -- JSON：命中的字典
            columns        TEXT,                  -- JSON：结果列名
            row_count      INTEGER,
            result_preview TEXT,                  -- 结果前几行（审核/回看用）
            answer         TEXT,                  -- 自然语言结论（审核时看它有没有胡说）
            info           TEXT,                  -- 检索方式（"向量召回…｜字典…｜时间口径…"）
            attempts       INTEGER DEFAULT 0,     -- 尝试次数（>1 说明经过了自愈重试）
            pipeline       TEXT,                  -- v2 企业级 / legacy 旧兜底
            understanding  TEXT,                  -- JSON：查询理解（意图/指标/实体/维度/时间/歧义）
            review_status  TEXT DEFAULT 'none',   -- none/pending/approved/rejected
            review_note    TEXT,                  -- 审核意见
            reviewer       TEXT,                  -- 审核人
            submitted_at   TEXT,                  -- 提交审核时间
            reviewed_at    TEXT,                  -- 审核完成时间
            remind_count   INTEGER DEFAULT 0,     -- 用户催过几次
            reminded_at    TEXT,                  -- 最近一次提醒时间
            created_at     TEXT
        )
    """)

    # ── 迁移：老库的 query_records 没有"处理过程/查询理解"这几列，补上 ──
    #   ★ 为什么不重建表？已有记录是用户的真实取数历史，重建就丢了。
    #     CREATE TABLE IF NOT EXISTS 对已存在的表不生效，新列只能 ALTER 加。
    qr_cols = [r[1] for r in cur.execute("PRAGMA table_info(query_records)").fetchall()]
    for cname, cdef in {
        "info": "TEXT",                 # 检索方式/时间口径等摘要
        "attempts": "INTEGER DEFAULT 0",
        "pipeline": "TEXT",
        "understanding": "TEXT",        # 查询理解 JSON
    }.items():
        if cname not in qr_cols:
            cur.execute(f"ALTER TABLE query_records ADD COLUMN {cname} {cdef}")

    # ⑩ ★ 查询结果快照（导出用）
    #   为什么单独存一份，而不是导出时重新执行 SQL？
    #     ① 重新执行会引入一条新的执行路径，必须重新过只读/白名单/校验，
    #        容易绕过统一闸门；快照只读、没有这个风险。
    #     ② 用户导出的是"当时看到的数"，重跑可能已经变了，反而不一致。
    #   只存成功取数的结果；行数受 MAX_RESULT_ROWS 限制（默认 1000），不会撑爆配置库。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS query_result_snapshots (
            record_id    INTEGER PRIMARY KEY,
            columns_json TEXT,
            rows_json    TEXT,
            row_count    INTEGER DEFAULT 0,
            created_at   TEXT
        )
    """)

    # ⑪ ★ 收藏（个人维度；object_type 预留 dataset/dashboard，后续不用改表）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL,
            object_type TEXT NOT NULL,     -- record / dataset / dashboard
            object_id   TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            UNIQUE(username, object_type, object_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user "
                "ON favorites(username, object_type)")

    # ⑫ ★ 数据权限体系（角色 / 数据域 / 敏感资源 / 字段策略 / 版本 / 审批 / 审计 / AI 草稿 / 行级）
    _migrate_permission_tables(cur)

    # ⑬ ★ 知识治理升级（变更历史 + 知识元数据列 + 索引重建状态）—— 幂等，只增不删
    _migrate_knowledge_governance(cur)

    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════
# ★ 知识治理迁移：变更历史 + 元数据列 + 索引状态（幂等，只增不删）
# ═══════════════════════════════════════════════════════════
def _migrate_knowledge_governance(cur):
    """
    增量落地「知识可追溯」：① 变更历史表；② 知识表元数据列；③ 索引重建状态表。
    ★ 原则：只 CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN（幂等），
      不删列、不重建表；历史知识缺的元数据一律留 NULL（诚实标记「未知」，
      不凭空补造来源/审核人/权威等级）。
    """
    # ① 知识变更历史（版本快照 + 审计）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,     -- metric / entity / relationship / kb_doc / dict / table_comment / column_comment
            entity_id   TEXT NOT NULL,     -- 主键（metric 用 name；relationship/kb_doc 用 id）
            action      TEXT NOT NULL,     -- create / update / delete / enable / disable
            before_json TEXT,              -- 变更前快照（JSON）
            after_json  TEXT,              -- 变更后快照（JSON）
            operator    TEXT,              -- 操作人（账号体系可用时）
            created_at  TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_kh_entity "
                "ON knowledge_history(entity_type, entity_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_kh_ts ON knowledge_history(created_at)")

    # ② 知识元数据列（created_at/updated_at/updated_by/version/source_ref）
    _meta_cols = {
        "created_at": "TEXT",
        "updated_at": "TEXT",
        "updated_by": "TEXT",
        "version": "INTEGER DEFAULT 1",
        "source_ref": "TEXT",
    }
    for table in ("metrics", "entities", "relationships", "kb_docs",
                  "dict_entries", "table_comments", "column_comments"):
        try:
            existing = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
        except Exception:
            continue
        for cname, cdef in _meta_cols.items():
            if cname not in existing:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {cname} {cdef}")

    # ②-b 反馈闭环（GAP-6）：feedbacks 增加「关联知识 / 回归样例 / 根因」三列，
    #   让用户反馈能显式关联到被修正的知识或生成的回归样例，形成治理闭环。
    try:
        fb_cols = [r[1] for r in cur.execute("PRAGMA table_info(feedbacks)").fetchall()]
    except Exception:
        fb_cols = []
    for cname, cdef in {
        "related_knowledge_id": "TEXT",    # 关联的知识对象（如 "metric:代驾完单量"）
        "regression_sample_id": "TEXT",    # 转化出的回归样例 ID（Golden/regression 集）
        "root_cause": "TEXT",              # 根因定位（找错表/口径错误/代码缺陷/知识缺失…）
    }.items():
        if cname not in fb_cols:
            cur.execute(f"ALTER TABLE feedbacks ADD COLUMN {cname} {cdef}")

    # ③ 索引重建状态表（GAP-3：重建成功/失败与知识计数落库，供「过期索引」检测）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS index_state (
            key         TEXT PRIMARY KEY,   -- 'rag_index'
            status      TEXT,               -- ok / failed
            counts_json TEXT,               -- {tables:n, metrics:n, dicts:n, entities:n, relationships:n, docs:n}
            error       TEXT,
            built_at    TEXT
        )
    """)


# ═══════════════════════════════════════════════════════════
# ★ 数据权限体系迁移（幂等：CREATE IF NOT EXISTS + 逐表校验 + 内置角色种子）
# ═══════════════════════════════════════════════════════════
def _migrate_permission_tables(cur):
    """
    增量建数据权限相关表，并回填内置角色 + 老用户角色绑定。
    ★ 只增表、不删不重建现有表；每张表独立 try/except，失败不拖垮整体。
    """
    ddl = [
        # ① 角色
        """CREATE TABLE IF NOT EXISTS roles (
            name         TEXT PRIMARY KEY,
            display_name TEXT,
            description  TEXT,
            builtin      INTEGER DEFAULT 0,
            created_at   TEXT,
            updated_at   TEXT
        )""",
        # ② 用户-角色绑定
        """CREATE TABLE IF NOT EXISTS user_role_bindings (
            username   TEXT NOT NULL,
            role_name  TEXT NOT NULL,
            created_by TEXT,
            created_at TEXT,
            PRIMARY KEY (username, role_name)
        )""",
        """CREATE INDEX IF NOT EXISTS idx_urb_role ON user_role_bindings(role_name)""",
        # ③ 数据域
        """CREATE TABLE IF NOT EXISTS data_domains (
            name         TEXT PRIMARY KEY,
            display_name TEXT,
            description  TEXT,
            owner        TEXT,
            enabled      INTEGER DEFAULT 1,
            created_at   TEXT,
            updated_at   TEXT
        )""",
        # ④ 敏感资源映射
        """CREATE TABLE IF NOT EXISTS sensitive_resources (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_name    TEXT NOT NULL,
            table_name     TEXT NOT NULL,
            raw_table      TEXT,
            column_name    TEXT NOT NULL,
            raw_column     TEXT,
            sensitivity    TEXT DEFAULT 'high',
            aliases        TEXT,
            mapping_status TEXT DEFAULT 'confirmed',
            note           TEXT,
            owner          TEXT,
            created_at     TEXT,
            updated_at     TEXT,
            UNIQUE(table_name, column_name)
        )""",
        """CREATE INDEX IF NOT EXISTS idx_sr_domain ON sensitive_resources(domain_name)""",
        """CREATE INDEX IF NOT EXISTS idx_sr_table ON sensitive_resources(table_name)""",
        # ⑤ 字段访问策略
        """CREATE TABLE IF NOT EXISTS permission_policies (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_type TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            domain_name  TEXT,
            table_name   TEXT NOT NULL,
            column_name  TEXT NOT NULL,
            action       TEXT DEFAULT 'query',
            access       TEXT NOT NULL,
            mask_rule    TEXT,
            priority     INTEGER DEFAULT 0,
            enabled      INTEGER DEFAULT 1,
            version      INTEGER DEFAULT 1,
            created_by   TEXT,
            created_at   TEXT,
            updated_at   TEXT
        )""",
        """CREATE INDEX IF NOT EXISTS idx_pp_subject
            ON permission_policies(subject_type, subject_name)""",
        """CREATE INDEX IF NOT EXISTS idx_pp_table ON permission_policies(table_name)""",
        # ⑥ 策略版本 / 发布记录
        """CREATE TABLE IF NOT EXISTS permission_policy_versions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_id    INTEGER,
            version      INTEGER,
            payload      TEXT,
            status       TEXT DEFAULT 'draft',
            change_note  TEXT,
            before_json  TEXT,
            after_json   TEXT,
            operator     TEXT,
            published_at TEXT
        )""",
        # ⑦ 审批 / 发布记录
        """CREATE TABLE IF NOT EXISTS permission_approvals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id     INTEGER,
            target_type  TEXT,
            requested_by TEXT,
            approved_by  TEXT,
            status       TEXT DEFAULT 'pending',
            reason       TEXT,
            created_at   TEXT,
            decided_at   TEXT
        )""",
        # ⑧ 权限判定 / 变更审计
        """CREATE TABLE IF NOT EXISTS permission_audit_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT,
            roles       TEXT,
            request_id  TEXT,
            ts          TEXT,
            question    TEXT,
            domain      TEXT,
            tables      TEXT,
            columns     TEXT,
            action      TEXT,
            decision    TEXT,
            deny_reason TEXT,
            rule_ids    TEXT,
            policy_version INTEGER,
            rewritten   INTEGER DEFAULT 0,
            final_sql   TEXT,
            returned_columns TEXT,
            exported_columns TEXT,
            ok          INTEGER,
            created_at  TEXT
        )""",
        """CREATE INDEX IF NOT EXISTS idx_pal_user ON permission_audit_logs(username)""",
        """CREATE INDEX IF NOT EXISTS idx_pal_ts ON permission_audit_logs(ts)""",
        # ⑨ AI 自然语言草稿
        """CREATE TABLE IF NOT EXISTS ai_policy_drafts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_text  TEXT,
            parsed_json  TEXT,
            ambiguities  TEXT,
            impact_json  TEXT,
            status       TEXT DEFAULT 'pending',
            created_by   TEXT,
            created_at   TEXT,
            reviewed_by  TEXT,
            reviewed_at  TEXT
        )""",
        # ⑩ 行级权限（P3 数据模型，P1 预留）
        """CREATE TABLE IF NOT EXISTS row_permission_rules (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_type TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            table_name   TEXT NOT NULL,
            filter_sql   TEXT NOT NULL,
            description  TEXT,
            enabled      INTEGER DEFAULT 1,
            created_by   TEXT,
            created_at   TEXT,
            updated_at   TEXT
        )""",
    ]
    for stmt in ddl:
        try:
            cur.execute(stmt)
        except Exception as e:
            # 单张表失败不影响其它表（记录到日志，迁移幂等，下次启动重试）
            print(f"⚠️ 权限表迁移失败（幂等，可重试）：{e}")

    # ── 内置角色种子：admin / user（builtin=1）──
    for name, zh in (("admin", "管理员"), ("user", "普通用户")):
        cur.execute(
            "INSERT OR IGNORE INTO roles (name, display_name, description, builtin, "
            "created_at, updated_at) VALUES (?,?,?,1,?,?)",
            (name, zh, f"内置角色：{zh}", _now(), _now()))

    # ── 老用户角色回填：users.role → user_role_bindings（保证升级后行为一致）──
    try:
        rows = cur.execute("SELECT username, role FROM users").fetchall()
        for username, role in rows:
            cur.execute(
                "INSERT OR IGNORE INTO user_role_bindings "
                "(username, role_name, created_by, created_at) VALUES (?,?,?,?)",
                (username, role, "system-migration", _now()))
    except Exception:
        pass


def migrate_permission_schema():
    """
    ★ 供迁移前备份后【单独调用】的入口：返回每张权限表的创建结果。
      失败的表记入返回，不抛全局异常；调用方据此判断是否需要人工介入。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    try:
        _migrate_permission_tables(cur)
        conn.commit()
        result = {"ok": True, "tables": _permission_tables(cur)}
    except Exception as e:
        conn.rollback()
        result = {"ok": False, "error": str(e)}
    conn.close()
    return result


def _permission_tables(cur):
    """列出所有权限相关表名（校验迁移结果用）"""
    names = ["roles", "user_role_bindings", "data_domains", "sensitive_resources",
             "permission_policies", "permission_policy_versions",
             "permission_approvals", "permission_audit_logs",
             "ai_policy_drafts", "row_permission_rules"]
    existing = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    return {n: (n in existing) for n in names}


# ═══════════════════════════════════════════════════════════
# 读配置（★ 每次都重新读库，所以改完立即生效）
# ═══════════════════════════════════════════════════════════
def get_prompt(key):
    """按名字取一条 Prompt 配置"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT content FROM prompts WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise KeyError(f"配置项不存在：{key}")
    return row[0]


def get_metrics(enabled_only=True):
    """
    取出指标知识库

    参数 enabled_only：
        True（默认）→ 只返回【已开放】的指标（给 RAG 用，这是正常业务路径）
        False       → 返回全部指标（给管理页面用，要能看到并切换停用的指标）

    ★ 为什么默认只返回开放的？
      因为调用方（rag.py）拿了就去建索引、注入 prompt。
      默认值选"安全的那一个"，就不会因为忘记传参数而漏掉权限控制。
      ——这就是"默认安全"的设计原则。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    sql = ("SELECT name, keywords, definition, doc, tables, enabled, "
           "metric_type, priority, sql_template, depends_metrics, conflict_key, "
           "required_predicates, required_columns, forbidden_predicates "
           "FROM metrics ")
    if enabled_only:
        sql += "WHERE enabled = 1 "                 # 只取开放的
    sql += "ORDER BY name"
    rows = cur.execute(sql).fetchall()
    conn.close()
    return [                                       # 把数据库行转回字典列表
        {
            "name": r[0],
            "keywords": _from_json(r[1]),          # JSON 字符串 → 列表
            "definition": r[2],
            "doc": r[3],
            "tables": _from_json(r[4]),
            "enabled": bool(r[5]),                 # ★ 转成 Python 的 True/False 更好用
            "metric_type": r[6] or None,           # 指标类型（count/amount/ratio/...）
            "priority": r[7] if r[7] is not None else 1,
            "sql_template": r[8] or "",
            "depends_metrics": _from_json(r[9]),
            "conflict_key": r[10] or "",
            "required_predicates": _from_json(r[11]),   # ★ 硬约束：必须出现的过滤片段
            "required_columns": _from_json(r[12]),      # ★ 硬约束：必须出现的字段
            "forbidden_predicates": _from_json(r[13]),  # ★ 负向约束：禁止出现的字段/片段
        }
        for r in rows
    ]


def get_disabled_metrics():
    """
    只取【已停用】的指标

    ★ 这个函数是干什么用的？
      "停用"意味着"不注入给模型"，但【不意味着"当用户问起时可以直接糊弄过去"】。
      因为停用的指标不在知识库里，AI 不知道口径，
      但如果它命中了某张表，还是可能用字段硬算出一个错数 —— 那是最危险的。
      所以 agent 会拿这份清单做"问题级拦截"：问题问到了停用指标 → 直接拒绝。
    """
    return [m for m in get_metrics(enabled_only=False) if not m["enabled"]]


def set_metric_enabled(name, enabled, operator=None):
    """开放 / 停用一个指标。返回是否真的改到了（指标不存在则返回 False）。

    ★ 知识治理：记录 enable/disable 变更历史，更新 updated_at / version。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    before = _row_dict(cur, "metrics", "name", name)
    cur.execute("UPDATE metrics SET enabled = ?, updated_at = ?, version = version + 1 "
                "WHERE name = ?",
                (1 if enabled else 0, _now(), name))
    affected = cur.rowcount                        # 受影响行数，0 表示没这条指标
    if affected:
        after = _row_dict(cur, "metrics", "name", name)
        _record_history(cur, "metric", name,
                        "enable" if enabled else "disable", before, after, operator)
    conn.commit()
    conn.close()
    return affected > 0


# ═══════════════════════════════════════════════════════════
# ★ 数据字典（编码 → 中文含义）的读写
# ═══════════════════════════════════════════════════════════
def get_dict_entries(enabled_only=True):
    """
    取出数据字典条目

    参数 enabled_only：
        True（默认）→ 只返回开放的（给 RAG 用，默认安全）
        False       → 全部（给管理页面用）

    返回：[{"name","dict_type","field_hint","keywords","items","enabled"}, ...]
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    sql = ("SELECT name, dict_type, field_hint, keywords, items, enabled "
           "FROM dict_entries ")
    if enabled_only:
        sql += "WHERE enabled = 1 "
    sql += "ORDER BY name"
    rows = cur.execute(sql).fetchall()
    conn.close()
    return [
        {
            "name": r[0],
            "dict_type": r[1] or "",
            "field_hint": r[2] or "",
            "keywords": _from_json(r[3]),
            "items": _from_json(r[4]),          # [{"value":..,"label":..}, ...]
            "enabled": bool(r[5]),
        }
        for r in rows
    ]


def get_disabled_dicts():
    """只取已停用的字典 —— 和 get_disabled_metrics 对称，便于统一处理"""
    return [d for d in get_dict_entries(enabled_only=False) if not d["enabled"]]


def upsert_dict_entry(entry, operator=None):
    """
    写入/更新一个字典条目（有则覆盖，无则新增），写入变更历史。

    ★ 和 upsert_metric 一样：调用方【没带 enabled】就保留库里的开关状态，
      避免"改个取值列表把停用过的字典无声地打开了"。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    name = entry["name"]
    before = _row_dict(cur, "dict_entries", "name", name)
    action = "update" if before else "create"
    _ts = _now()
    cols = ["name", "dict_type", "field_hint", "keywords", "items", "updated_at"]
    vals = [name, entry.get("dict_type", ""), entry.get("field_hint", ""),
            _to_json(entry.get("keywords", [])), _to_json(entry.get("items", [])), _ts]
    if "enabled" in entry:
        cols.append("enabled")
        vals.append(1 if entry["enabled"] else 0)
    if action == "create":
        cols.append("created_at")
        vals.append(_ts)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols
                       if c not in ("name", "created_at"))
    updates += ", version = dict_entries.version + 1"
    ph = ",".join("?" * len(cols))
    cur.execute(
        f"INSERT INTO dict_entries ({','.join(cols)}) VALUES ({ph}) "
        f"ON CONFLICT(name) DO UPDATE SET {updates}",
        vals,
    )
    after = _row_dict(cur, "dict_entries", "name", name)
    _record_history(cur, "dict", name, action, before, after, operator)
    conn.commit()
    conn.close()


def delete_dict_entry(name, operator=None):
    """删除一个字典条目（写入变更历史）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    before = _row_dict(cur, "dict_entries", "name", name)
    cur.execute("DELETE FROM dict_entries WHERE name = ?", (name,))
    affected = cur.rowcount
    _record_history(cur, "dict", name, "delete", before, None, operator)
    conn.commit()
    conn.close()
    return affected > 0


def set_dict_enabled(name, enabled, operator=None):
    """开放 / 停用一个字典条目（写入变更历史）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    before = _row_dict(cur, "dict_entries", "name", name)
    cur.execute("UPDATE dict_entries SET enabled = ?, updated_at = ?, "
                "version = dict_entries.version + 1 WHERE name = ?",
                (1 if enabled else 0, _now(), name))
    affected = cur.rowcount
    if affected:
        after = _row_dict(cur, "dict_entries", "name", name)
        _record_history(cur, "dict", name,
                        "enable" if enabled else "disable", before, after, operator)
    conn.commit()
    conn.close()
    return affected > 0


# ═══════════════════════════════════════════════════════════
# ★ Semantic Layer · 实体 / 关系 / 文档 的读写
# ═══════════════════════════════════════════════════════════
def get_entities():
    """取出全部业务实体（P2 层）。返回 [{name,table_name,grain,comment,keywords,enabled,source}]"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT name, table_name, grain, comment, keywords, enabled, source "
        "FROM entities ORDER BY name"
    ).fetchall()
    conn.close()
    return [
        {"name": r[0], "table_name": r[1] or "", "grain": r[2] or "",
         "comment": r[3] or "", "keywords": _from_json(r[4]),
         "enabled": bool(r[5] if len(r) > 5 else 1),
         "source": (r[6] if len(r) > 6 and r[6] else "manual")}
        for r in rows
    ]


def get_entity_by_table(table_name):
    """
    按【主表】反查业务实体 —— 判断"这张表有没有登记过实体"

    ★ 为什么按表名反查而不是按实体名？
      授权新表时手里只有表名，实体名是 AI 现起的，没法拿它当键。
    """
    for e in get_entities():
        if e.get("table_name") == table_name:
            return e
    return None


def upsert_entity(ent, source="manual", operator=None):
    """
    写入/更新一个业务实体（有则覆盖）。返回 (是否写入, 原因)

    ★ source 护栏（和表注释/字段注释同一套规矩）：
        · 已存在且 source='manual' → **AI 不得覆盖**（那是人定过的口径）
        · 人保存时 source='manual'，AI 起草时 source='ai'
      没有这道护栏，AI 每次授权都会把人精心写的关键词冲掉。
    ★ 知识治理：写入变更历史 + updated_at/created_at/version。
    """
    _ensure_ready()
    name = (ent or {}).get("name", "").strip()
    if not name:
        return False, "实体名为空"
    old = next((e for e in get_entities() if e["name"] == name), None)
    if old and old.get("source") == "manual" and source == "ai":
        return False, "已有人工登记的实体，AI 不覆盖"

    conn = _connect()
    cur = conn.cursor()
    before = _row_dict(cur, "entities", "name", name) or old
    action = "update" if before else "create"
    _ts = _now()
    cur.execute("""
        INSERT INTO entities (name, table_name, grain, comment, keywords, enabled, source,
                              updated_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
            table_name = excluded.table_name,
            grain      = excluded.grain,
            comment    = excluded.comment,
            keywords   = excluded.keywords,
            enabled    = excluded.enabled,
            source     = excluded.source,
            updated_at = excluded.updated_at,
            version    = entities.version + 1
    """, (name, ent.get("table_name", ""), ent.get("grain", ""),
          ent.get("comment", ""), _to_json(ent.get("keywords", [])),
          1 if ent.get("enabled", True) else 0, source, _ts, _ts))
    after = _row_dict(cur, "entities", "name", name)
    _record_history(cur, "entity", name, action, before, after, operator)
    conn.commit()
    conn.close()
    return True, "已保存"


def delete_entity(name, operator=None):
    """删除一个业务实体（P2 层，写入变更历史）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    before = _row_dict(cur, "entities", "name", name)
    cur.execute("DELETE FROM entities WHERE name = ?", (name,))
    affected = cur.rowcount
    _record_history(cur, "entity", name, "delete", before, None, operator)
    conn.commit()
    conn.close()
    return affected > 0


def get_relationships(enabled_only=True):
    """取出关联/Join 规则（P2 层）。返回 [{id,from_entity,to_entity,join_sql,join_type,cardinality,pre_agg_required,rule_note,enabled}]"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    sql = ("SELECT id, from_entity, to_entity, join_sql, join_type, cardinality, "
           "pre_agg_required, rule_note, enabled FROM relationships ")
    if enabled_only:
        sql += "WHERE enabled = 1 "
    sql += "ORDER BY id"
    rows = cur.execute(sql).fetchall()
    conn.close()
    return [
        {"id": r[0], "from_entity": r[1] or "", "to_entity": r[2] or "",
         "join_sql": r[3] or "", "join_type": r[4] or "LEFT",
         "cardinality": r[5] or "1:1", "pre_agg_required": bool(r[6]),
         "rule_note": r[7] or "", "enabled": bool(r[8])}
        for r in rows
    ]


def upsert_relationship(rel, operator=None):
    """写入/更新一条关联规则（按 from_entity+to_entity 幂等），写入变更历史。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    fe, te = rel.get("from_entity", ""), rel.get("to_entity", "")
    row = cur.execute(
        "SELECT id FROM relationships WHERE from_entity=? AND to_entity=?",
        (fe, te)).fetchone()
    rel_id = row[0] if row else None
    before = _row_dict(cur, "relationships", "id", rel_id) if rel_id else None
    action = "update" if before else "create"
    _ts = _now()
    if row:
        cur.execute("""
            UPDATE relationships SET join_sql=?, join_type=?, cardinality=?,
                pre_agg_required=?, rule_note=?, enabled=?,
                updated_at=?, version=relationships.version+1 WHERE id=?
        """, (rel.get("join_sql", ""), rel.get("join_type", "LEFT"),
              rel.get("cardinality", "1:1"),
              1 if rel.get("pre_agg_required") else 0, rel.get("rule_note", ""),
              1 if rel.get("enabled", True) else 0, _ts, row[0]))
    else:
        cur.execute("""
            INSERT INTO relationships
                (from_entity, to_entity, join_sql, join_type, cardinality,
                 pre_agg_required, rule_note, enabled, updated_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (fe, te, rel.get("join_sql", ""), rel.get("join_type", "LEFT"),
              rel.get("cardinality", "1:1"),
              1 if rel.get("pre_agg_required") else 0, rel.get("rule_note", ""),
              1 if rel.get("enabled", True) else 0, _ts, _ts))
        rel_id = cur.lastrowid
    after = _row_dict(cur, "relationships", "id", rel_id)
    _record_history(cur, "relationship", rel_id, action, before, after, operator)
    conn.commit()
    conn.close()


def delete_relationship(rel_id, operator=None):
    """删除一条关联规则（P2 层，按 id，写入变更历史）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    before = _row_dict(cur, "relationships", "id", rel_id)
    cur.execute("DELETE FROM relationships WHERE id = ?", (rel_id,))
    affected = cur.rowcount
    _record_history(cur, "relationship", rel_id, "delete", before, None, operator)
    conn.commit()
    conn.close()
    return affected > 0


def get_kb_docs(enabled_only=True):
    """取出业务文档（P4 层）。返回 [{id,title,content,keywords,priority,enabled}]"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    sql = "SELECT id, title, content, keywords, priority, enabled FROM kb_docs "
    if enabled_only:
        sql += "WHERE enabled = 1 "
    sql += "ORDER BY id"
    rows = cur.execute(sql).fetchall()
    conn.close()
    return [
        {"id": r[0], "title": r[1] or "", "content": r[2] or "",
         "keywords": _from_json(r[3]), "priority": r[4] or 4, "enabled": bool(r[5])}
        for r in rows
    ]


def upsert_kb_doc(doc, operator=None):
    """写入/更新一条业务文档，写入变更历史。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    doc_id = doc.get("id")
    before = _row_dict(cur, "kb_docs", "id", doc_id) if doc_id else None
    action = "update" if before else "create"
    _ts = _now()
    if doc_id:
        cur.execute("""
            UPDATE kb_docs SET title=?, content=?, keywords=?, priority=?, enabled=?,
                updated_at=?, version=kb_docs.version+1
            WHERE id=?
        """, (doc.get("title", ""), doc.get("content", ""),
              _to_json(doc.get("keywords", [])), doc.get("priority", 4),
              1 if doc.get("enabled", True) else 0, _ts, doc_id))
    else:
        cur.execute("""
            INSERT INTO kb_docs (title, content, keywords, priority, enabled, updated_at, created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (doc.get("title", ""), doc.get("content", ""),
              _to_json(doc.get("keywords", [])), doc.get("priority", 4),
              1 if doc.get("enabled", True) else 0, _ts, _ts))
        doc_id = cur.lastrowid
    after = _row_dict(cur, "kb_docs", "id", doc_id)
    _record_history(cur, "kb_doc", doc_id, action, before, after, operator)
    conn.commit()
    conn.close()


def delete_kb_doc(doc_id, operator=None):
    """删除一条业务文档（P4 层，按 id，写入变更历史）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    before = _row_dict(cur, "kb_docs", "id", doc_id)
    cur.execute("DELETE FROM kb_docs WHERE id = ?", (doc_id,))
    affected = cur.rowcount
    _record_history(cur, "kb_doc", doc_id, "delete", before, None, operator)
    conn.commit()
    conn.close()
    return affected > 0


def filter_visible_tables(table_names, default_visible=True):
    """
    批量判断多张表是否允许 AI 访问，返回其中"可见"的表名集合

    ★ 为什么要做成批量，而不是让上层逐张判断？
      因为原来的写法是"每张表调一次 is_table_visible"，
      而 is_table_visible 每次都 get_table_acl() —— 每次都【新建一个数据库连接】。
          demo 库：4 张表 × 若干字段 = 二十几次连接（无所谓）
          真实库：300 张表 × 10 字段 = 3000 次连接（点一次"重建索引"就明显变慢）
      这里改成"取一次名单，在内存里判断"，复杂度从 O(行数) 次查询降到 1 次查询。
    """
    acl = get_table_acl()                          # ★ 只查一次库
    result = set()
    for t in table_names:
        info = acl.get(t)                          # 名单里有没有登记过这张表
        visible = info["visible"] if info else default_visible
        #        登记过 → 按登记的来；没登记 → 按默认策略
        if visible:
            result.add(t)
    return result


def get_table_comments():
    """取出全部表注释，返回 {表名: 注释} 字典"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT table_name, comment FROM table_comments")
    rows = cur.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}              # 字典推导式：直接组成字典


def get_table_comment_sources():
    """
    取出每张表注释的【来源】，返回 {表名: "manual" | "ai"}

    ★ 为什么要单独这个接口？
      get_table_comments 只返回文本（给 RAG 用，RAG 不在乎谁写的）；
      页面要展示"这是 AI 临时生成的还是你改过的"，才需要来源。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT table_name, source FROM table_comments")
    rows = cur.fetchall()
    conn.close()
    return {r[0]: r[1] or "manual" for r in rows}


def get_table_comment_source(table_name):
    """取一张表注释的来源；没写过则返回 None"""
    return get_table_comment_sources().get(table_name)


def get_column_comments(table_name=None):
    """
    取出字段注释

    参数 table_name：
        传了  → 只取这张表的字段注释（管理页面下钻用，避免把上万条全捞出来）
        不传  → 取全部

    返回：{"表名.字段名": 注释}
    ★ 键用"表名.字段名"拼，和 db.fetch_comments() 的格式保持一致，
      这样 rag.py 里两路注释可以直接 merge，不用做格式转换。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    if table_name:
        cur.execute("SELECT table_name, column_name, comment FROM column_comments "
                    "WHERE table_name = ?", (table_name,))
    else:
        cur.execute("SELECT table_name, column_name, comment FROM column_comments")
    rows = cur.fetchall()
    conn.close()
    return {f"{r[0]}.{r[1]}": r[2] for r in rows}  # 拼成 "表.字段" 做键


def get_column_comment_sources():
    """
    取全部字段注释的来源 → {"表名.字段名": "manual"|"ai"}

    ★ 和 get_table_comment_sources 成对存在，用途也一样：
      决定"这条注释能不能被 AI 重新生成覆盖"。
      人工改过的永远是 manual，批量补全时会被跳过 ——
      否则 AI 重跑一次就把人一条条核对过的结果洗掉了。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT table_name, column_name, source FROM column_comments")
    rows = cur.fetchall()
    conn.close()
    return {f"{r[0]}.{r[1]}": (r[2] or "manual") for r in rows}


def get_column_comment_source(table_name, column_name):
    """取一个字段注释的来源；没写过则返回 None"""
    return get_column_comment_sources().get(f"{table_name}.{column_name}")


def count_column_comments():
    """统计已维护的字段注释条数（给概览用）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    row = cur.execute("SELECT COUNT(*) FROM column_comments").fetchone()
    conn.close()
    return row[0]


# ═══════════════════════════════════════════════════════════
# 写配置
# ═══════════════════════════════════════════════════════════
def set_prompt(key, content):
    """写入/更新一条 Prompt 配置"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO prompts (key, content) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET content = excluded.content
    """, (key, content))
    #   ON CONFLICT(key) DO UPDATE = 主键冲突时不报错，改成"更新"（UPSERT）
    conn.commit()
    conn.close()


def upsert_metric(metric, operator=None):
    """
    写入/更新一个指标（有则覆盖，无则新增），并写入变更历史。

    ★ enabled 语义：调用方【显式带了】enabled → 一起更新（改口径≠改开放状态，
      停用过的指标不会被无声重开）；没带 → 保留库里原值。
    ★ 扩展列（Semantic Layer 升级新增）：metric_type / priority / sql_template /
      depends_metrics / conflict_key —— 带就写，没带就不动（保留原值/默认值）。
    ★ 知识治理（GAP-2）：每次变更写 knowledge_history（before/after 快照），
      updated_at/updated_by 记时间与操作人，version 每次 +1，created_at 仅创建时记。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()

    before = _row_dict(cur, "metrics", "name", metric["name"])
    action = "update" if before else "create"

    cols = ["name", "keywords", "definition", "doc", "tables"]
    vals = [metric["name"], _to_json(metric.get("keywords", [])),
            metric.get("definition", ""), metric.get("doc", ""),
            _to_json(metric.get("tables", []))]

    for cname in ["metric_type", "priority", "sql_template", "conflict_key"]:
        if metric.get(cname) is not None:
            cols.append(cname)
            vals.append(metric[cname])
    if metric.get("depends_metrics") is not None:
        cols.append("depends_metrics")
        vals.append(_to_json(metric["depends_metrics"]))
    # ★ 硬约束（机器可校验）：JSON 数组，带就写
    if metric.get("required_predicates") is not None:
        cols.append("required_predicates")
        vals.append(_to_json(metric["required_predicates"]))
    if metric.get("required_columns") is not None:
        cols.append("required_columns")
        vals.append(_to_json(metric["required_columns"]))
    if metric.get("forbidden_predicates") is not None:
        cols.append("forbidden_predicates")
        vals.append(_to_json(metric["forbidden_predicates"]))

    if "enabled" in metric:
        cols.append("enabled")
        vals.append(1 if metric["enabled"] else 0)

    # ★ 元数据：updated_at 每次写都记；updated_by 带操作人则记；created_at 仅创建记
    _ts = _now()
    cols.append("updated_at")
    vals.append(_ts)
    if operator is not None:
        cols.append("updated_by")
        vals.append(operator)
    if action == "create":
        cols.append("created_at")
        vals.append(_ts)

    # DO UPDATE：除主键 name 与「创建时间」外其余列覆盖；版本号每次 +1
    updates = ",".join(f"{c}=excluded.{c}" for c in cols
                       if c not in ("name", "created_at"))
    updates += ", version = metrics.version + 1"
    ph = ",".join("?" * len(cols))
    cur.execute(
        f"INSERT INTO metrics ({','.join(cols)}) VALUES ({ph}) "
        f"ON CONFLICT(name) DO UPDATE SET {updates}",
        vals,
    )
    after = _row_dict(cur, "metrics", "name", metric["name"])
    _record_history(cur, "metric", metric["name"], action, before, after, operator)
    conn.commit()
    conn.close()


def delete_metric(name, operator=None):
    """删除一个指标（写入变更历史，删除前快照可追溯）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    before = _row_dict(cur, "metrics", "name", name)
    cur.execute("DELETE FROM metrics WHERE name = ?", (name,))
    affected = cur.rowcount                       # 受影响的行数（0 表示没这条）
    _record_history(cur, "metric", name, "delete", before, None, operator)
    conn.commit()
    conn.close()
    return affected > 0


def set_table_comment(table_name, comment, source="manual", operator=None):
    """
    写入/更新一张表的业务注释（写入变更历史）

    ★ source 参数：
        - "manual"（默认）= 用户手写 / 在 AI 基础上改过 → 以后 AI 不再自动覆盖
        - "ai"     = AI 自动生成的临时注释 → 下次重新生成时可覆盖
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    before = _row_dict(cur, "table_comments", "table_name", table_name)
    action = "update" if before else "create"
    _ts = _now()
    cur.execute("""
        INSERT INTO table_comments (table_name, comment, source, updated_at, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(table_name) DO UPDATE SET
            comment = excluded.comment,
            source  = excluded.source,
            updated_at = excluded.updated_at,
            version = table_comments.version + 1
    """, (table_name, comment, source, _ts, _ts))
    after = _row_dict(cur, "table_comments", "table_name", table_name)
    _record_history(cur, "table_comment", table_name, action, before, after, operator)
    conn.commit()
    conn.close()


def set_column_comment(table_name, column_name, comment, source="manual", operator=None):
    """
    写入/更新一个字段的业务注释（写入变更历史）

    ★ 空注释 = 删除这条记录（回到"没有注释"的状态）
      为什么这样设计？因为页面上的输入框被清空时，语义就是"我不想给它备注了"，
      留一条空字符串记录会让后面的"注释覆盖率"统计虚高（看起来有注释，其实没有）。

    ★ source 参数（默认 manual —— 页面上人敲的，就是人工）：
        AI 批量补全时传 source="ai"，标记为"机器写的、还没人核对"。
        下次批量重跑会覆盖 ai 的，但绝不会碰 manual 的。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()

    def _snap():
        cur.execute("SELECT * FROM column_comments WHERE table_name=? AND column_name=?",
                    (table_name, column_name))
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None

    before = _snap()
    action = "update" if before else "create"
    _ts = _now()
    if comment and comment.strip():
        cur.execute("""
            INSERT INTO column_comments (table_name, column_name, comment, source,
                                         updated_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(table_name, column_name)
            DO UPDATE SET comment = excluded.comment,
                          source  = excluded.source,
                          updated_at = excluded.updated_at,
                          version = column_comments.version + 1
        """, (table_name, column_name, comment.strip(), source, _ts, _ts))
        affected = 1
        after = _snap()
        _record_history(cur, "column_comment", f"{table_name}.{column_name}",
                        action, before, after, operator)
    else:
        cur.execute("DELETE FROM column_comments WHERE table_name = ? AND column_name = ?",
                    (table_name, column_name))
        affected = cur.rowcount
        if affected:
            _record_history(cur, "column_comment", f"{table_name}.{column_name}",
                            "delete", before, None, operator)
    conn.commit()
    conn.close()
    return affected


def delete_column_comment(table_name, column_name, operator=None):
    """删除一个字段的人工注释（回到无注释状态，写入变更历史）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM column_comments WHERE table_name=? AND column_name=?",
                (table_name, column_name))
    _cols = [d[0] for d in cur.description]
    _r = cur.fetchone()
    before = dict(zip(_cols, _r)) if _r else None
    cur.execute("DELETE FROM column_comments WHERE table_name = ? AND column_name = ?",
                (table_name, column_name))
    affected = cur.rowcount
    if affected:
        _record_history(cur, "column_comment", f"{table_name}.{column_name}",
                        "delete", before, None, operator)
    conn.commit()
    conn.close()
    return affected > 0


# ═══════════════════════════════════════════════════════════
# ★ 知识变更历史查询（GAP-2：版本 / 审计可追溯）
# ═══════════════════════════════════════════════════════════
def list_knowledge_history(entity_type=None, entity_id=None, limit=200):
    """查询知识变更历史（可按类型/对象过滤），按时间倒序返回最近 limit 条。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    sql = ("SELECT id, entity_type, entity_id, action, before_json, after_json, "
           "operator, created_at FROM knowledge_history ")
    cond, args = [], []
    if entity_type:
        cond.append("entity_type = ?")
        args.append(entity_type)
    if entity_id is not None:
        cond.append("entity_id = ?")
        args.append(str(entity_id))
    if cond:
        sql += "WHERE " + " AND ".join(cond) + " "
    sql += "ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    rows = cur.execute(sql, args).fetchall()
    conn.close()
    return [
        {"id": r[0], "entity_type": r[1], "entity_id": r[2], "action": r[3],
         "before": json.loads(r[4]) if r[4] else None,
         "after": json.loads(r[5]) if r[5] else None,
         "operator": r[6], "created_at": r[7]}
        for r in rows
    ]


# ═══════════════════════════════════════════════════════════
# ★ 索引重建状态（GAP-3：重建成功/失败落库，检测过期索引）
# ═══════════════════════════════════════════════════════════
def set_index_state(status, counts=None, error=None):
    """记录最近一次 RAG 索引重建状态（ok / failed）与知识计数。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO index_state (key, status, counts_json, error, built_at)
        VALUES ('rag_index', ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            status=excluded.status, counts_json=excluded.counts_json,
            error=excluded.error, built_at=excluded.built_at
    """, (status, json.dumps(counts or {}, ensure_ascii=False), error, _now()))
    conn.commit()
    conn.close()


def get_index_state():
    """读取最近一次 RAG 索引重建状态；从未记录返回 None。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    row = cur.execute("SELECT key, status, counts_json, error, built_at "
                      "FROM index_state WHERE key='rag_index'").fetchone()
    conn.close()
    if not row:
        return None
    return {"key": row[0], "status": row[1],
            "counts": json.loads(row[2]) if row[2] else {},
            "error": row[3], "built_at": row[4]}


def list_prompts():
    """列出所有 Prompt 配置项的名字和长度"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT key, LENGTH(content) FROM prompts ORDER BY key")
    rows = cur.fetchall()
    conn.close()
    return rows


# ═══════════════════════════════════════════════════════════
# ★ 表白名单（访问控制）的读写
# ═══════════════════════════════════════════════════════════
def get_table_acl():
    """
    取出表白名单，返回 {表名: {"visible": True/False, "note": "..."}}
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT table_name, visible, note FROM table_acl")
    rows = cur.fetchall()
    conn.close()
    return {
        r[0]: {"visible": bool(r[1]), "note": r[2] or ""}
        #      bool(1) = True, bool(0) = False
        for r in rows
    }


def is_table_visible(table_name, default_visible=True):
    """
    判断某张表是否允许 AI 访问

    参数 default_visible 就是 ACL_DEFAULT_VISIBLE：
        True  → 黑名单模式（没登记的默认可见）
        False → 白名单模式（没登记的默认不可见）
    """
    acl = get_table_acl()                        # 取整张名单
    if table_name not in acl:                    # 这张表没登记过
        return default_visible                   # 按默认策略决定
    return acl[table_name]["visible"]            # 登记过就按登记的来


def set_table_acl(table_name, visible, note=""):
    """设置某张表是否允许 AI 访问"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO table_acl (table_name, visible, note) VALUES (?,?,?)
        ON CONFLICT(table_name) DO UPDATE SET
            visible = excluded.visible,
            note    = excluded.note
    """, (table_name, 1 if visible else 0, note))
    #   1 if visible else 0：Python 的三元表达式 → 等价于 visible ? 1 : 0
    conn.commit()
    conn.close()


def delete_table_acl(table_name):
    """
    删除某张表的登记记录 —— 让它回到"默认策略"

    ★ 什么时候需要这个？
      移除登记 ≠ 禁止访问，这两件事不一样：
          设成 visible=0  → 明确禁止（黑名单模式下会被屏蔽）
          删除登记        → 回到默认策略（白名单模式下＝不可见，黑名单模式下＝可见）
      白名单模式里两种效果一样；黑名单模式里差别很大。
      所以"取消登记"必须有独立操作，不能拿"设成 0"代替。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM table_acl WHERE table_name = ?", (table_name,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def list_table_acl():
    """列出表白名单（返回原始行，供 admin 打印）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT table_name, visible, note FROM table_acl ORDER BY visible, table_name")
    rows = cur.fetchall()
    conn.close()
    return rows


# ═══════════════════════════════════════════════════════════
# ⑧ ★ 问题反馈（错例复盘）—— 把"踩坑"变成知识库的输入
# ═══════════════════════════════════════════════════════════
#  ★ 为什么错误类型是固定枚举，不让用户随便写？
#    自由文本没法统计。固定几类，才能回答"我们最常错在哪一类"——
#    如果 80% 的错都是"枚举值理解错"，那结论很清楚：优先补字段注释里的枚举说明。
#    这是从"一条条修"升级到"知道该往哪投精力"的关键。
FEEDBACK_REASONS = {
    "wrong_filter": "筛错条件（用了无关字段去过滤）",
    "wrong_enum":   "枚举值理解错（比如 6 当成取消）",
    "wrong_metric": "口径算错（公式/分母不对）",
    "wrong_table":  "用错表 / 该用的表没权限",
    "wrong_time":   "时间范围不对",
    "missing_data": "数据缺失（该有的表/字段没有）",
    # ★ 反馈闭环分类扩展（任务书 §7.1）：覆盖更多根因类型
    "wrong_field":      "选错字段（用了含义不对的字段）",
    "missing_filter":   "过滤条件缺失",
    "join_dup":         "JOIN 导致重复/行数膨胀",
    "agg_grain":        "聚合粒度错误（明细×明细 / 去重不对）",
    "sql_error":        "SQL 执行失败（语法/函数/类型）",
    "data_error":       "数据源本身异常",
    "result_mismatch":  "查询结果与业务认知不符",
    "knowledge_missing":"知识缺失（口径/关系/字典未登记）",
    "permission_denied":"权限不足",
    "other":        "其他（请补充说明）",
}


def _now():
    """当前时间字符串（统一格式，方便排序和展示）"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_feedback(item):
    """
    新增一条反馈，返回新记录的 id

    item 里除了 question/sql/reason 这些，最好把【当时的上下文】也带上
    （命中了哪些表/指标/字典）—— 复盘时能省掉"回忆当时发生了什么"。

    ★ 反馈闭环（GAP-6）：可选带 related_knowledge_id / regression_sample_id / root_cause，
      把反馈显式关联到被修正的知识或转化出的回归样例。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    now = _now()
    cur.execute("""
        INSERT INTO feedbacks
            (question, sql, result, exec_ok, reason, detail,
             tables, metrics, dicts, status, note, created_at, updated_at,
             related_knowledge_id, regression_sample_id, root_cause)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        item.get("question", ""),
        item.get("sql", ""),
        item.get("result", ""),
        1 if item.get("exec_ok") else 0,
        item.get("reason", "other"),
        item.get("detail", ""),
        _to_json(item.get("tables") or []),
        _to_json(item.get("metrics") or []),
        _to_json(item.get("dicts") or []),
        item.get("status", "pending"),
        item.get("note", ""),
        now, now,
        item.get("related_knowledge_id"),
        item.get("regression_sample_id"),
        item.get("root_cause"),
    ))
    fid = cur.lastrowid                      # 刚插入那行的自增 id
    conn.commit()
    conn.close()
    return fid


def get_feedbacks(status=None, limit=200):
    """
    取反馈列表（按时间倒序）

    status = None 取全部；也可传 "pending" / "resolved" / "ignored"
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    if status:
        cur.execute("SELECT * FROM feedbacks WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (status, limit))
    else:
        cur.execute("SELECT * FROM feedbacks ORDER BY id DESC LIMIT ?", (limit,))
    cols = [d[0] for d in cur.description]   # 列名（用 SELECT * 时要自己取）
    rows = cur.fetchall()
    conn.close()

    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d["tables"] = _from_json(d.get("tables")) or []
        d["metrics"] = _from_json(d.get("metrics")) or []
        d["dicts"] = _from_json(d.get("dicts")) or []
        d["exec_ok"] = bool(d.get("exec_ok"))
        out.append(d)
    return out


def update_feedback(fid, status=None, note=None, related_knowledge_id=None,
                    regression_sample_id=None, root_cause=None):
    """
    更新反馈的处理状态 / 备注 / 闭环关联字段

    ★ 为什么要 note（处理备注）？
      复盘的价值在于"下次不再犯"。只把状态改成"已处理"，
      过两个月没人记得当时改了什么、为什么这么改。
      写一句"补了 status 的枚举含义"，这条记录才有长期价值。

    ★ 反馈闭环（GAP-6）：管理员把有效反馈处理成知识/回归样例后，
      用 related_knowledge_id / regression_sample_id 把反馈与产出关联起来，
      再通过 root_cause 记录根因，形成「反馈 → 归类 → 修正 → 回归」可追溯闭环。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    sets, args = [], []
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if note is not None:
        sets.append("note = ?")
        args.append(note)
    if related_knowledge_id is not None:
        sets.append("related_knowledge_id = ?")
        args.append(related_knowledge_id)
    if regression_sample_id is not None:
        sets.append("regression_sample_id = ?")
        args.append(regression_sample_id)
    if root_cause is not None:
        sets.append("root_cause = ?")
        args.append(root_cause)
    if not sets:                             # 什么都没传，就当没改
        conn.close()
        return False
    sets.append("updated_at = ?")
    args.append(_now())
    args.append(fid)
    cur.execute(f"UPDATE feedbacks SET {', '.join(sets)} WHERE id = ?", args)
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_feedback(fid):
    """删除一条反馈（确认是误报时用）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM feedbacks WHERE id = ?", (fid,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# ═══════════════════════════════════════════════════════════
# ⑨ ★ 账号与权限（管理员 / 普通用户）
# ═══════════════════════════════════════════════════════════
#  【怎么分工？】
#    管理员：知识库 / Prompt / 表权限 / 账号 / 处理反馈 —— 维护"口径资产"
#    普通用户：取数 + 反馈 —— 只消费，不改资产
#  【为什么口令要用 PBKDF2 而不是简单 sha256？】
#    sha256 太快了 —— 攻击者一秒能算上亿次，口令字典瞬间跑完。
#    PBKDF2 故意"算得慢"（十几万次迭代），把暴力破解的成本抬高几个数量级；
#    再加"每人一个随机盐"，让彩虹表彻底失效。
# ═══════════════════════════════════════════════════════════

VALID_ROLES = ("admin", "user")
ROLE_ZH = {"admin": "管理员", "user": "普通用户"}

# ★ 首次建库时内置的管理员。
#   ★ 不再硬编码 admin123：优先读 ADMIN_INITIAL_PASSWORD；
#     没配就生成随机密码，只在首次初始化时打印一次。
#     首次登录必须改密（must_change_password=1），避免"默认口令长期在线"。
DEFAULT_ADMIN = {
    "username": "admin",
    "display_name": "系统管理员",
    "role": "admin",
}


def _initial_admin_password():
    """初始管理员密码：环境变量优先，否则随机生成（12 字符左右）。"""
    pwd = (os.getenv("ADMIN_INITIAL_PASSWORD") or "").strip()
    if pwd:
        return pwd, False
    return secrets.token_urlsafe(9), True

_PBKDF2_ITER = 120000          # 迭代次数：越大越安全，也越慢（12 万次约几十毫秒）
_SESSION_HOURS = 12            # 会话有效期（小时）：到点自动失效，需重新登录


def _new_salt():
    """生成 16 字节随机盐（密码学安全随机源）"""
    return secrets.token_hex(16)


def _hash_pw(password, salt):
    """把「口令 + 盐」派生成不可逆的十六进制串"""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("utf-8"), _PBKDF2_ITER)
    return dk.hex()


def _user_row_to_dict(row):
    """数据库行 → 对外字典。★ 默认剔除 password_hash / salt，避免口令材料外泄"""
    cols = ["username", "display_name", "password_hash", "salt",
            "role", "enabled", "must_change_password", "created_at", "updated_at",
            "user_attributes"]
    d = dict(zip(cols, row))
    d["role_zh"] = ROLE_ZH.get(d.get("role"), d.get("role"))
    d["enabled"] = bool(d.get("enabled"))
    d["must_change_password"] = bool(d.get("must_change_password"))
    # ★ 行级权限的已验证身份属性（如 dept_id / region），存 JSON，读出来是 dict
    try:
        d["user_attributes"] = json.loads(d.get("user_attributes")) if d.get("user_attributes") else {}
    except Exception:
        d["user_attributes"] = {}
    return d


def _public_user(d):
    """只保留能返回给前端看的字段（绝不带口令材料）"""
    return {
        "username": d.get("username"),
        "display_name": d.get("display_name") or d.get("username"),
        "role": d.get("role"),
        "role_zh": d.get("role_zh") or ROLE_ZH.get(d.get("role"), d.get("role")),
        "enabled": bool(d.get("enabled")),
        "must_change_password": bool(d.get("must_change_password")),
        "user_attributes": d.get("user_attributes") or {},
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


def ensure_default_admin():
    """没有任何账号时，内置一个管理员，避免"锁在门外"（首次使用能进得来）

    ★ 安全要求：
      ① 不再使用固定口令 admin123；
      ② 初始口令只在这个函数里出现一次（随机生成时打印，环境变量配置时不打印）；
      ③ 首次登录强制改密（must_change_password=1）。
    """
    conn = _connect()
    cur = conn.cursor()
    n = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if n == 0:
        password, generated = _initial_admin_password()
        salt = _new_salt()
        now = _now()
        cur.execute(
            "INSERT INTO users (username, display_name, password_hash, salt, "
            "role, enabled, must_change_password, created_at, updated_at) "
            "VALUES (?,?,?,?,?,1,1,?,?)",
            (DEFAULT_ADMIN["username"], DEFAULT_ADMIN["display_name"],
             _hash_pw(password, salt), salt,
             DEFAULT_ADMIN["role"], now, now),
        )
        conn.commit()
        if generated:
            print(f"★ 已初始化默认管理员：{DEFAULT_ADMIN['username']} / {password}")
            print("  （随机初始密码，仅显示这一次；首次登录必须修改）")
        else:
            print(f"★ 已初始化默认管理员：{DEFAULT_ADMIN['username']} "
                  "（初始密码取自 ADMIN_INITIAL_PASSWORD，首次登录必须修改）")
    conn.close()


def flag_default_password_admins():
    """老库迁移：把仍在用公开默认口令 admin123 的管理员标记为「必须改密」。

    ★ 只处理 admin123 这一个已知公开口令，不动用户自己设置的其他密码。
    ★ 默认开启；如需保留旧行为（不推荐），设 ADMIN_FORCE_CHANGE=0。
    返回被标记的账号数。
    """
    if (os.getenv("ADMIN_FORCE_CHANGE", "1").strip().lower()
            not in ("1", "true", "yes", "on")):
        return 0
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT username, salt, password_hash FROM users "
        "WHERE role = 'admin' AND must_change_password = 0"
    ).fetchall()
    n = 0
    for username, salt, pw_hash in rows:
        if hmac.compare_digest(pw_hash or "", _hash_pw("admin123", salt or "")):
            cur.execute(
                "UPDATE users SET must_change_password = 1, updated_at = ? "
                "WHERE username = ?", (_now(), username))
            n += 1
    if n:
        conn.commit()
        print(f"⚠️ 检测到 {n} 个管理员仍在使用公开默认口令，已标记为首次登录必须改密")
    conn.close()
    return n


def create_user(username, password, role="user", display_name=""):
    """新建账号。返回对外字典。

    ★ 校验都在这一层做（而不是靠前端）—— 前端能绕过，后端不能。
    ★ 登录名限定字符集：既避免"看起来像两个人其实是同一个"的歧义账号，
      也避免登录名里的引号/尖括号跑到前端 HTML 里（存储型 XSS 的常见入口）。
    """
    import re as _re
    username = (username or "").strip()
    if not username:
        raise ValueError("登录名不能为空")
    if not _re.fullmatch(r"[A-Za-z0-9_.\-]{2,32}", username):
        raise ValueError("登录名只能用 2~32 位字母 / 数字 / _ . -")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    if role not in VALID_ROLES:
        raise ValueError(f"未知角色：{role}")
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    if cur.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        conn.close()
        raise ValueError(f"账号已存在：{username}")
    salt = _new_salt()
    now = _now()
    cur.execute(
        "INSERT INTO users (username, display_name, password_hash, salt, "
        "role, enabled, must_change_password, created_at, updated_at) "
        "VALUES (?,?,?,?,?,1,1,?,?)",
        (username, display_name or username, _hash_pw(password, salt),
         salt, role, now, now),
    )
    conn.commit()
    conn.close()
    return _public_user(get_user(username))


def get_user(username):
    """按登录名取账号（含口令材料，仅供内部校验用）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    row = cur.execute(
        "SELECT username, display_name, password_hash, salt, role, enabled, "
        "must_change_password, created_at, updated_at, user_attributes "
        "FROM users WHERE username = ?",
        (username,)
    ).fetchone()
    conn.close()
    return _user_row_to_dict(row) if row else None


def list_users():
    """全部账号（不含口令材料），管理员在前、再按创建时间"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT username, display_name, password_hash, salt, role, enabled, "
        "must_change_password, created_at, updated_at, user_attributes "
        "FROM users ORDER BY role, created_at"
    ).fetchall()
    conn.close()
    return [_public_user(_user_row_to_dict(r)) for r in rows]


def count_enabled_admins():
    """启用中的管理员数量 —— 用于防止"把最后一个管理员删掉/停用"导致无人能管"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    n = cur.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND enabled = 1"
    ).fetchone()[0]
    conn.close()
    return n


def update_user(username, role=None, enabled=None, display_name=None, password=None,
                new_username=None):
    """
    改角色 / 启停 / 显示名 / 重设口令 / 改登录名（重命名）。返回对外字典。

    ★ 安全约束：不允许把最后一个启用中的管理员降级或停用（否则系统失去管理入口）。
    ★ 任何改动都会清掉该用户的会话 —— 让"停用/降权/改名"立即生效，不必等 token 过期。
    ★ 改登录名会同步更新 sessions / query_records 里对该账号的引用，
      避免改名后"旧会话还有效"或"历史记录找不到人"。
    """
    import re as _re
    _ensure_ready()
    cur_user = get_user(username)
    if not cur_user:
        raise ValueError(f"账号不存在：{username}")

    if role is not None and role not in VALID_ROLES:
        raise ValueError(f"未知角色：{role}")

    losing_admin = (
        cur_user["role"] == "admin" and cur_user["enabled"]
        and ((role is not None and role != "admin") or (enabled is False))
    )
    if losing_admin and count_enabled_admins() <= 1:
        raise ValueError("这是唯一启用中的管理员，不能降级或停用")

    conn = _connect()
    cur = conn.cursor()

    # ── 改登录名（主键值重命名）：先做，后续所有 SET 都用新名当 WHERE ──
    final_username = username
    if new_username is not None and new_username != username:
        nw = (new_username or "").strip()
        if not _re.fullmatch(r"[A-Za-z0-9_.\-]{2,32}", nw):
            conn.close()
            raise ValueError("新登录名只能用 2~32 位字母 / 数字 / _ . -")
        if cur.execute("SELECT 1 FROM users WHERE username = ?", (nw,)).fetchone():
            conn.close()
            raise ValueError(f"账号已存在：{nw}")
        # 同步引用旧登录名的会话与历史记录，避免"改名后旧会话还有效 / 历史找不到人"
        cur.execute("UPDATE sessions SET username = ? WHERE username = ?", (nw, username))
        cur.execute("UPDATE query_records SET username = ? WHERE username = ?", (nw, username))
        cur.execute("UPDATE users SET username = ? WHERE username = ?", (nw, username))
        final_username = nw

    sets, args = [], []
    if role is not None:
        sets.append("role = ?"); args.append(role)
    if enabled is not None:
        sets.append("enabled = ?"); args.append(1 if enabled else 0)
    if display_name is not None:
        sets.append("display_name = ?"); args.append(display_name)
    if password is not None:
        if len(password) < 6:
            conn.close()
            raise ValueError("密码至少 6 位")
        salt = _new_salt()
        sets.append("password_hash = ?"); args.append(_hash_pw(password, salt))
        sets.append("salt = ?"); args.append(salt)
        # ★ 管理员重置密码后，用户下次登录必须改密（避免管理员知道用户口令）
        sets.append("must_change_password = 1")

    changed = bool(sets) or (final_username != username)
    if not changed:
        conn.close()
        return _public_user(get_user(username))

    sets.append("updated_at = ?"); args.append(_now())
    args.append(final_username)
    cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE username = ?", args)
    conn.commit()
    conn.close()

    # ★ 口令变了 / 被停用 / 被降权 / 改了登录名 → 现有会话一律作废，防止"旧会话还拿着旧身份"
    delete_user_sessions(final_username)
    return _public_user(get_user(final_username))


def delete_user(username):
    """删除账号（同样不允许删掉最后一个管理员）"""
    _ensure_ready()
    u = get_user(username)
    if not u:
        raise ValueError(f"账号不存在：{username}")
    if u["role"] == "admin" and u["enabled"] and count_enabled_admins() <= 1:
        raise ValueError("这是唯一启用中的管理员，不能删除")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    delete_user_sessions(username)
    return True


def verify_user(username, password):
    """校验口令。成功返回对外字典，失败返回 None（不区分"账号不存在/口令错"，避免枚举账号）"""
    u = get_user(username)
    if not u:
        #   仍然做一次哈希，抹平"账号不存在"与"口令错"的响应时间差异
        _hash_pw(password or "", _new_salt())
        return None
    if not u["enabled"]:
        return None
    expected = u["password_hash"]
    actual = _hash_pw(password or "", u["salt"])
    if not hmac.compare_digest(expected, actual):
        return None
    return _public_user(u)


def change_own_password(username, old_password, new_password):
    """用户自助改密（首次登录强制改密也走这里）。

    ★ 必须校验原密码：否则拿到一个有效 token 就能直接改密接管账号。
    ★ 改完清除 must_change_password 标记。
    """
    u = get_user(username)
    if not u:
        raise ValueError("账号不存在")
    if not hmac.compare_digest(u["password_hash"],
                               _hash_pw(old_password or "", u["salt"])):
        raise ValueError("原密码不正确")
    if not new_password or len(new_password) < 6:
        raise ValueError("新密码至少 6 位")
    if new_password == old_password:
        raise ValueError("新密码不能与原密码相同")

    salt = _new_salt()
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 0, "
        "updated_at = ? WHERE username = ?",
        (_hash_pw(new_password, salt), salt, _now(), username),
    )
    conn.commit()
    conn.close()
    return _public_user(get_user(username))


# ── 会话 ──
def create_session(username, hours=_SESSION_HOURS):
    """登录成功 → 发一个随机 token（前端保存，之后每次请求带上）"""
    _ensure_ready()
    from datetime import datetime, timedelta
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (token, username, created_at, expires_at) VALUES (?,?,?,?)",
        (token, username, now.strftime("%Y-%m-%d %H:%M:%S"),
         (now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()
    return token


def get_session_user(token):
    """token → 账号（顺带检查过期时间；过期即视为未登录）"""
    if not token:
        return None
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    row = cur.execute("SELECT username, expires_at FROM sessions WHERE token = ?",
                      (token,)).fetchone()
    conn.close()
    if not row:
        return None
    username, expires_at = row
    if expires_at and expires_at < _now():      # 字符串按同格式可比
        delete_session(token)
        return None
    u = get_user(username)
    if not u or not u["enabled"]:               # 账号被停用 → 会话立即失效
        delete_session(token)
        return None
    return _public_user(u)


def delete_session(token):
    """登出 / 失效时删掉会话"""
    if not token:
        return
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def delete_user_sessions(username):
    """清掉某账号的所有会话（停用/改口令/改角色时调用）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def purge_expired_sessions():
    """清掉过期会话（登录时顺手清理，避免表无限增长）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


# ═══════════════════════════════════════════════════════════
# ⑩ ★ 取数记录 + 审核（个人历史 / 管理员复核）
# ═══════════════════════════════════════════════════════════
#  【两条核心规则】
#    ① 记录是【自动】落的：每次 /api/ask 都写一条，不依赖前端上报 ——
#       靠前端上报就能被绕过，历史就不完整了。
#    ② 只有【本人】能提交自己的记录、催审核；只有【管理员】能改审核结论。
#       权限在函数这一层就校验（而不是只在前端藏按钮）。
# ═══════════════════════════════════════════════════════════

REVIEW_STATUS_ZH = {
    "none": "未提交审核",
    "pending": "待管理员审核",
    "approved": "管理员已审核通过",
    "rejected": "审核未通过",
}


def add_query_record(item):
    """
    落一条取数记录（每次取数都调）。返回新记录 id。

    ★ 只存"摘要"不存全量结果：结果可能上万行，存进来会把配置库撑爆。
      审核要看的是「SQL + 命中表/指标 + 结论」，前几行足够。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    now = _now()
    cur.execute("""
        INSERT INTO query_records
            (username, question, sql, ok, tables, metrics, dicts,
             columns, row_count, result_preview, answer,
             info, attempts, pipeline, understanding,
             review_status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'none', ?)
    """, (
        item.get("username", ""),
        item.get("question", ""),
        item.get("sql", ""),
        1 if item.get("ok") else 0,
        _to_json(item.get("tables") or []),
        _to_json(item.get("metrics") or []),
        _to_json(item.get("dicts") or []),
        _to_json(item.get("columns") or []),
        int(item.get("row_count") or 0),
        item.get("result_preview", ""),
        item.get("answer", ""),
        item.get("info", ""),
        int(item.get("attempts") or 0),
        item.get("pipeline", ""),
        #   ★ 查询理解存 JSON：它是"当时模型怎么理解这个问题的"证据，
        #     审核时要靠它判断"是口径错还是理解错"——没有它只能猜。
        _to_json(item.get("understanding")) if item.get("understanding") else "",
        now,
    ))
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def _record_row_to_dict(cols, row):
    """
    记录行 → 字典。

    ★ 为什么用"列名"而不是写死的位置顺序？
      老库是用 ALTER TABLE 加列的，新列会追加到【表末尾】，
      和新建库时的列顺序【不一样】。若按位置 zip，'info' 可能正好落在
      'review_status' 上 —— 数据会静默串到错误的字段里，而且看起来"有值"，
      极难排查。按列名取就与物理顺序无关，升不升级都不会错。
    """
    d = dict(zip(cols, row))
    for k in ("tables", "metrics", "dicts", "columns"):
        d[k] = _from_json(d.get(k)) or []
    # 查询理解是 JSON（可能为空/老数据）
    d["understanding"] = _from_json(d.get("understanding")) or None
    d["ok"] = bool(d.get("ok"))
    d["attempts"] = int(d.get("attempts") or 0)
    d["review_status_zh"] = REVIEW_STATUS_ZH.get(d.get("review_status"), d.get("review_status"))
    d["remind_count"] = int(d.get("remind_count") or 0)
    return d


def get_query_records(username=None, limit=50, review_status=None,
                      only_submitted=False, pending_first=True):
    """
    取数历史。username=None 取全部（管理员视角）。

    only_submitted=True → 只要提交过审核的（审核页用）
    pending_first=False → 纯时间倒序（「最近」页用，收藏不置顶、审核状态也不置顶）
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    where, args = [], []
    if username:
        where.append("username = ?"); args.append(username)
    if review_status:
        where.append("review_status = ?"); args.append(review_status)
    if only_submitted:
        where.append("review_status != 'none'")
    sql = "SELECT * FROM query_records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if pending_first:
        # ★ 审核页：待审核排最前（一打开就是待办），再按时间倒序
        sql += (" ORDER BY CASE review_status WHEN 'pending' THEN 0 "
                "WHEN 'rejected' THEN 1 ELSE 2 END, id DESC LIMIT ?")
    else:
        # ★ 「最近」页：纯时间倒序 —— 收藏不置顶，待审/驳回也不插队
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(limit)
    cur.execute(sql, args)
    cols = [c[0] for c in cur.description]       # ★ 按真实列名取值，不写死顺序
    rows = cur.fetchall()
    conn.close()
    return [_record_row_to_dict(cols, r) for r in rows]


def get_query_record(rid):
    """取单条记录（含审核状态）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM query_records WHERE id = ?", (rid,))
    cols = [c[0] for c in cur.description]
    row = cur.fetchone()
    conn.close()
    return _record_row_to_dict(cols, row) if row else None


def submit_for_review(rid, username):
    """
    提交审核（只有本人能提自己的记录）。

    ★ 为什么必须校验 username？
      接口是按 id 提交的。不校验的话，甲可以提交乙的记录、还能冒用别人的名字，
      审核队列里就会出现"谁提的都分不清"的脏数据。
    """
    rec = get_query_record(rid)
    if not rec:
        raise ValueError(f"记录不存在：{rid}")
    if rec["username"] != username:
        raise ValueError("只能提交自己的取数记录")
    if rec["review_status"] == "pending":
        raise ValueError("这条已经在待审核队列里了")
    if rec["review_status"] == "approved":
        raise ValueError("这条已经审核通过了，无需重复提交")
    conn = _connect()
    cur = conn.cursor()
    now = _now()
    cur.execute("""UPDATE query_records
                   SET review_status='pending', submitted_at=?, review_note=''
                   WHERE id=?""", (now, rid))
    conn.commit()
    conn.close()
    return get_query_record(rid)


def remind_review(rid, username):
    """催管理员审核（本人才能催；只统计次数，不改结论）"""
    rec = get_query_record(rid)
    if not rec:
        raise ValueError(f"记录不存在：{rid}")
    if rec["username"] != username:
        raise ValueError("只能催自己的取数记录")
    if rec["review_status"] != "pending":
        raise ValueError("这条不在待审核状态，无需提醒")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""UPDATE query_records
                   SET remind_count = remind_count + 1, reminded_at = ?
                   WHERE id = ?""", (_now(), rid))
    conn.commit()
    conn.close()
    return get_query_record(rid)


def review_record(rid, reviewer, status, note=""):
    """
    管理员给出审核结论：approved / rejected。

    ★ 结论必须带"谁审的 + 什么时间"，否则过两个月没人知道该找谁问。
    ★ note 强烈建议写清"口径对不对、错在哪"，空备注的审核等于没审。
    """
    if status not in ("approved", "rejected"):
        raise ValueError(f"未知审核结论：{status}")
    rec = get_query_record(rid)
    if not rec:
        raise ValueError(f"记录不存在：{rid}")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""UPDATE query_records
                   SET review_status=?, reviewer=?, review_note=?, reviewed_at=?
                   WHERE id=?""", (status, reviewer, note or "", _now(), rid))
    conn.commit()
    conn.close()
    return get_query_record(rid)


def count_pending_reviews():
    """待审核条数（管理端角标用）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    n = cur.execute(
        "SELECT COUNT(*) FROM query_records WHERE review_status = 'pending'"
    ).fetchone()[0]
    conn.close()
    return n


def count_my_submissions(username):
    """我的提交里待审核/未通过的条数（用户端角标用）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    row = cur.execute(
        "SELECT SUM(review_status='pending'), SUM(review_status='rejected') "
        "FROM query_records WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    return {"pending": int(row[0] or 0), "rejected": int(row[1] or 0)}


def delete_query_record(rid, username=None):
    """
    删除一条记录。username 不传 = 管理员删任意；传了 = 只能删自己的。
    """
    rec = get_query_record(rid)
    if not rec:
        return False
    if username and rec["username"] != username:
        raise ValueError("只能删除自己的取数记录")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM query_records WHERE id = ?", (rid,))
    n = cur.rowcount
    # 同步清理快照与收藏，避免孤儿数据
    cur.execute("DELETE FROM query_result_snapshots WHERE record_id = ?", (rid,))
    cur.execute("DELETE FROM favorites WHERE object_type = 'record' AND object_id = ?",
                (str(rid),))
    conn.commit()
    conn.close()
    return n > 0


# ═══════════════════════════════════════════════════════════
# ⑪ 查询结果快照（导出）
# ═══════════════════════════════════════════════════════════
_SNAPSHOT_MAX_ROWS = 2000


def save_result_snapshot(record_id, columns, rows):
    """保存一次成功取数的结果快照（导出用）。失败不影响主流程。"""
    _ensure_ready()
    cols = list(columns or [])
    body = list(rows or [])[:_SNAPSHOT_MAX_ROWS]
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO query_result_snapshots "
        "(record_id, columns_json, rows_json, row_count, created_at) "
        "VALUES (?,?,?,?,?)",
        (int(record_id), _to_json(cols),
         json.dumps(body, ensure_ascii=False, default=str),
         len(body), _now()),
    )
    conn.commit()
    conn.close()


def get_result_snapshot(record_id):
    """取结果快照；没有则返回 None（老记录/失败记录）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    row = cur.execute(
        "SELECT columns_json, rows_json, row_count, created_at "
        "FROM query_result_snapshots WHERE record_id = ?", (int(record_id),)
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        rows = json.loads(row[1] or "[]")
    except Exception:
        rows = []
    return {"columns": _from_json(row[0]) or [], "rows": rows,
            "row_count": row[2] or len(rows), "created_at": row[3]}


# ═══════════════════════════════════════════════════════════
# ⑫ 收藏
# ═══════════════════════════════════════════════════════════
def add_favorite(username, object_type, object_id):
    """收藏；已收藏则幂等返回 False"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO favorites (username, object_type, object_id, created_at) "
        "VALUES (?,?,?,?)", (username, object_type, str(object_id), _now()))
    added = cur.rowcount > 0
    conn.commit()
    conn.close()
    return added


def remove_favorite(username, object_type, object_id):
    """取消收藏；未收藏返回 False"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM favorites WHERE username = ? AND object_type = ? AND object_id = ?",
        (username, object_type, str(object_id)))
    removed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return removed


def is_favorite(username, object_type, object_id):
    """是否已收藏"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    row = cur.execute(
        "SELECT 1 FROM favorites WHERE username = ? AND object_type = ? AND object_id = ?",
        (username, object_type, str(object_id))).fetchone()
    conn.close()
    return bool(row)


def list_favorites(username, object_type=None):
    """列出某人的收藏（按时间倒序）"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    if object_type:
        rows = cur.execute(
            "SELECT id, object_type, object_id, created_at FROM favorites "
            "WHERE username = ? AND object_type = ? ORDER BY id DESC",
            (username, object_type)).fetchall()
    else:
        rows = cur.execute(
            "SELECT id, object_type, object_id, created_at FROM favorites "
            "WHERE username = ? ORDER BY id DESC", (username,)).fetchall()
    conn.close()
    return [{"id": r[0], "object_type": r[1], "object_id": r[2],
             "created_at": r[3]} for r in rows]


def list_favorite_records(username):
    """收藏取数页专用：联表取回记录原文（问题 / SQL / 审核状态）。

    ★ 为什么不能只返回 object_id？
      收藏页要显示“当时问的是什么”。只给 id 的话前端还得逐条再查一次，
      既慢又容易失败；联表一次取回，问题文本也就自然保留下来了。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT f.id AS fav_id, f.created_at AS fav_at,
               q.id, q.question, q.sql, q.ok, q.row_count,
               q.review_status, q.pipeline, q.created_at
        FROM favorites f
        JOIN query_records q ON CAST(f.object_id AS INTEGER) = q.id
        WHERE f.username = ? AND f.object_type = 'record'
        ORDER BY q.created_at DESC, q.id DESC
    """, (username,)).fetchall()
    conn.close()
    return [{
        "id": r[2],
        "question": r[3] or "",
        "sql": r[4] or "",
        "ok": bool(r[5]),
        "row_count": r[6] or 0,
        "review_status": r[7] or "none",
        "pipeline": r[8] or "",
        "created_at": r[9],
        "favorited_at": r[1],
    } for r in rows]


# ═══════════════════════════════════════════════════════════
# ★ 数据权限体系 CRUD（角色 / 绑定 / 数据域 / 敏感资源 / 策略 / 版本 / 审计 / 草稿 / 行级）
# ═══════════════════════════════════════════════════════════
#  原则：
#   · 只增不删用户可见数据（删除用 enabled=0 / status 标记，审计可追溯）；
#   · 敏感资源映射默认 mapping_status='pending'（AI 推荐候选），
#     管理员确认后才 'confirmed' 参与生效判定；
#   · 策略变更写版本表，回滚有据。
# ═══════════════════════════════════════════════════════════


# ── 角色 ──
def list_roles():
    """全部角色（内置在前）。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT name, display_name, description, builtin, created_at, updated_at "
        "FROM roles ORDER BY builtin DESC, name").fetchall()
    conn.close()
    keys = ["name", "display_name", "description", "builtin", "created_at", "updated_at"]
    return [dict(zip(keys, r)) for r in rows]


def upsert_role(name, display_name="", description="", builtin=0):
    """新建/更新自定义角色（内置角色不允许改名）。"""
    import re as _re
    name = (name or "").strip()
    if not _re.fullmatch(r"[A-Za-z0-9_.\-]{2,32}", name):
        raise ValueError("角色名只能用 2~32 位字母/数字/_ . -")
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    existing = cur.execute("SELECT builtin FROM roles WHERE name = ?", (name,)).fetchone()
    if existing and existing[0]:
        conn.close()
        raise ValueError("内置角色不可修改")
    now = _now()
    cur.execute(
        "INSERT INTO roles (name, display_name, description, builtin, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET display_name=excluded.display_name, "
        "description=excluded.description, updated_at=excluded.updated_at",
        (name, display_name or name, description, builtin, now, now))
    conn.commit()
    conn.close()
    return {"ok": True, "name": name}


def delete_role(name):
    """删除自定义角色（内置角色拒绝；同时清绑定）。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    row = cur.execute("SELECT builtin FROM roles WHERE name = ?", (name,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"角色不存在：{name}")
    if row[0]:
        conn.close()
        raise ValueError("内置角色不可删除")
    cur.execute("DELETE FROM roles WHERE name = ?", (name,))
    cur.execute("DELETE FROM user_role_bindings WHERE role_name = ?", (name,))
    cur.execute("DELETE FROM permission_policies WHERE subject_type='role' AND subject_name = ?", (name,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── 用户-角色绑定 ──
def get_user_roles(username):
    """某用户的全部自定义角色绑定（不含内置，内置由 users.role 提供）。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT role_name FROM user_role_bindings WHERE username = ? ORDER BY role_name",
        (username,)).fetchall()
    conn.close()
    return [r[0] for r in rows]


def list_user_role_bindings():
    """全部绑定（管理界面展示用）。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT username, role_name, created_by, created_at "
        "FROM user_role_bindings ORDER BY username, role_name").fetchall()
    conn.close()
    return [{"username": r[0], "role_name": r[1], "created_by": r[2], "created_at": r[3]}
            for r in rows]


def set_user_roles(username, roles, operator=""):
    """整体替换某用户的角色绑定（roles 为角色名列表）。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_role_bindings WHERE username = ?", (username,))
    now = _now()
    for r in (roles or []):
        cur.execute(
            "INSERT OR IGNORE INTO user_role_bindings (username, role_name, created_by, created_at) "
            "VALUES (?,?,?,?)", (username, r, operator, now))
    conn.commit()
    conn.close()
    return {"ok": True, "username": username, "roles": list(roles or [])}


# ── 数据域 ──
def list_data_domains():
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT name, display_name, description, owner, enabled, created_at, updated_at "
        "FROM data_domains ORDER BY name").fetchall()
    conn.close()
    keys = ["name", "display_name", "description", "owner", "enabled", "created_at", "updated_at"]
    return [dict(zip(keys, r)) for r in rows]


def upsert_data_domain(name, display_name="", description="", owner="", enabled=1):
    name = (name or "").strip()
    if not name:
        raise ValueError("数据域标识不能为空")
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "INSERT INTO data_domains (name, display_name, description, owner, enabled, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET display_name=excluded.display_name, "
        "description=excluded.description, owner=excluded.owner, enabled=excluded.enabled, "
        "updated_at=excluded.updated_at",
        (name, display_name or name, description, owner, 1 if enabled else 0, now, now))
    conn.commit()
    conn.close()
    return {"ok": True, "name": name}


def delete_data_domain(name):
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM data_domains WHERE name = ?", (name,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── 敏感资源映射 ──
def get_sensitive_resources(domain=None, table=None, confirmed_only=True):
    """
    敏感资源列表。confirmed_only=True 时只返回管理员已确认的映射
    （AI 推荐的 pending 候选不参与生效判定）。
    """
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    sql = ("SELECT id, domain_name, table_name, raw_table, column_name, raw_column, "
           "sensitivity, aliases, mapping_status, note, owner, created_at, updated_at "
           "FROM sensitive_resources WHERE 1=1")
    args = []
    if domain:
        sql += " AND domain_name = ?"
        args.append(domain)
    if table:
        sql += " AND table_name = ?"
        args.append(table)
    if confirmed_only:
        sql += " AND mapping_status = 'confirmed'"
    sql += " ORDER BY table_name, column_name"
    rows = cur.execute(sql, args).fetchall()
    conn.close()
    keys = ["id", "domain_name", "table_name", "raw_table", "column_name", "raw_column",
            "sensitivity", "aliases", "mapping_status", "note", "owner", "created_at", "updated_at"]
    return [dict(zip(keys, r)) for r in rows]


def upsert_sensitive_resource(domain_name, table_name, column_name, sensitivity="high",
                              aliases=None, note="", owner="", mapping_status="confirmed"):
    """
    新增/更新敏感资源映射。
    ★ mapping_status 必须显式传 'confirmed' 才会参与生效判定；
      AI 推荐候选应传 'pending'，管理员确认后再改回 confirmed。
    """
    table_name = (table_name or "").strip().lower()
    column_name = (column_name or "").strip().lower()
    if not table_name or not column_name:
        raise ValueError("表名与字段名不能为空")
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "INSERT INTO sensitive_resources "
        "(domain_name, table_name, raw_table, column_name, raw_column, sensitivity, "
        " aliases, mapping_status, note, owner, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(table_name, column_name) DO UPDATE SET "
        " domain_name=excluded.domain_name, sensitivity=excluded.sensitivity, "
        " aliases=excluded.aliases, mapping_status=excluded.mapping_status, "
        " note=excluded.note, owner=excluded.owner, updated_at=excluded.updated_at",
        (domain_name, table_name, table_name, column_name, column_name, sensitivity,
         _to_json(aliases or []), mapping_status, note, owner, now, now))
    conn.commit()
    conn.close()
    return {"ok": True}


def delete_sensitive_resource(rid):
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM sensitive_resources WHERE id = ?", (rid,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── 字段访问策略 ──
def get_active_policies():
    """生效中的权限策略（enabled=1）。供权限引擎读取。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, subject_type, subject_name, domain_name, table_name, column_name, "
        "action, access, mask_rule, priority, enabled, version, created_by, created_at, updated_at "
        "FROM permission_policies WHERE enabled = 1 ORDER BY priority DESC, id").fetchall()
    conn.close()
    keys = ["id", "subject_type", "subject_name", "domain_name", "table_name", "column_name",
            "action", "access", "mask_rule", "priority", "enabled", "version",
            "created_by", "created_at", "updated_at"]
    return [dict(zip(keys, r)) for r in rows]


def list_permission_policies(include_disabled=True):
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    sql = ("SELECT id, subject_type, subject_name, domain_name, table_name, column_name, "
           "action, access, mask_rule, priority, enabled, version, created_by, created_at, updated_at "
           "FROM permission_policies")
    if not include_disabled:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY priority DESC, id DESC"
    rows = cur.execute(sql).fetchall()
    conn.close()
    keys = ["id", "subject_type", "subject_name", "domain_name", "table_name", "column_name",
            "action", "access", "mask_rule", "priority", "enabled", "version",
            "created_by", "created_at", "updated_at"]
    return [dict(zip(keys, r)) for r in rows]


def upsert_permission_policy(subject_type, subject_name, table_name, column_name, access,
                             action="query", mask_rule=None, domain_name=None,
                             priority=0, enabled=1, created_by="", change_note=""):
    """
    新增/更新字段访问策略。返回 policy id + 版本。
    ★ 每次写入都会落一条版本记录（before/after 快照），供回滚与审计。
    """
    if subject_type not in ("role", "user"):
        raise ValueError("subject_type 只能是 role / user")
    if access not in ("ALLOW", "MASK", "DENY"):
        raise ValueError("access 只能是 ALLOW / MASK / DENY")
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    now = _now()

    # 找已存在的同键策略（同主体+表+列+动作），决定是新增还是升级版本
    row = cur.execute(
        "SELECT id, version FROM permission_policies WHERE subject_type=? AND subject_name=? "
        " AND table_name=? AND column_name=? AND action=?",
        (subject_type, subject_name, table_name, column_name, action)).fetchone()

    after = {"subject_type": subject_type, "subject_name": subject_name, "table_name": table_name,
             "column_name": column_name, "access": access, "action": action,
             "mask_rule": mask_rule, "domain_name": domain_name, "priority": priority, "enabled": enabled}

    if row:
        pid, old_ver = row
        new_ver = old_ver + 1
        before = {"access": "?", "version": old_ver}
        # 取旧值做 before 快照
        old = cur.execute(
            "SELECT access, mask_rule, enabled FROM permission_policies WHERE id=?", (pid,)).fetchone()
        before = {"access": old[0], "mask_rule": old[1], "enabled": old[2]} if old else {}
        cur.execute(
            "UPDATE permission_policies SET domain_name=?, access=?, mask_rule=?, priority=?, "
            "enabled=?, version=?, updated_at=?, created_by=? WHERE id=?",
            (domain_name, access, mask_rule, priority, 1 if enabled else 0, new_ver, now, created_by, pid))
    else:
        new_ver = 1
        before = {}
        cur.execute(
            "INSERT INTO permission_policies "
            "(subject_type, subject_name, domain_name, table_name, column_name, action, access, "
            " mask_rule, priority, enabled, version, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (subject_type, subject_name, domain_name, table_name, column_name, action, access,
             mask_rule, priority, 1 if enabled else 0, new_ver, created_by, now, now))
        pid = cur.lastrowid

    cur.execute(
        "INSERT INTO permission_policy_versions "
        "(policy_id, version, payload, status, change_note, before_json, after_json, operator, published_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, new_ver, _to_json_str(after), "published" if enabled else "disabled",
         change_note, _to_json_str(before), _to_json_str(after), created_by, now))
    conn.commit()
    conn.close()
    return {"ok": True, "id": pid, "version": new_ver}


def set_policy_enabled(pid, enabled, operator=""):
    """启停策略（保留版本记录）。"""
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    row = cur.execute("SELECT version FROM permission_policies WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"策略不存在：{pid}")
    new_ver = row[0] + 1
    now = _now()
    cur.execute("UPDATE permission_policies SET enabled=?, version=?, updated_at=? WHERE id=?",
                (1 if enabled else 0, new_ver, now, pid))
    cur.execute(
        "INSERT INTO permission_policy_versions "
        "(policy_id, version, payload, status, change_note, before_json, after_json, operator, published_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, new_ver, "{}", "published" if enabled else "disabled",
         "启用" if enabled else "停用", "{}", "{}", operator, now))
    conn.commit()
    conn.close()
    return {"ok": True, "id": pid, "version": new_ver}


def delete_policy(pid):
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM permission_policies WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}


def get_policy_versions(policy_id):
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, policy_id, version, payload, status, change_note, operator, published_at "
        "FROM permission_policy_versions WHERE policy_id=? ORDER BY version DESC",
        (policy_id,)).fetchall()
    conn.close()
    keys = ["id", "policy_id", "version", "payload", "status", "change_note", "operator", "published_at"]
    return [dict(zip(keys, r)) for r in rows]


# ── 权限审计 ──
def add_permission_audit(item):
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO permission_audit_logs "
        "(username, roles, request_id, ts, question, domain, tables, columns, action, decision, "
        " deny_reason, rule_ids, policy_version, rewritten, final_sql, returned_columns, "
        " exported_columns, ok, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (item.get("username", ""), _to_json(item.get("roles") or []),
         item.get("request_id", ""), _now(), item.get("question", ""),
         item.get("domain", ""), _to_json(item.get("tables") or []),
         _to_json(item.get("columns") or []), item.get("action", "query"),
         item.get("decision", ""), item.get("deny_reason", ""),
         _to_json(item.get("rule_ids") or []), item.get("policy_version"),
         1 if item.get("rewritten") else 0, item.get("final_sql", ""),
         _to_json(item.get("returned_columns") or []),
         _to_json(item.get("exported_columns") or []),
         1 if item.get("ok") else 0, _now()))
    conn.commit()
    conn.close()


def list_permission_audit(username=None, limit=200):
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    sql = ("SELECT id, username, roles, request_id, ts, question, domain, tables, columns, "
           "action, decision, deny_reason, rule_ids, policy_version, rewritten, final_sql, "
           "returned_columns, exported_columns, ok, created_at FROM permission_audit_logs")
    args = []
    if username:
        sql += " WHERE username = ?"
        args.append(username)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 1000)))
    rows = cur.execute(sql, args).fetchall()
    conn.close()
    keys = ["id", "username", "roles", "request_id", "ts", "question", "domain", "tables",
            "columns", "action", "decision", "deny_reason", "rule_ids", "policy_version",
            "rewritten", "final_sql", "returned_columns", "exported_columns", "ok", "created_at"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        for f in ("roles", "tables", "columns", "rule_ids", "returned_columns", "exported_columns"):
            d[f] = _from_json(d.get(f))
        out.append(d)
    return out


# ── AI 自然语言草稿 ──
def save_ai_draft(source_text, parsed_json, ambiguities, impact_json, created_by=""):
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "INSERT INTO ai_policy_drafts "
        "(source_text, parsed_json, ambiguities, impact_json, status, created_by, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (source_text, _to_json_str(parsed_json), _to_json_str(ambiguities),
         _to_json_str(impact_json), "pending", created_by, now))
    draft_id = cur.lastrowid
    conn.commit()
    conn.close()
    return draft_id


def list_ai_drafts(status=None):
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    sql = ("SELECT id, source_text, parsed_json, ambiguities, impact_json, status, "
           "created_by, created_at, reviewed_by, reviewed_at FROM ai_policy_drafts")
    args = []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    sql += " ORDER BY id DESC"
    rows = cur.execute(sql, args).fetchall()
    conn.close()
    keys = ["id", "source_text", "parsed_json", "ambiguities", "impact_json", "status",
            "created_by", "created_at", "reviewed_by", "reviewed_at"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["parsed"] = _from_json_str(d.get("parsed_json"))
        d["ambiguities"] = _from_json_str(d.get("ambiguities"))
        d["impact"] = _from_json_str(d.get("impact_json"))
        out.append(d)
    return out


def update_ai_draft_status(draft_id, status, reviewed_by="", reason=""):
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("UPDATE ai_policy_drafts SET status=?, reviewed_by=?, reviewed_at=? WHERE id=?",
                (status, reviewed_by, _now(), draft_id))
    if status in ("approved", "rejected"):
        cur.execute(
            "INSERT INTO permission_approvals (draft_id, target_type, requested_by, approved_by, status, reason, created_at, decided_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (draft_id, "draft", "", reviewed_by, status, reason, _now(), _now()))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── 行级权限 ──
def list_row_permission_rules():
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, subject_type, subject_name, table_name, filter_sql, description, enabled, "
        "created_by, created_at, updated_at FROM row_permission_rules ORDER BY id").fetchall()
    conn.close()
    keys = ["id", "subject_type", "subject_name", "table_name", "filter_sql",
            "description", "enabled", "created_by", "created_at", "updated_at"]
    return [dict(zip(keys, r)) for r in rows]


def upsert_row_permission_rule(subject_type, subject_name, table_name, filter_sql,
                               description="", enabled=1, created_by=""):
    if subject_type not in ("role", "user"):
        raise ValueError("subject_type 只能是 role / user")
    if not (filter_sql or "").strip():
        raise ValueError("行级过滤条件不能为空")
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "INSERT INTO row_permission_rules "
        "(subject_type, subject_name, table_name, filter_sql, description, enabled, created_by, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (subject_type, subject_name, table_name, filter_sql, description,
         1 if enabled else 0, created_by, now, now))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── JSON 工具（对象版，区别于列表版 _to_json/_from_json）──
def _to_json_str(obj):
    return json.dumps(obj, ensure_ascii=False)


def _from_json_str(text):
    try:
        return json.loads(text) if text else None
    except Exception:
        return None


# ── 用户已验证属性（行级权限用：dept_id / region 等，仅管理员可设置）──
def get_user_attributes(username):
    """取某用户的已验证身份属性（dict）。"""
    u = get_user(username)
    return (u or {}).get("user_attributes") or {}


def set_user_attributes(username, attrs):
    """
    设置用户的已验证身份属性（仅管理员接口调用）。
    ★ 属性是行级权限的信任来源，必须由管理员设置，绝不能由用户自己提交。
    """
    if not isinstance(attrs, dict):
        raise ValueError("属性必须是对象")
    _ensure_ready()
    conn = _connect()
    cur = conn.cursor()
    if not cur.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        conn.close()
        raise ValueError(f"账号不存在：{username}")
    cur.execute("UPDATE users SET user_attributes = ?, updated_at = ? WHERE username = ?",
                (_to_json_str(attrs), _now(), username))
    conn.commit()
    conn.close()
    return {"ok": True, "username": username}
