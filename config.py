# -*- coding: utf-8 -*-
"""
config.py —— 所有"可能变的东西"集中放在这里
★ 这是将来【在线配置平台】的雏形：阈值、方言、模型名，将来都要能在界面上改
★ 原则：代码里不出现魔法数字，一切从 config 读
"""
import os                            # 引入"操作系统工具箱"：用它读环境变量
from dotenv import load_dotenv       # 引入 .env 读取工具

load_dotenv()                        # 执行加载：把 .env 里的键值对变成环境变量


def _env_bool(name, default):
    """读布尔环境变量：1/true/yes/on 为真，其余为假；未设置时用默认值。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _default_acl_visible(app_env):
    """ACL 默认策略：生产环境默认【白名单】（false），开发/测试默认黑名单（true）。

    ★ 为什么按环境区分？
      生产环境"默认可见"意味着新增一张表就自动暴露给 AI；
      "默认拒绝"更安全，只是新表要显式授权。
      本地 demo 没有 ACL 数据，若默认拒绝会导致一张表都看不见，所以开发环境放宽。
    """
    return app_env != "production"


# ═══════════════════════════════════════════════════════════
# 大模型配置
# ═══════════════════════════════════════════════════════════
LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY")   # 钥匙：从环境变量取，绝不写死在代码里
LLM_BASE_URL = "https://api.deepseek.com"  # 服务器地址：决定请求发到哪家
LLM_MODEL = "deepseek-flash"                   # 模型名
LLM_TEMPERATURE = 0.0                         # 温度：取数场景必须为 0
#   温度 0   = 每次回答几乎一样（取数必选，同一问题必须得到同一 SQL）
#   温度 0.8 = 更有创意但每次不同（写文案用）

# ── SQL 生成 / 自愈修复调用的超时（秒）──
#   ★ 之前 SQL 生成那一次 llm.chat 不设 timeout，模型卡住会无限阻塞
#     （OpenAI SDK 默认超时长达 600s，实际不可接受）；现在生成与各处自愈修复统一用它。
LLM_GENERATE_TIMEOUT = int(os.getenv("LLM_GENERATE_TIMEOUT", "60"))


# ═══════════════════════════════════════════════════════════
# ★ 数据源选择（改这一行就切换数据库，代码零改动）
# ═══════════════════════════════════════════════════════════
DB_TYPE = os.getenv("DB_TYPE", "sqlite")
#   "sqlite"     → 用本地文件库（开发 / 演示，开箱即用）
#   "clickhouse" → 连公司真实数仓（生产）
#   ★ 建议写在 .env 里：DB_TYPE=clickhouse
#     这样代码和配置分离，不同环境用不同 .env 即可

# ── SQLite 配置（开发用）──
DB_PATH = "demo_shop.db"      # SQLite 数据库文件路径

# ── ClickHouse 配置（生产用）──
#   ★ 这些建议全部写在 .env 里，不要硬编码在代码里
CK_HOST = os.getenv("CK_HOST", "127.0.0.1")
CK_PORT = int(os.getenv("CK_PORT", "8123"))       # 8123 = HTTP 接口
CK_USER = os.getenv("CK_USER", "ai_reader")       # ★ 必须是【只读账号】
CK_PASSWORD = os.getenv("CK_PASSWORD", "")
CK_DATABASE = os.getenv("CK_DATABASE", "default")

# ★ 大查询保护（防止一句自然语言拖垮整个集群）
CK_MAX_EXECUTION_SEC = int(os.getenv("CK_MAX_EXECUTION_SEC", "30"))   # 服务端超时（秒）
CK_MAX_RESULT_ROWS = int(os.getenv("CK_MAX_RESULT_ROWS", "10000"))    # 服务端最多返回行数
MAX_RESULT_ROWS = int(os.getenv("MAX_RESULT_ROWS", "1000"))           # 应用层强制 LIMIT
# ★ 大查询内存上限（应用层统一给每个查询加 SETTINGS max_memory_usage）。
#   由系统统一控制，而不是让模型自己写 SETTINGS（模型写了会和 LIMIT 注入冲突→code 62）。
#   值略高于服务端默认上限，让合理的多表 JOIN 能跑过，又不至于无上限拖垮集群。
MAX_QUERY_MEMORY = int(os.getenv("MAX_QUERY_MEMORY", "12000000000")) # 约 11.2 GiB
# ★ 单次查询执行超时（秒）。应用层统一加到 SETTINGS max_execution_time，
#   覆盖 CK 只读账号默认的 30s 硬上限，让合理的复杂多表 JOIN 能跑完
#   （这种查询单条常需 30~90s）。仍是上限保护，不会无限拖。
MAX_QUERY_TIMEOUT = int(os.getenv("MAX_QUERY_TIMEOUT", "120"))

# ── 连接复用（★ 性能关键：不要关）──
#   实测（2026-10-30，本地 SSH 隧道）：新建连接 8.3 秒，复用连接查询 0.05 秒。
#   一次取数里 execute 阶段原来 5.8 秒，其中 SQL 本身只占 0.08 秒 ——
#   也就是说 **99% 的时间花在建连接上**。
#   开启复用后：每线程保持一条长连接，只有首次请求付连接成本。
#   ★ 断线（隧道重建）会自动重连并重试一次，见 db_clickhouse._run。
CK_CONN_REUSE = _env_bool("CK_CONN_REUSE", True)
#   连接空闲超过这个秒数就丢弃重建（防止拿到被中间设备掐死但对象还活着的连接）
CK_CONN_MAX_AGE = int(os.getenv("CK_CONN_MAX_AGE", "600"))

# ── 尾段并行（自然语言回答 ∥ 事后自审）──
#   这两步互不依赖，串行实测 1.9s + 5.0s；并行后取较大者，省掉约 1.9s。
#   ★ 返回结构完全不变，前端无需改动。置 0 可退回串行（排障用）。
TAIL_PARALLEL = _env_bool("TAIL_PARALLEL", True)

# ── 数据字典源表（★ 库里的"编码→中文"字典表）──
#   这张表里存着 dict_type / dict_value / dict_label，
#   比如 dict_type='app_entity_source' 就是"数据渠道"字典（101=车拖车 …）。
#   知识库页面可以按 dict_type 一键导入，不用手抄。
#   ★ 做成配置而不是写死：不同公司这张表名字不一样（有的叫 sys_dict_data、有的叫 dim_dict）。
DICT_TABLE = os.getenv("DICT_TABLE", "ods_sys_dict_data_all")

# ── SQL 方言：会写进 prompt，必须和实际连接的库一致！──
SQL_DIALECT = "ClickHouse" if DB_TYPE == "clickhouse" else "SQLite"
#   ★★★ 血泪教训：之前模型生成了 ClickHouse 的 countIf()，
#       但数据库是 SQLite，报错 "no such function: countIf"。
#       根因就是方言和实际库不一致。
#   ★ 现在改成"跟着 DB_TYPE 自动走"——从根上杜绝了这个错误。

# ── ClickHouse LEFT JOIN 未匹配记录的补值机制（join_use_nulls 配置）──
#   ClickHouse 的 join_use_nulls 决定 LEFT/RIGHT JOIN 未匹配的右表字段补什么：
#     0 = 补【字段类型的默认值】（UInt/Int→0，String→''，Date→默认日期）
#         → 此时用 IS NULL 判断"是否关联成功"会失效，必须用关联标记字段 / != 0。
#     1 = 补 NULL（标准 SQL 行为）→ IS NULL / IS NOT NULL 判断正常有效。
#   ★ 为什么默认 -1（未知）而不是 0？
#     任务要求"配置未知时不允许直接假设所有关联字段都补 0"——
#     宁可少拦一条，也不要把 join_use_nulls=1 的库误判成 0 导致误伤正确 SQL。
#   ★ 建议在 .env 里显式写 CK_JOIN_USE_NULLS=0 或 1（和真实库对齐）；
#     配置与真实库不一致时，规则检查会给出错误或漏掉的提示。
CK_JOIN_USE_NULLS = int(os.getenv("CK_JOIN_USE_NULLS", "-1"))


# ═══════════════════════════════════════════════════════════
# Embedding 配置（RAG 用）
# ═══════════════════════════════════════════════════════════
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"                 # 中文 embedding 模型（512 维）
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："   # BGE 官方要求：查询侧加这句指令
#   为什么查询侧要加、文档侧不加？
#   因为 BGE 要区分"这段文字是要去检索别人"还是"这段文字等着被检索"。
#   不加指令，相似度会被压缩在 0.4~0.8 的窄区间，分离度很差。

# ★ 模型缓存目录 —— 企业内网部署的关键配置
EMBED_CACHE_DIR = os.getenv("EMBED_CACHE_DIR", "models")
#   为什么不放在默认的临时目录？
#     ① 系统会定期清理临时目录 —— 模型被清掉后，程序下次启动会去联网重新下载，
#        而内网环境根本连不上，直接启动失败（这个坑我们踩过）
#     ② 放在项目里，模型可以跟代码一起打包部署（U 盘拷过去就能跑）
#   部署建议：先在能联网的机器上跑一次把 models/ 下好，
#             再把整个 models/ 目录一起拷到内网服务器。

EMBED_LOCAL_ONLY = os.getenv("EMBED_LOCAL_ONLY", "1") == "1"
#   1（默认）= 只用本地缓存，不联网。适合内网/生产环境（启动快，不受网络影响）
#   0         = 本地没有就去下载。适合第一次准备模型
#   ★ 注意：即使设成 1，程序也会在"本地没有模型"时报出清晰的提示，
#     告诉你该怎么办，而不是抛一堆看不懂的堆栈。


# ═══════════════════════════════════════════════════════════
# 检索阈值（★ 这几个数字是将来要持续调优的核心参数）
# ═══════════════════════════════════════════════════════════
TABLE_THRESHOLD = 0.45        # 表召回的最低相似度：低于它就认为"没找到相关表"
METRIC_THRESHOLD = 0.50       # 指标召回的最低相似度
TABLE_TOP_K = 5               # 最多召回几张表（防止 prompt 被撑爆；调到 5 以覆盖「多业务线联合查询」）
METRIC_TOP_K = 8              # 最多召回几个指标（多指标联合查询时一次带全）
# ★ 数据字典召回：阈值设得比表/指标都高
#   为什么？因为"多注入一个不相关的字典"比"少注入"更糟 ——
#   字典是一大串编码，注错了白占上下文，还可能诱导模型用错编码。
#   宁可漏（模型按常规写法处理），也不要错（拿错字典去套字段）。
DICT_THRESHOLD = 0.55         # 字典召回的最低相似度（更严）
DICT_TOP_K = 2                # 最多召回几个字典

# ★ 「无归属卫星表」的保留阈值（实体过滤专用）
#   【它解决什么问题？】
#     识别出实体主表后，P3 会把"不在主表∪指标依赖表里的表"当跨业务线丢掉。
#     但真实数仓里一个业务实体常带若干【卫星表】：代驾订单 → 代驾费用表、代驾司机激活费表、
#     询价记录表……它们不属于任何已注册实体，于是被误杀 ——
#     问「代驾司机缴纳激活费」就连激活费表都拿不到，直接"表结构不足"拒答。
#   规则：明确属于【别的实体】的表 → 照旧丢；查不到归属的表 → 只有相似度足够高才留。
#   取 0.60 的依据（实测四类问题的分数分布）：
#     该保留的卫星表（代驾激活费）0.6989 ；
#     纯噪声（询价记录 0.4820~0.5864、大板统计 0.4750~0.5550）最高只到 0.5864。
#     0.60 落在这个空隙里 —— 既救回该留的，又不把噪声放回来。
#   ⚠️ 这是启发式阈值（样本有限）。要调它就改这一个数字，不要散落到别处。
SATELLITE_KEEP_THRESHOLD = 0.60

# ── 「用户点名表」判定的最小连续重合字数 ──
#   用户问题若与某张表的【业务自述名】有 ≥ 这么多字连续重合，就认定"用户在点名这张表"，
#   该表不受实体过滤影响（实现见 rag.named_tables / semantic_layer.P3）。
#
#   ★ 为什么必须有这个东西：向量分数【分不开】该留的和噪声。
#     实测同一句「查询8月份资金流水表中…」：该留的资金流水表 0.5686，
#     纯噪声的大板统计表 0.5449 —— 只差 0.024，任何阈值都切不开。
#     而"用户明确写出的词"是比分数更强的证据：「资金流水」与
#     「ods_tu_record_all：资金流水记录明细」有 4 字连续重合。
#   ★ 为什么是 4：实测 3 会把「板订单」误命中「大板订单主表」
#     （用户问"小板"却把大板救回来 —— 正是实体过滤要防的跨业务线泄漏）；
#     4 时干净，且「资金流水」恰为 4 字。
NAMED_TABLE_MIN_CHARS = 4

# ⚠️ 已知隐患：这几个阈值是拍脑袋定的，安全区间很窄（约 0.40~0.44）。
#    将来知识库扩容后必须重跑测试集重新校准 —— 这就是【评测平台】存在的理由。


# ═══════════════════════════════════════════════════════════
# 执行配置
# ═══════════════════════════════════════════════════════════
MAX_RETRY = 3                 # SQL 执行失败最多重试几次（防止死循环 + 防止烧钱）
MAX_ROWS_PRINT = 10           # 结果最多打印几行（防止刷屏）

# ── 运行环境标识（用于生产环境保护）──
#   production（默认）：启用所有 fail-closed 策略；禁止 always 降级。
#   dev / test：允许正则兜底、允许 always（便于本地调试）。
APP_ENV = os.getenv("APP_ENV", "production").strip().lower()

# ── 表名解析降级策略（安全关键）──
#   True（默认）= 解析器不可用/解析失败时，即使正则抓到了表，也拒绝执行。
#                 原因：正则不认反引号、双引号、表函数，漏一张就是放行一张。
#   False       = 允许正则兜底结果（仅建议本地/测试环境使用）。
TABLE_PARSE_STRICT = os.getenv("TABLE_PARSE_STRICT", "1") == "1"

# ═══════════════════════════════════════════════════════════
# SQL 逻辑自审层（生成 SQL 后、返回前，额外调一次 LLM 做字段级逻辑诊断）
#   ★ 仅诊断、不改 SQL、不重跑（见 sql_rules / agent._review_sql）
#   ★ 会增加一次 LLM 调用延迟（约 3~6 秒）；想关掉在 .env 写 SQL_REVIEW_ENABLED=false
# ═══════════════════════════════════════════════════════════
SQL_REVIEW_ENABLED = os.getenv("SQL_REVIEW_ENABLED", "true").strip().lower() \
    in ("1", "true", "yes", "on")

# ★ 分层自审：按风险等级决定是否/如何调 LLM 逻辑自审（见 agent._sql_risk_level / _review_sql）。
#   LOW    → 跳过 LLM 自审（规则校验已覆盖，省 14s 级无谓调用）
#   MEDIUM → 轻量自审（只出整体结论 + 风险点，短输出 + 短超时）
#   HIGH   → 深度自审（逐字段诊断，长输出 + 较长超时；仅少数高风险查询付出此成本）
SQL_REVIEW_TIMEOUT_MEDIUM = int(os.getenv("SQL_REVIEW_TIMEOUT_MEDIUM", "10"))
SQL_REVIEW_TIMEOUT_HIGH = int(os.getenv("SQL_REVIEW_TIMEOUT_HIGH", "20"))

# ★ 自动定点修复上限：生成 SQL 后最多允许几次 LLM 自愈（防无限循环 + 省 token）。
#   任务书要求「最多一次自动定点修复」；修复后仍不达标则拒绝执行。
MAX_AUTO_FIX = int(os.getenv("MAX_AUTO_FIX", "1"))


# ═══════════════════════════════════════════════════════════
# ★ 需求一致性审核（第六维审核，见 requirement_review.py）
#   解决的问题：现有五维审核只判断"SQL 合不合法"，不判断"SQL 合不合题意"，
#   于是"替换写成追加""去掉分组顺手删过滤""漏 GROUP BY"都能静默通过。
#
#   ★ 它与 SQL_REVIEW_ENABLED 的区别：
#     SQL_REVIEW_ENABLED 是【执行之后】的逐字段逻辑诊断（只诊断、不改、不拦）；
#     本开关是【执行之前】的闸门之一（可阻断、可回灌模型重生成）。
# ═══════════════════════════════════════════════════════════
REQ_REVIEW_ENABLED = _env_bool("REQ_REVIEW_ENABLED", True)

#   触发范围：
#     always      = 每次取数都审（多一次 LLM 调用，3~6 秒）
#     modify_only = 只审「增量修改 / 含修改动词 / 有歧义」的场景（默认，成本可控）
#     off         = 完全不审（等价于改造前）
REQ_REVIEW_MODE = os.getenv("REQ_REVIEW_MODE", "modify_only").strip().lower()

#   是否启用语义层（P2 才有实现；P0 只有确定性规则，置 1 也是空转）。
#   置 0 = 只跑确定性规则（零 LLM 成本，可单测，适合灰度期先看误报率）。
REQ_REVIEW_LLM = _env_bool("REQ_REVIEW_LLM", True)

#   是否阻断执行。
#   ★ 默认 False（只告警不阻断）：P0/P1 灰度期先观察 fail 样本准不准，
#     误拦率（false_block_rate）< 5% 之后再打开。
#     打开后：需求审核 fail → 回灌模型修正 → 重审；重试耗尽仍 fail → 拒绝执行。
REQ_REVIEW_BLOCK = _env_bool("REQ_REVIEW_BLOCK", False)

#   语义层单次超时（秒）。超时一律记 skipped，绝不当成 pass。
REQ_REVIEW_TIMEOUT = int(os.getenv("REQ_REVIEW_TIMEOUT", "8"))

#   需求审核不通过时，是否把这条 SQL 沉淀为失败经验（failure_type=requirement_mismatch）。
#   ★ 只在"确定没满足需求且已拒绝执行"时写，灰度期（BLOCK=0）不写 ——
#     那时 fail 里混着误报，写进去会污染经验库。
REQ_FAIL_RECORD = _env_bool("REQ_FAIL_RECORD", True)

#   增量修改场景的【经验检索】是否带上上一轮问题一起做 embedding。
#   ★ 为什么默认开：本轮问句往往是"再加完成率"这种 5 个字，
#     单靠它做向量检索几乎召不回真正的基线案例，等于每轮从零参考。
EXP_MODIFY_CTX = _env_bool("EXP_MODIFY_CTX", True)


# ═══════════════════════════════════════════════════════════
# ★ 知识治理：生产检索是否严格只认 approved
# ═══════════════════════════════════════════════════════════
#   已按《数据治理方案》完成一轮全量复核（2026-10-27）：
#     candidate 145 条 → approved 142（含 2 条就地修正）、rejected 3 条，
#     六层知识已无 candidate，故默认开启严格模式，召回无实质损失。
#
#   true （默认）：只有 approved 可进生产检索；AI 新抽取的知识先落 candidate，
#                  必须经复核（`python _review_governance.py apply`）才生效。
#                  这是治理方案要求的合规态。
#   false        ：放宽——candidate 也进检索，仅用于"刚迁移完、知识还没复核"
#                  的过渡期，或线上召回突然变少、需要临时止血的场景。
#   ★ 改动前后都建议跑 `python _governance_impact.py` 看影响面。
GOVERNANCE_STRICT_STATUS = _env_bool("GOVERNANCE_STRICT_STATUS", True)


# ═══════════════════════════════════════════════════════════
# ★ 表访问控制策略
# ═══════════════════════════════════════════════════════════
ACL_DEFAULT_VISIBLE = _env_bool(
    "ACL_DEFAULT_VISIBLE", _default_acl_visible(APP_ENV))
#   ★ 从 .env 读；没配时按环境取默认：
#       production → False（白名单，默认拒绝，新增表不会自动暴露）
#       dev/test   → True （黑名单，方便本地 demo 直接跑）
#   True  = 黑名单模式：表没在名单里 → 默认【可见】，只有显式标 0 才禁止
#           → 好用，适合刚上线（只需屏蔽少数敏感表）
#   False = 白名单模式：表没在名单里 → 默认【不可见】，必须显式标 1 才允许
#           → 更安全，适合成熟系统（默认拒绝，逐个授权）
#
# ★ 企业实践：先黑名单（快速跑通），稳定后切白名单（默认拒绝）。
#   切换时只需把这一行改成 False，然后给允许的表逐个 set_table_acl(name, True)。
#
# ★ 新表刚开放访问时，自动扫字段清单起草一句表业务注释（草稿，不注入模型）。
#   为什么默认开？表注释决定"模型能不能找对表"，但写它得通读几百个字段，
#   人总是拖到最后才补 —— 让机器先起草，人只需判断措辞对不对。
#   它会调一次大模型（约 2 秒/表），量大或想省钱时可设为 0 关掉。
AUTO_TABLE_DOC = os.getenv("AUTO_TABLE_DOC", "1") == "1"

# ★ 但请记住：这只是【第二层】防线（控制 AI 看见什么）。
#   真正的硬约束是：
#     第一层 —— 数据库账号只读 + 表级授权（改不了）
#     第四层 —— guard.check_tables 校验生成的 SQL（拦模型硬写）


# ═══════════════════════════════════════════════════════════
# ★ SQL 经验中心（经验沉淀 / 检索复用 / 归纳提炼）
# ═══════════════════════════════════════════════════════════
#   设计原则：**默认只做「参考」，不做「代替」**。
#   经验检索只往 Prompt 里加一段带可信度标注的参考，
#   SQL 仍由现有链路生成、现有闸门校验 —— 这样出问题最多"没用上经验"，
#   不会"用了错经验直接给你一条错 SQL"。
#
#   EXP_ENABLED=0 是一键回滚开关：所有经验逻辑短路，行为回到接入前。
EXP_ENABLED = _env_bool("EXP_ENABLED", True)

# 成功案例优先检索（把已发布/候选的黄金 SQL 作为 Prompt 参考）
EXP_SUCCESS_RECALL = _env_bool("EXP_SUCCESS_RECALL", True)
# 失败经验预防（Prompt 提示 + 生成后确定性检查）
EXP_FAIL_PREVENT = _env_bool("EXP_FAIL_PREVENT", True)
# 命中失败模式时是否自动修一轮（默认关：多一次 LLM 调用，且会改变现有重试行为；
#   评测 C/D 组会打开它来量化收益）
EXP_FAIL_REPAIR = _env_bool("EXP_FAIL_REPAIR", False)
# 归纳规则/模板参与检索（只有 status=approved 的规则会进来）
EXP_RULES = _env_bool("EXP_RULES", True)
# 精确复用（问题完全相同 + 正式黄金 SQL + 通过全部闸门 → 直接执行）
#   ★ 默认关：这是唯一一条「不经过模型」的路径，必须先有评测数据支撑再开。
EXP_EXACT_REUSE = _env_bool("EXP_EXACT_REUSE", False)

# 注入条数上限（控制 token 成本；无匹配时零开销）
EXP_MAX_CASES = int(os.getenv("EXP_MAX_CASES", "2"))
EXP_MAX_FAILURES = int(os.getenv("EXP_MAX_FAILURES", "3"))
EXP_MAX_RULES = int(os.getenv("EXP_MAX_RULES", "2"))

# 分数阈值（低于阈值不注入）
EXP_MIN_SCORE = float(os.getenv("EXP_MIN_SCORE", "0.55"))
EXP_FAIL_MIN_SCORE = float(os.getenv("EXP_FAIL_MIN_SCORE", "0.50"))
EXP_RULE_MIN_SCORE = float(os.getenv("EXP_RULE_MIN_SCORE", "0.55"))

# ── 语义模型接入（知识中心整合 · P2）──
# ★ 语义模型是「一个业务主题 = 一个模型」的审核通过的结构化语义资产。
#   命中时作为【权威约束】注入 Prompt（优先级高于经验参考）；
#   未命中时零开销、零行为变化。
# ★ 默认关闭（灰度）：先评测接入前后的问数效果，再决定是否默认开启。
#   SEMANTIC_MODEL_ENABLED=0 是一键回滚开关：所有语义模型逻辑短路，行为回到接入前。
SEMANTIC_MODEL_ENABLED = _env_bool("SEMANTIC_MODEL_ENABLED", False)
# 注入模型数上限（控制 token 成本；匹配不到时零开销）
SEMANTIC_MODEL_MAX = int(os.getenv("SEMANTIC_MODEL_MAX", "2"))


# 排序权重（★ 这是**初始启发式**，最终值要靠 evaluation/experiments/experience 的
#   四组对照实验来定。权重之和为 1，便于解释：分数 = Σ(权重 × 分项)）
#     sim      语义相似度         —— 问题像不像
#     table    表集合重合度       —— 是不是同一条业务线
#     metric   指标口径重合度     —— 口径是否一致（最影响正确性）
#     trust    可信等级           —— 是否正式黄金 SQL
#     verify   结果验证等级       —— 有没有真的验证过
#     recency  新鲜度             —— 表结构/口径会变
#   ⚠️ 调权重请同步跑一遍评测，不要凭感觉改。
EXP_RANK_WEIGHTS = {
    "sim":     float(os.getenv("EXP_W_SIM", "0.45")),
    "table":   float(os.getenv("EXP_W_TABLE", "0.15")),
    "metric":  float(os.getenv("EXP_W_METRIC", "0.20")),
    "trust":   float(os.getenv("EXP_W_TRUST", "0.12")),
    "verify":  float(os.getenv("EXP_W_VERIFY", "0.05")),
    "recency": float(os.getenv("EXP_W_RECENCY", "0.03")),
}


# ═══════════════════════════════════════════════════════════
# 取数结果缓存（result_cache.py）
# ═══════════════════════════════════════════════════════════
# ★ 为什么默认开 120 秒？
#   一次取数 20~35 秒 + 一次 LLM 计费，而日报/周报类问题高度重复。
#   120 秒足以吃掉"刷新页面""两个人前后脚问同一句"这类重复，
#   又短到不会让用户觉得"系统在骗我"（数仓本身通常就是 T+1）。
#
# ★ 缓存 key 已包含：问题 + 用户 + 角色 + 可见表指纹 + 知识指纹 + 方言 + 模型 + 经验开关。
#   这意味着：改口径 / 改权限 / 换模型 / 切经验开关，缓存都会自动失效。
#
# ★ 想彻底关掉就设 RESULT_CACHE_TTL=0（评测、排障、对账时建议关）。
RESULT_CACHE_TTL = int(os.getenv("RESULT_CACHE_TTL", "120"))
RESULT_CACHE_MAX = int(os.getenv("RESULT_CACHE_MAX", "200"))
# 知识指纹自身的缓存时间（秒）—— 太长会让"改了知识但缓存没失效"的窗口变大
RESULT_CACHE_KFP_TTL = int(os.getenv("RESULT_CACHE_KFP_TTL", "30"))


# ═══════════════════════════════════════════════════════════
# Prompt 落盘（排障用）
# ═══════════════════════════════════════════════════════════
#   PROMPT_DUMP=1 时，每次取数把最终 system prompt 写成
#   logs/prompt/<时间>-<问题哈希>.txt，可逐段查看、可 diff 两次改动。
#   默认关闭：一次取数一个文件，长期开会把磁盘写满。
PROMPT_DUMP = _env_bool("PROMPT_DUMP", False)
PROMPT_DUMP_DIR = os.getenv("PROMPT_DUMP_DIR", "logs/prompt")

