-- ═══════════════════════════════════════════════════════════════════════
-- ck_demo_setup.sql —— 在 ClickHouse 里建一套"和演示库数据完全一致"的沙箱
--
-- ★ 为什么要有这个文件？
--   你准备把 Agent 接到真实数仓，但直接连生产库试跑风险太大。
--   先在 ClickHouse 里建一个 demo 库试通，确认无误再切生产表。
--
-- ★ 使用方式（两种都行）
--   方式一：命令行
--       clickhouse-client --multiquery < ck_demo_setup.sql
--   方式二：HTTP（8123）
--       curl "http://你的CK:8123/" --data-binary @ck_demo_setup.sql
--
-- ★ 注意：本脚本会 TRUNCATE 这几张表，请确认 demo 库是测试库！
-- ═══════════════════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS demo;

-- ───────────────────────────────────────────────────────────────────────
-- ① 订单事实表
--    ★ 注意每个字段后面的 COMMENT —— 这就是 Agent 的知识来源。
--      ClickHouse 会把它们存进 system.columns.comment，
--      Agent 抓表结构时会自动带上，知识库"冷启动零成本"。
-- ───────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS demo.dwd_order_all
(
    order_id    Int64               COMMENT '订单编号',
    user_id     Int64               COMMENT '用户ID',
    driver_id   Int64               COMMENT '司机ID',
    city        String              COMMENT '城市',
    status      Int8                COMMENT '订单状态：1待接单 2已接单 3已支付 6已完成 7已取消',
    amount      Float64             COMMENT '订单金额',
    create_time DateTime            COMMENT '下单时间',
    finish_time Nullable(DateTime)  COMMENT '完单时间'
)
ENGINE = MergeTree
ORDER BY order_id
COMMENT '订单事实表：记录每笔订单的下单、接单、支付、完成、取消全生命周期与金额';

-- ───────────────────────────────────────────────────────────────────────
-- ② 用户维表
-- ───────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS demo.dim_user
(
    user_id    Int64     COMMENT '用户ID',
    user_name  String    COMMENT '用户姓名',
    reg_city   String    COMMENT '注册城市',
    reg_time   DateTime  COMMENT '注册时间',
    user_level Int8      COMMENT '会员等级：1普通 2银卡 3金卡'
)
ENGINE = MergeTree
ORDER BY user_id
COMMENT '用户维表：记录用户的注册信息、注册城市和会员等级';

-- ───────────────────────────────────────────────────────────────────────
-- ③ 司机维表
-- ───────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS demo.dim_driver
(
    driver_id    Int64     COMMENT '司机ID',
    driver_name  String    COMMENT '司机姓名',
    city         String    COMMENT '所属城市',
    reg_time     DateTime  COMMENT '注册时间',
    driver_level Int8      COMMENT '司机等级'
)
ENGINE = MergeTree
ORDER BY driver_id
COMMENT '司机维表：记录司机的所属城市、注册时间和司机等级';

-- ───────────────────────────────────────────────────────────────────────
-- ④ 退款事实表
-- ───────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS demo.dwd_refund
(
    refund_id   Int64     COMMENT '退款单号',
    order_id    Int64     COMMENT '关联订单ID',
    refund_amt  Float64   COMMENT '退款金额',
    refund_time DateTime  COMMENT '退款时间'
)
ENGINE = MergeTree
ORDER BY refund_id
COMMENT '退款事实表：记录每笔退款的关联订单、退款金额和退款时间';

-- ───────────────────────────────────────────────────────────────────────
-- ⑤ ★ 敏感表（手机号 + 身份证）
--    故意放在这里，用来验证权限控制是否真的生效。
--    真实环境里，这类表连 GRANT 都不该给 Agent 账号。
-- ───────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS demo.dim_user_contact
(
    user_id Int64   COMMENT '用户ID',
    phone   String  COMMENT '手机号',
    id_card String  COMMENT '身份证号'
)
ENGINE = MergeTree
ORDER BY user_id
COMMENT '用户敏感信息表：记录手机号、身份证号（★ 禁止 AI 访问）';


-- ═══════════════════════════════════════════════════════════════════════
-- 造数（和 SQLite 演示库完全一致，方便对比两边结果）
-- ═══════════════════════════════════════════════════════════════════════

TRUNCATE TABLE demo.dwd_order_all;
INSERT INTO demo.dwd_order_all (order_id, user_id, driver_id, city, status, amount, create_time, finish_time) VALUES
    (1,  101, 201, '北京', 6, 300.0, '2026-06-15 10:00:00', '2026-06-15 10:30:00'),
    (2,  102, 202, '上海', 6, 450.0, '2026-07-05 09:00:00', '2026-07-05 09:40:00'),
    (3,  103, 203, '北京', 7, 200.0, '2026-07-10 14:00:00', NULL),
    (4,  101, 201, '广州', 6, 180.0, '2026-07-15 11:00:00', '2026-07-15 11:20:00'),
    (5,  104, 204, '北京', 6, 520.0, '2026-07-20 16:00:00', '2026-07-20 16:50:00'),
    (6,  102, 202, '上海', 3, 260.0, '2026-07-22 08:00:00', NULL),
    (7,  105, 205, '深圳', 7, 310.0, '2026-08-01 19:00:00', NULL),
    (8,  103, 203, '北京', 6, 400.0, '2026-07-28 13:00:00', '2026-07-28 13:35:00'),
    (9,  106, 206, '上海', 2, 150.0, '2026-07-30 20:00:00', NULL),
    (10, 101, 201, '深圳', 6, 620.0, '2026-06-20 15:00:00', '2026-06-20 15:45:00');

TRUNCATE TABLE demo.dim_user;
INSERT INTO demo.dim_user (user_id, user_name, reg_city, reg_time, user_level) VALUES
    (101, '张三', '北京', '2026-05-01 00:00:00', 3),
    (102, '李四', '上海', '2026-06-10 00:00:00', 2),
    (103, '王五', '北京', '2026-07-03 00:00:00', 1),
    (104, '赵六', '广州', '2026-07-18 00:00:00', 1),
    (105, '孙七', '深圳', '2026-08-02 00:00:00', 1);

TRUNCATE TABLE demo.dim_driver;
INSERT INTO demo.dim_driver (driver_id, driver_name, city, reg_time, driver_level) VALUES
    (201, '王师傅', '北京', '2026-01-10 00:00:00', 3),
    (202, '李师傅', '上海', '2026-03-15 00:00:00', 2),
    (203, '张师傅', '北京', '2026-07-01 00:00:00', 1),
    (204, '刘师傅', '广州', '2026-07-12 00:00:00', 1);

TRUNCATE TABLE demo.dwd_refund;
INSERT INTO demo.dwd_refund (refund_id, order_id, refund_amt, refund_time) VALUES
    (1, 3, 200.0, '2026-07-12 10:00:00'),
    (2, 7, 310.0, '2026-08-03 10:00:00'),
    (3, 6, 260.0, '2026-07-25 10:00:00');

TRUNCATE TABLE demo.dim_user_contact;
INSERT INTO demo.dim_user_contact (user_id, phone, id_card) VALUES
    (101, '13800000001', '110101199001011234'),
    (102, '13800000002', '310101199002022345'),
    (103, '13800000003', '440101199003033456');


-- ═══════════════════════════════════════════════════════════════════════
-- ★ 只读账号（第一层防线，必须做）
--
-- 为什么必须做？因为代码层的护栏是"第二道"防线：
--     代码护栏拦的是"模型写错"      → 有 bug 就可能绕过
--     数据库权限拦的是"代码被绕过"   → 无法绕过
--   两者防的不是同一种风险。
--
-- ⚠️ 密码请在生产环境替换成强密码，并妥善保存。
-- ═══════════════════════════════════════════════════════════════════════

CREATE USER IF NOT EXISTS ai_reader IDENTIFIED WITH plaintext_password BY 'ChangeMe_ReadOnly_2026';

-- 逐表授权 —— 注意：故意【不】授 dim_user_contact
GRANT SELECT ON demo.dwd_order_all TO ai_reader;
GRANT SELECT ON demo.dim_user      TO ai_reader;
GRANT SELECT ON demo.dim_driver    TO ai_reader;
GRANT SELECT ON demo.dwd_refund    TO ai_reader;

-- 绝对不要这样做（全库授权 = 前功尽弃）
-- GRANT SELECT ON demo.* TO ai_reader;

-- 查询超时保护：防止一句自然语言拖垮集群
ALTER USER ai_reader SETTINGS max_execution_time = 30,
                            max_result_rows    = 10000;

-- ★ 额外一道保险：禁止读系统表（避免 AI 通过 system.* 探测到未授权的表名）
--   注意：如果 Agent 需要抓表结构（system.tables / system.columns），
--   就不能加下面这两条，否则知识库会抓不到任何表。
--   取舍：靠代码层的 ACL 白名单控制"AI 能看到哪些表"，这里放开 system 表只读。
-- REVOKE SELECT ON system.* FROM ai_reader;
