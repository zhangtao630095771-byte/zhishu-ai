# -*- coding: utf-8 -*-
"""
server.py —— FastAPI 后端（Web API 层）

★ 定位：把已有的 Agent 能力"包一层 HTTP 接口"，让浏览器能调用
★ 它自己【不做任何业务逻辑】—— 所有逻辑都在 agent.py / rag.py 里

架构：
    浏览器  →  server.py（路由、参数校验、JSON 序列化）  →  agent.py  →  各模块

启动方式：
    .venv/bin/python -m uvicorn server:app --reload --port 8000
    然后浏览器打开 http://127.0.0.1:8000
"""
import os
import csv                                        # 导出 CSV（标准库，无额外依赖）
import io                                         # 内存文件流（导出 Excel/CSV 用）
import asyncio                                      # 让同步阻塞的取数流程不卡住事件循环
import queue                                      # 后台起草表注释的任务队列
import secrets                                    # 生成请求级 request_id（整改任务7）
import threading                                  # 后台线程（不阻塞授权响应）
import time                                       # 缺口扫描结果的缓存时间戳
import math                                         # 处理 nan/inf（JSON 不允许序列化）
from urllib.parse import quote                    # 中文文件名做 RFC 5987 编码
from fastapi import FastAPI, HTTPException, Request   # Web 框架（Request 用于读取登录 token）
from fastapi.responses import FileResponse, Response   # 返回 HTML 文件 / 导出文件
from pydantic import BaseModel                  # 定义请求体的数据结构

import config          # 配置
import colname         # 列名 → 中文（计算列兜底 + 同名列消歧）
import config_store    # 配置存储（Prompt / 指标 / 表注释 / 权限）
import db              # 数据源
import enum_parse      # 从字段注释里解析「编码 → 含义」（结果表自动翻中文）
import gaps            # 知识缺口扫描（主动发现该补哪些字段）
import guard           # 护栏 + SQL 解析（提取表名 / 格式化）
import knowledge       # 业务知识访问入口（表清单等）
import rag             # 检索
import suggest         # 根据反馈生成优化建议（把"踩坑"翻译成"该改哪条知识"）
import agent           # 编排（核心流程）
import exporter        # 知识库导出（JSON / Markdown）
import ai_ingest       # AI 自动整合录入知识库（输入口径逻辑 → AI 分类 → 自动落库）
import join_suggest    # ★ JOIN 关联字段推荐（选表 → 推荐关联字段 → 自动拼 join_sql）
import permission      # ★ 字段级权限引擎（ALLOW/MASK/DENY + 审计）

from fastapi.responses import JSONResponse
from clickhouse_connect.driver.exceptions import OperationalError as _CHOpsErr
from urllib3.exceptions import MaxRetryError as _MaxRetryErr


# ═══════════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════════
app = FastAPI(title="知数AI", version="1.0")


# ★ 全局异常兜底：数据库（ClickHouse）连不上时，给个能看懂的提示
#   之前隧道一掉，接口就抛一大段 traceback + 裸 500，前端只显示"Internal Server Error"，
#   用户完全不知道发生了什么、该干嘛。把这类连接错误转成 503 + 一句人话，
#   并提示去拉隧道 —— 这正是日常用这个工具最容易踩的环境坑。
@app.exception_handler(_CHOpsErr)
@app.exception_handler(_MaxRetryErr)
async def _db_unavailable(request, exc):
    return JSONResponse(
        status_code=503,
        content={"error": "数据库未连接",
                 "detail": "ClickHouse 连接失败（隧道可能已断开）。请运行 ./tunnel.sh start 重新建立隧道后重试。",
                 "type": "db_unavailable"},
    )


# ═══════════════════════════════════════════════════════════
# ★ 权限控制（RBAC）—— 管理员 / 普通用户
# ═══════════════════════════════════════════════════════════
#  【为什么放在"中间件"而不是逐个接口加装饰器？】
#    这个后端有 60+ 个接口。逐个加装饰器，一是容易漏（漏一个就是越权漏洞），
#    二是以后新增接口很容易忘了加。中间件是"统一闸门"：
#    请求进来先过一道策略表，按「路径 + 方法」决定要什么角色 ——
#    新增接口默认落到 admin（最严格），忘了也不会出安全问题。
#
#  【三级策略】
#    public：登录页、静态页、登录/登出/当前用户 —— 不需要身份
#    user  ：取数、系统状态、提交反馈 —— 登录即可（普通用户的核心能力）
#    admin ：知识库 / Prompt / 表权限 / 账号 / 处理反馈 —— 只有管理员
#
#  【为什么身份不看前端传来的 role？】
#    前端可被篡改。前端只持有随机 token，真实身份每次到服务器查会话表 ——
#    这样"停用账号 / 降权"能立即生效，且普通用户改 JS 也变不成管理员。
# ═══════════════════════════════════════════════════════════
_PUBLIC_PATHS = {
    "/", "/favicon.ico", "/logo.png", "/logo-mark.png", "/api/ping",
    "/api/auth/login", "/api/auth/me", "/api/auth/logout",
}

# ★ 强制改密期间仍允许访问的接口：只放"看自己是谁"和"改自己密码"。
_MUST_CHANGE_ALLOWED = {"/api/auth/me", "/api/auth/logout", "/api/me/password"}


def _required_role(method: str, path: str) -> str:
    """按「方法 + 路径」判断所需角色。默认 admin（新增接口自动受保护）"""
    m = (method or "GET").upper()
    if path in _PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/openapi"):
        return "public"
    if path in ("/api/ask", "/api/status", "/api/me/password", "/api/permission/my-access"):
        return "user"
    # 提交反馈是普通用户的核心能力；查看/处理反馈只有管理员
    if path == "/api/feedback" and m == "POST":
        return "user"
    # ★ 取数历史 / 单条记录 / 审核状态查询：登录即可。
    #   注意这里【只放到"登录"这一级】—— 具体能不能看别人的记录、能不能改审核结论，
    #   交给接口内部按角色判断（能看谁的历史、谁能催、谁能审，逻辑不同，
    #   在中间件里用路径前缀表达不了，硬塞会写出难维护的规则）。
    if path.startswith("/api/history") or path.startswith("/api/records"):
        return "user"
    # 收藏是个人能力：登录即可，但只能操作自己的收藏
    if path.startswith("/api/favorites"):
        return "user"
    # 待审计数角标：管理员看全公司待办，普通用户看自己提交的，各自不越界
    if path == "/api/review/summary":
        return "user"
    return "admin"


def _bearer_token(request: Request):
    """从 Authorization: Bearer xxx 里取登录 token"""
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    path = request.url.path
    token = _bearer_token(request)
    user = config_store.get_session_user(token) if token else None
    request.state.user = user                      # 供各接口读取当前用户

    # ★ 整改任务7：为每个请求生成唯一 request_id（16 位 hex）。
    #   此前 _audit_permission 读取 request.state.request_id，但这里从未设置，
    #   导致权限审计的 request_id 恒为空、追踪链断裂（test_audit_safety.py 复现）。
    #   公开路径（/api/ping 等）也生成——异常/401 请求同样需要可追踪。
    if not getattr(request.state, "request_id", ""):
        request.state.request_id = secrets.token_hex(8)

    need = _required_role(request.method, path)
    if need == "public":
        return await call_next(request)
    if user is None:
        return JSONResponse(status_code=401, content={
            "error": "未登录或登录已过期", "type": "unauthorized",
            "detail": "请重新登录后再操作。",
        })
    # ★ 首次登录 / 管理员重置密码后：只允许改密，不允许继续用业务接口。
    #   这是"默认口令"风险的最后一道闸门 —— 光标记不够，必须真的拦住。
    if user.get("must_change_password") and path not in _MUST_CHANGE_ALLOWED:
        return JSONResponse(status_code=403, content={
            "error": "需要先修改密码", "type": "must_change_password",
            "detail": "当前账号仍使用初始/重置密码，请先修改密码后再使用。",
        })
    if need == "admin" and user.get("role") != "admin":
        return JSONResponse(status_code=403, content={
            "error": "无权限", "type": "forbidden",
            "detail": "该操作仅管理员可用（知识库 / Prompt / 表权限 / 账号管理 / 处理反馈）。",
        })
    return await call_next(request)



# ═══════════════════════════════════════════════════════════
# 索引缓存
# ★ 为什么用"懒加载 + 缓存"而不是启动时建？
#   因为 startup 事件在不同 FastAPI 版本里写法不同（on_event 已废弃），
#   懒加载更兼容；而且能保证"第一次请求时一定建好"。
# ═══════════════════════════════════════════════════════════
_INDEX = {"data": None}


def get_index():
    """取索引；没有就建一次（之后一直复用）"""
    if _INDEX["data"] is None:
        if config.DB_TYPE == "clickhouse":
            import ssh_tunnel
            ssh_tunnel.ensure_tunnel()   # 连真实库时，首次请求前确保 SSH 隧道已拉起
        db.create_demo_db()                        # 确保演示库存在（真实库无副作用）
        _INDEX["data"] = rag.build_index()         # 建索引（会打印进度）
    return _INDEX["data"]


def _ensure_db():
    """★ 访问真实库前先确保隧道通 —— 掉线了自动拉起，避免前端只看到 503。

    隧道是环境层的易碎点（沙箱/网络一波动就断），之前只要断了，所有库相关接口
    全抛 500。这里在入口处自愈：平时 _port_up 直接返回（仅一次 socket 探测，开销极小），
    只有端口真的不通才调 tunnel.sh 重建。一次 DB 查询不该因为隧道掉线就失败。
    """
    if config.DB_TYPE == "clickhouse":
        try:
            import ssh_tunnel
            ssh_tunnel.ensure_tunnel(verbose=False)
        except Exception:
            pass                            # 拉起失败不拖垮主流程，交给下游的 503 兜底


def rebuild_index():
    """★ 重建索引 —— 改完 Prompt / 指标 / 表注释 / 权限后必须调这个"""
    _INDEX["data"] = rag.build_index()
    return _INDEX["data"]


# ═══════════════════════════════════════════════════════════
# 工具：把结果行转成 JSON 能序列化的形式
# ═══════════════════════════════════════════════════════════
def _jsonable(rows):
    """
    数据库返回的值可能是 date / Decimal / numpy 类型，这些 JSON 不认识。
    ★ 处理原则：基础类型原样保留，其它一律转字符串。
      （前端只要显示，不需要再做数学运算，转字符串最省事）
    """
    out = []
    for row in rows:
        new_row = []
        for v in row:
            if v is None or isinstance(v, (int, float, str, bool)):
                new_row.append(v)                  # 基础类型，直接用
            else:
                new_row.append(str(v))             # date/Decimal 等 → 字符串
        out.append(new_row)
    return out


def _clean_nan(obj):
    """
    递归把 float 的 nan / inf 转成 None。
    ★ 原因：Python 标准 json.dumps 不允许序列化 nan/inf（会抛
      'Out of range float values are not JSON compliant'），
      而 SQL 结果里的除零 / 空聚合、检索打分都可能产生 nan。
      转成 None 后前端显示为 null，不会让整个接口 500。
    """
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_nan(v) for v in obj]
    return obj


# ═══════════════════════════════════════════════════════════
# 请求体模型（Pydantic 会自动校验类型，省去手写判断）
# ═══════════════════════════════════════════════════════════
class AskBody(BaseModel):
    question: str                                   # 用户问题

class ContentBody(BaseModel):
    content: str                                    # 配置内容

class MetricBody(BaseModel):
    keywords: list[str] = []                        # 关键词列表
    definition: str = ""                            # 业务口径
    doc: str = ""                                   # 富文本描述
    tables: list[str] = []                          # 依赖的表

class MetricEnabledBody(BaseModel):
    enabled: bool                                   # 是否开放该指标给 AI

class TableBody(BaseModel):
    comment: str                                    # 表业务注释

class AclBody(BaseModel):
    visible: bool                                   # 是否允许 AI 访问
    note: str = ""                                  # 备注

class KbCommentBody(BaseModel):
    comment: str = ""                               # 业务注释（空 = 清除）

class DictBody(BaseModel):
    dict_type: str = ""                             # 来源字典类型（便于溯源/重新导入）
    field_hint: str = ""                            # ★ 适用字段（告诉模型这个字典用在哪）
    keywords: list[str] = []                        # 检索关键词
    items: list[dict] = []                          # [{"value": "101", "label": "车拖车"}, ...]

class DictEnabledBody(BaseModel):
    enabled: bool                                   # 是否开放该字典给 AI

class DictImportBody(BaseModel):
    dict_type: str                                  # 要从数据库导入哪个字典类型
    name: str = ""                                  # 字典名称（留空则用 dict_type）
    field_hint: str = ""                            # 适用字段
    keywords: list[str] = []                        # 检索关键词


class TableImportBody(BaseModel):
    table: str                                      # 业务表名（如 ods_tu_record_type_all）
    value_col: str                                  # 编码列（如 id）
    label_col: str = ""                             # 可选：中文含义列（如 type_name）
    name: str = ""                                  # 字典名称（留空则用 表.列）
    field_hint: str = ""                            # 适用字段
    keywords: list[str] = []                        # 检索关键词
    limit: int = 1000                               # 最多导入多少条去重取值


class EntityBody(BaseModel):
    table_name: str = ""                            # 落地主表
    grain: str = ""                                 # 粒度：一行=一个什么
    comment: str = ""                               # 业务说明
    keywords: list[str] = []                        # 中文别名/同义词
    enabled: bool = True                            # 是否启用
    old_name: str = ""                              # ★ 编辑改名时传【原实体名】（用于删旧行）


class RelationshipBody(BaseModel):
    from_entity: str = ""                           # 左实体（别名）
    to_entity: str = ""                             # 右实体（别名）
    join_sql: str = ""                              # 标准 JOIN 片段
    join_type: str = "LEFT"                         # LEFT / INNER
    cardinality: str = "1:1"                        # 1:1 / 1:N（1:N 建议 pre_agg_required）
    pre_agg_required: bool = False                  # 右表是否需先 GROUP BY 预聚合
    rule_note: str = ""                             # 业务说明
    enabled: bool = True
    old_id: int | None = None                       # ★ 编辑时传原记录 id（改了左右实体时删旧行）


class KbDocBody(BaseModel):
    title: str = ""                                 # 标题
    content: str = ""                               # 正文
    keywords: list[str] = []                        # 检索关键词
    priority: int = 4                               # 优先级（默认 P4）
    enabled: bool = True
    id: int | None = None                           # ★ 编辑时传 id → 就地更新（支持改标题）


class IngestBody(BaseModel):
    text: str                                       # 用户输入的口径逻辑描述


class SqlIngestBody(BaseModel):
    """★ SQL 投喂：管理员粘贴「取数问题 + 标准 SQL」"""
    question: str                                   # 取数问题（自然语言）
    sql: str                                        # 标准 SQL（正确答案）
    note: str = ""                                  # 可选补充说明


class SuggestJoinBody(BaseModel):
    """★ JOIN 关联字段推荐：左右填【表名或实体名】均可"""
    left: str                                       # 左表/左实体
    right: str                                      # 右表/右实体
    hint: str = ""                                  # 可选补充说明（如"按订单号关联"）


class IngestItemBody(BaseModel):
    type: str                                       # metric/entity/relationship/column_comment/dict/doc
    data: dict = {}                                 # 编辑后重新落库的数据


# ═══════════════════════════════════════════════════════════
# ① 首页：返回前端页面
# ═══════════════════════════════════════════════════════════
@app.get("/")
def index_page():
    path = os.path.join(os.path.dirname(__file__), "frontend", "index.html")
    #      __file__ 是当前文件的路径，dirname 取目录，再拼上 frontend/index.html
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="前端文件不存在：frontend/index.html")
    return FileResponse(path)                       # 把 HTML 文件返回给浏览器


# ═══════════════════════════════════════════════════════════
# ①-a 系统图标 / 品牌标记
# ═══════════════════════════════════════════════════════════
#  【为什么要有这个路由？】
#    前端是"单文件零构建"，但图片没法内联进 HTML 里（favicon 必须是 URL）。
#    所以把裁剪好的图标放在 frontend/ 下，由这里返回：
#      /logo.png       —— 正方形图标（浏览器标签页 favicon / 添加到主屏）
#      /logo-mark.png  —— 紧凑横向标记（侧栏 / 登录页，去掉了上下留白）
#    ★ 它们在 _PUBLIC_PATHS 里 —— 登录页还没登录就得能显示。
_ASSETS = {
    "/logo.png": ("logo.png", "image/png"),
    "/logo-mark.png": ("logo-mark.png", "image/png"),
    "/favicon.ico": ("logo.png", "image/png"),
}


@app.get("/logo.png")
@app.get("/logo-mark.png")
@app.get("/favicon.ico")
def static_asset(request: Request):
    fname, mime = _ASSETS[request.url.path]
    path = os.path.join(os.path.dirname(__file__), "frontend", fname)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"资源不存在：frontend/{fname}")
    # 图标几乎不变，让浏览器缓存一天，省掉每次刷新的请求
    return FileResponse(path, media_type=mime,
                        headers={"Cache-Control": "public, max-age=86400"})


# ═══════════════════════════════════════════════════════════
# ①-b ★ 登录 / 登出 / 当前用户
# ═══════════════════════════════════════════════════════════
class LoginBody(BaseModel):
    username: str
    password: str


class PasswordChangeBody(BaseModel):
    old_password: str
    new_password: str


class UserCreateBody(BaseModel):
    username: str
    password: str
    role: str = "user"
    display_name: str = ""


class UserUpdateBody(BaseModel):
    role: str | None = None
    enabled: bool | None = None
    display_name: str | None = None
    password: str | None = None
    new_username: str | None = None     # ★ 改登录名（重命名）


class FavoriteBody(BaseModel):
    object_type: str                    # record / dataset / dashboard（当前只开放 record）
    object_id: str


@app.get("/api/ping")
def api_ping():
    """探活（不需要登录）"""
    return {"ok": True}


@app.post("/api/auth/login")
def api_login(body: LoginBody):
    """
    账号口令登录 → 发一个会话 token

    ★ 失败时不区分"账号不存在"和"口令错误"——否则可以拿来枚举系统里有哪些账号。
    """
    config_store.purge_expired_sessions()          # 顺手清理过期会话
    user = config_store.verify_user(body.username.strip(), body.password)
    if not user:
        raise HTTPException(status_code=401, detail="账号或密码不正确")
    token = config_store.create_session(user["username"])
    return {"ok": True, "token": token, "user": user,
            "message": f"欢迎回来，{user['display_name']}"}


@app.post("/api/auth/logout")
def api_logout(request: Request):
    """登出：删掉服务端会话（token 立即失效）"""
    config_store.delete_session(_bearer_token(request))


@app.post("/api/me/password")
def api_change_my_password(body: PasswordChangeBody, request: Request):
    """登录用户自助改密（首次登录强制改密也走这里）。

    ★ 必须校验原密码：仅凭一个有效 token 不能直接改密。
    """
    user = request.state.user
    try:
        updated = config_store.change_own_password(
            user["username"], body.old_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": updated, "message": "密码已修改"}
    return {"ok": True, "message": "已退出登录"}


@app.get("/api/auth/me")
def api_me(request: Request):
    """
    当前登录用户

    ★ 刻意不做成"未登录就 401"：页面首次加载要用它来"探是否已登录"，
      返回 {user: null} 比抛错更好用（前端不用把 401 当异常处理）。
    """
    return {"user": getattr(request.state, "user", None)}


# ═══════════════════════════════════════════════════════════
# ①-c ★ 账号管理（仅管理员，中间件已挡）
# ═══════════════════════════════════════════════════════════
@app.get("/api/users")
def api_list_users():
    """账号列表 + 角色字典（前端不抄一份角色文案）"""
    return {"items": config_store.list_users(), "roles": config_store.ROLE_ZH}


@app.post("/api/users")
def api_create_user(body: UserCreateBody):
    """新建账号（管理员 / 普通用户）"""
    try:
        u = config_store.create_user(body.username, body.password,
                                     role=body.role, display_name=body.display_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": u, "message": f"已创建账号：{u['username']}（{u['role_zh']}）"}


@app.put("/api/users/{username}")
def api_update_user(username: str, body: UserUpdateBody):
    """改角色 / 启停 / 显示名 / 重设口令 / 改登录名"""
    try:
        u = config_store.update_user(
            username, role=body.role, enabled=body.enabled,
            display_name=body.display_name, password=body.password,
            new_username=body.new_username,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": u, "message": "已保存（该账号现有会话已失效，需重新登录）"}


@app.delete("/api/users/{username}")
def api_delete_user(username: str):
    """删除账号"""
    try:
        config_store.delete_user(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "message": f"已删除账号：{username}"}


# ═══════════════════════════════════════════════════════════
# ②-b ★ 给查询结果配"中文名"
# ═══════════════════════════════════════════════════════════
# 【为什么需要这个？】
#   查询结果直接显示 order_count / app_entity_source 这种技术列名，
#   业务同学看不懂；而且 app_entity_source 的值是 101/102 这种编码，
#   不翻译成"车拖车/车拖车司机端"根本没法读。
#   所以我们把知识库里已有的中文名回填到结果上：
#     表头显示「中文名 + 字段名」
#     编码值旁边标注中文含义
#
# 【中文名从哪来？】按可信度从高到低：
#   ① 数据字典名 —— 该字段配了字典，说明它是业务编码维度，字典名就是业务叫法
#   ② 人工维护的字段注释
#   ③ 数据库自带的字段注释
#   ④ 指标名 —— 列是算出来的别名（如 order_count），且本次只召回了一个指标
#   都没有就留空，前端只显示字段名（不瞎编）。
# ═══════════════════════════════════════════════════════════
def _result_labels(columns, sql, index, metric_names):
    """
    给结果列找中文名，并准备"编码 → 中文"的取值映射

    ★ 中文名的来源（按可信度从高到低）：
      ① 数据字典名 —— 该字段配了字典，说明它是业务编码维度，字典名就是业务叫法
      ② 人工维护的字段注释
      ③ 数据库自带的字段注释
      ④ 指标名 —— 只给【真正算出来的列】用（也就是"根据问题取"）：
         且仅当本次只召回一个指标时才兜底，召回多个就不猜

    ★★ 关键：先顺着 SELECT 的别名找到【来源字段】，再去查注释。
      模型常写 `origin_city AS city`：输出列叫 city，
      但带注释的字段是 origin_city（"起点城市"）。
      只看输出列名会导致：
        ① 取不到注释 ② 把这一列误判成"算出来的列"，
        贴上指标名 —— 截图上「city」被标成「完单率」就是这么来的。
      错误标签比没有标签更糟：用户会以为那一列就是完单率。

    返回：(column_labels, value_maps)
        column_labels = {列名: {"zh": 中文名, "source": dict/manual/db/metric/""}}
        value_maps    = {列名: {"101": "车拖车", ...}}   只有配了字典的字段才有
    """
    labels, value_maps = {}, {}

    # ── ① 数据字典：字段 → 字典 ──
    #   （取值映射留到最后统一合并，因为注释里也可能有枚举含义）
    dict_by_field = {}
    for d in (index.get("dicts") or []):
        f = d.get("field_hint")
        if f:
            dict_by_field[f] = d

    # ── ② 输出列 → 来源字段（顺着别名找到真实字段） ──
    src_field = guard.output_source_fields(sql or "")

    # ── ★②.5 SQL 中文别名（规则18：模型给每个输出列都加了反引号中文别名）──
    #   别名是模型为【这次查询】专门起的，比字段注释/指标名更贴合本次语义，
    #   所以表头优先显示它。按位置对齐（第 i 个输出项 = 结果第 i 列），
    #   数量对不上（如 SELECT *）就放弃，退回原来的注释/指标名兜底。
    _sql = sql or ""
    sql_aliases = guard.output_aliases(_sql)
    alias_by_col = {}
    if sql_aliases and len(sql_aliases) == len(columns):
        for _i, _a in enumerate(sql_aliases):
            if _a and any("\u4e00" <= ch <= "\u9fff" for ch in _a):
                alias_by_col[columns[_i]] = _a

    # ── ③ 字段注释 + 真实字段全集 ──
    field_zh, field_src = {}, {}
    comment_values = {}         # ★ {字段: {编码: 含义}} 从注释里解析出来的枚举
    all_fields = set()          # ★ 该表所有真实字段（含没有注释的）—— 用来判断"这列是不是字段"
    for t in guard.extract_tables(sql or ""):
        try:
            manual = config_store.get_column_comments(t)         # {"表.字段": 注释}
            db_cols = db.fetch_comments([t]).get("columns", {})  # 库自带注释
            for col, _dtype in db.list_columns(t):
                all_fields.add(col)          # ★ 不管有没有注释都收进来
                m = manual.get(f"{t}.{col}")
                b = db_cols.get(f"{t}.{col}")
                if m:                        # 人工优先
                    field_zh[col], field_src[col] = m, "manual"
                elif b:
                    field_zh[col], field_src[col] = b, "db"
                #   ★ 注释里往往已经写清了枚举含义（"车辆状况，1能启动，2不能启动"），
                #     自动解析成「编码 → 中文」，结果表就能把 1、2 直接翻成中文 ——
                #     不必要求每个枚举字段都去手工配一份数据字典
                #     （本库 89 个枚举字段只有 3 个配了字典，剩下 86 个原本全是裸数字）。
                vals = enum_parse.parse_values(m or b)
                if vals:
                    comment_values[col] = vals
        except Exception:
            continue    # 取不到注释不影响主流程，只是这列没有中文名

    #   ★ 只在"本次只召回了一个指标"时才兜底：
    #     召回多个指标时无法判断这个计算列属于哪个指标，宁可不猜。
    #     这是本项目一贯的原则 —— 宁可没有中文名，也不要贴错标签让人误读。
    single_metric = metric_names[0] if len(metric_names) == 1 else None

    #   ★ 数一下有几个"算出来的列"（既没有来源字段、也不是表里的真实字段）。
    #     这个数字决定计算列用哪种兜底策略：
    #       只有一个计算列 → 用指标名。因为这一列必然就是那个指标，业务叫法更准
    #                        （问"各城市的完单率"，ratio 列就该叫"完单率"而不是翻译的"完成率"）
    #       有多个计算列   → 用列名翻译。否则「每个月的订单量趋势」会把 month 列
    #                        也标成"订单量" —— 时间列怎么会是订单量？
    calc_cols = [c for c in columns
                 if not src_field.get(c) and c not in all_fields]

    for c in columns:
        src = src_field.get(c)                        # 别名背后的真实字段（没有就是 None）
        key = src or c                                # 查注释用的键：优先用真实字段名
        is_field = bool(src) or (c in all_fields)     # 这一列是不是来自某个真实字段

        if c in alias_by_col:
            # ★ SQL 里的中文别名（规则18）优先 —— 和"SQL 的中文释义"完全一致
            labels[c] = {"zh": alias_by_col[c], "source": "alias"}
        elif key in dict_by_field:
            labels[c] = {"zh": dict_by_field[key]["name"], "source": "dict"}
        elif key in field_zh:
            labels[c] = {"zh": field_zh[key], "source": field_src[key]}
        elif not is_field:
            #   ★ 计算列（没有来源字段）的中文名有两条路：
            #     ① 按列名逐词翻译 —— 列名是模型为【这一列】专门起的，
            #        最贴合这一列的实际含义（month → "月份"、driver_count → "司机数"）
            #     ② 用命中的指标名兜底
            #   ★ 谁优先，取决于一共有几列：
            #     只有一列 → 指标名更贴业务叫法（问"订单量"就该显示"订单量"，
            #                而不是翻译出来的"订单数"）
            #     有多列   → 用列名翻译。否则会出现「每个月的订单量趋势」
            #                把 month 列也标成"订单量"这种离谱结果 —— 时间列怎么会是订单量？
            #   ★ 只对计算列这么做：真实字段的名字是历史遗留的（如 order_new_old），
            #     直译会得到"订单新增存量"，看着像中文，其实是另一回事。
            zc = colname.translate(c)
            if single_metric and len(calc_cols) == 1:
                labels[c] = {"zh": single_metric, "source": "metric"}
            elif zc:
                labels[c] = {"zh": zc, "source": "colname"}
            elif single_metric:
                labels[c] = {"zh": single_metric, "source": "metric"}
            else:
                labels[c] = {"zh": "", "source": ""}
        else:
            #   是真实字段但没注释 → 留空（不瞎编：字段名是历史命名，直译容易出错）
            labels[c] = {"zh": "", "source": ""}

        #   ★ 取值映射的两个来源，合并（别名列也一起处理）：
        #     ① 注释解析（自动，覆盖广）
        #     ② 数据字典（人工明确配过，更可信）→ 覆盖同名编码
        merged = dict(comment_values.get(key) or {})
        if key in dict_by_field:
            merged.update({str(i["value"]): str(i["label"])
                           for i in (dict_by_field[key].get("items") or [])})
        if merged:
            value_maps[c] = merged

    # ── ★ 消歧：多列撞上同一个中文名时，用列名里的修饰词把它们区分开 ──
    #    「本周和上周的订单量对比」两列都叫"订单量"，用户根本分不清哪列是哪列。
    #    以前表头下面还挂着英文字段名，凑合还能认；现在表头只显示中文，这个问题被放大了。
    colname.disambiguate(labels, columns)

    # 只返回结果里真实出现的列的取值映射，别把整个字典塞给前端
    value_maps = {k: v for k, v in value_maps.items() if k in columns}
    return labels, value_maps


# ═══════════════════════════════════════════════════════════
# ③ 取数接口
# ═══════════════════════════════════════════════════════════
def _result_preview(columns, rows, max_rows=3):
    """
    结果前几行压成一段文本，存进取数记录。

    ★ 只存摘要不存全量：一次查询可能上万行，全存会把配置库撑爆；
      而审核时真正要看的是「SQL 对不对 + 命中哪些表/指标 + 结果是几行什么样」，
      前 3 行足够判断。
    """
    if not columns:
        return ""
    out = [" | ".join(str(c) for c in columns)]
    for r in (rows or [])[:max_rows]:
        out.append(" | ".join("" if v is None else str(v) for v in r))
    if rows and len(rows) > max_rows:
        out.append(f"...（共 {len(rows)} 行）")
    return "\n".join(out)
@app.post("/api/ask")
async def api_ask(body: AskBody, request: Request):
    """自然语言 → SQL → 结果

    ★ 用线程池跑（agent.ask 内部有 LLM 调用 + 多次 ClickHouse 查询，
      单个复杂查询可能跑 40~70 秒）。若不异步，单 worker 的 uvicorn 会被这一个
      请求占满事件循环，期间所有其他请求（含页面其他点击）都排队 → 前端"一直加载"。
      改为 asyncio.to_thread 后，事件循环不阻塞，多个取数请求可并发，
      且不会拖垮页面其它接口。
    """
    _ensure_db()
    question = body.question.strip()                # 去掉首尾空白
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    index = get_index()                             # 拿索引（主线程，避免传到线程里重建）
    user = getattr(request.state, "user", None) or {}
    # ★ 传可信用户：agent 内据此启用字段级权限闸门（DENY 拦截 + MASK 脱敏）
    result = await asyncio.to_thread(agent.ask, index, question, user)   # ★ 线程池跑，不卡事件循环

    # ★ 展示用：SQL 排版 + 列中文名 + 取值翻译
    #   都放在这里（而不是 agent 里）—— agent 负责"算得对"，
    #   server 负责"显示得好"，两边职责别混。
    labels, value_maps = ({}, {})
    if result["ok"] and result["columns"]:
        try:
            labels, value_maps = _result_labels(
                result["columns"], result["sql"], index, result["metrics"])
        except Exception as e:
            print(f"⚠️ 结果列中文名解析失败（不影响数据）：{e}")

    payload = _clean_nan({
        "question": result["question"],
        "ok": result["ok"],
        "sql": result["sql"],
        "sql_formatted": guard.format_sql(result["sql"]),   # ★ 排版后的 SQL（仅展示）
        "error": result["error"],
        "columns": result["columns"],
        "column_labels": labels,                    # ★ {列名: {zh, source}}
        "value_maps": value_maps,                   # ★ {列名: {编码: 中文}}
        "rows": _jsonable(result["rows"]),          # 转成可序列化格式
        "row_count": len(result["rows"]),
        "tables": result["tables"],
        "metrics": result["metrics"],
        "dicts": result["dicts"],                   # ★ 命中的数据字典（字段取值含义）
        "info": result["info"],
        "attempts": result["attempts"],
        "pipeline": result.get("pipeline"),   # ★ 流水线标记：v2=企业级 / legacy=旧兜底（异常回退时）
        "review": result.get("review"),       # ★ SQL 逻辑自审报告（逐字段诊断，只诊断不改 SQL）
        # ── 企业级流水线新增字段（Semantic Layer + Validator + NL Answer）──
        "understanding": result.get("understanding"),   # 查询理解：意图/指标/实体
        "validation": result.get("validation"),         # SQL 生成前 5 维校验报告
        "result_warnings": result.get("result_warnings"),  # 执行后结果校验 warning
        "validation_status": result.get("validation_status"),  # ★ 校验状态：passed/failed/error/degraded/blocked
        "answer": result.get("answer"),                 # 自然语言回答
        "table_swap": result.get("table_swap", ""),     # ★ 换表说明（点名表 vs 实际用表）
        "trace": result.get("trace"),                   # 各阶段耗时（可观测性）
        # ── 降级可观测性：V2 异常降级 legacy 时留痕（便于统计降级率/告警）──
        "degraded": result.get("degraded", False),          # 是否发生降级
        "degrade_reason": result.get("degrade_reason", ""),  # 降级原因（异常类型+信息）
        "degrade_stage": result.get("degrade_stage", ""),    # 降级发生的阶段
        # ── 字段级权限（ALLOW/MASK/DENY）──
        "masked": result.get("masked", False),                # 是否发生结果脱敏
        "mask_map": result.get("mask_map") or {},             # {列名: 脱敏规则}
        "deny_reasons": (result.get("perm_decision") or {}).get("deny_reasons") or [],
    })

    # ★ 自动落一条取数记录（个人历史 + 审核通道）
    #   为什么放在服务端而不是让前端上报？
    #     前端能被绕过 —— 用户不点上报，这次取数就"不存在"，历史永远不全。
    #     审核也需要"这次到底跑了什么"的原始证据，不能全靠用户自述。
    #   落库失败绝不影响取数本身：用户要的是数，记录只是附加动作。
    try:
        who = getattr(request.state, "user", None) or {}
        rid = config_store.add_query_record({
            "username": who.get("username", ""),
            "question": payload.get("question", ""),
            "sql": payload.get("sql", ""),
            "ok": payload.get("ok"),
            "tables": payload.get("tables"),
            "metrics": payload.get("metrics"),
            "dicts": payload.get("dicts"),
            "columns": payload.get("columns"),
            "row_count": payload.get("row_count"),
            "result_preview": _result_preview(payload.get("columns"), payload.get("rows")),
            "answer": payload.get("answer"),
            # ★ 处理过程 + 查询理解也要一起存：
            #   审核的人拿到"一条 SQL"其实很难判断对错 ——
            #   他需要知道"模型当时把问题理解成了什么（意图/指标/维度）"，
            #   才能分清是"理解错了"还是"口径写错了"。这两类问题的修法完全不同。
            "info": payload.get("info", ""),
            "attempts": payload.get("attempts"),
            "pipeline": payload.get("pipeline"),
            "understanding": payload.get("understanding"),
        })
        payload["record_id"] = rid
        # ★ 成功取数落一份结果快照，供导出/回看；
        #   失败不影响取数本身（导出会提示"该记录没有快照"）。
        if payload.get("ok") and payload.get("rows"):
            try:
                config_store.save_result_snapshot(
                    rid, payload.get("columns") or [], payload.get("rows") or [])
            except Exception as se:
                print(f"⚠️ 结果快照落库失败（不影响取数）：{se}")
    except Exception as e:
        print(f"⚠️ 取数记录落库失败（不影响取数）：{e}")

    # ★ 权限判定审计（不落敏感原始值；失败不影响取数）
    try:
        _audit_permission(request, payload, result)
    except Exception as e:
        print(f"⚠️ 权限审计落库失败（不影响取数）：{e}")

    return payload


def _audit_permission(request, payload, result):
    """把本次取数的字段级权限判定写入审计日志（供 /api/permission/audit 查看）。"""
    perm = result.get("perm_decision") or {}
    if not perm:
        return
    config_store.add_permission_audit({
        "username": (getattr(request.state, "user", None) or {}).get("username", ""),
        "roles": permission.user_roles(getattr(request.state, "user", None) or {}),
        "request_id": str(getattr(request.state, "request_id", "") or ""),
        "question": payload.get("question", ""),
        "tables": perm.get("requested_tables") or [],
        "columns": perm.get("output_columns") or [],
        "action": "query",
        "decision": ("deny" if perm.get("deny_reasons") else
                     ("mask" if perm.get("mask_map") else "allow")),
        "deny_reason": "；".join(perm.get("deny_reasons") or []),
        "rewritten": 1 if perm.get("rewritten") else 0,
        # ★ 整改任务7（S-01）：审计落库前对 SQL 字面量脱敏——手机号/身份证等
        #   用户输入的字符串字面量一律替换为 ?，只保留表/列/子句结构供诊断。
        #   注意：这是【审计存储】的脱敏，与返回给用户前的【查询结果】脱敏
        #   （permission.mask_rows）相互独立，二者已分别有测试覆盖。
        "final_sql": permission.safe_sql_repr(payload.get("sql", "")),
        "returned_columns": payload.get("columns") or [],
        "ok": 1 if payload.get("ok") else 0,
    })


# ═══════════════════════════════════════════════════════════
# ③ Prompt 配置接口
# ═══════════════════════════════════════════════════════════
@app.get("/api/prompts")
def api_list_prompts():
    """列出所有 Prompt 配置项（含内容，方便前端直接编辑）"""
    result = []
    for key, _length in config_store.list_prompts():    # 遍历 (key, 字符数)
        result.append({"key": key, "content": config_store.get_prompt(key)})
    return result


@app.put("/api/prompts/{key}")
def api_set_prompt(key: str, body: ContentBody):
    """修改某个 Prompt 配置"""
    config_store.set_prompt(key, body.content)
    return {"ok": True, "key": key, "length": len(body.content),
            "message": "已保存。★ 重启索引后生效（点『重建索引』）"}


@app.post("/api/prompts/{key}/reset")
def api_reset_prompt(key: str):
    """把某个 Prompt 恢复成默认值"""
    if key not in config_store.DEFAULT_PROMPTS:
        raise HTTPException(status_code=404, detail=f"没有默认值：{key}")
    config_store.set_prompt(key, config_store.DEFAULT_PROMPTS[key])
    return {"ok": True, "key": key}


# ═══════════════════════════════════════════════════════════
# ④ 指标知识接口
# ═══════════════════════════════════════════════════════════
@app.get("/api/metrics")
def api_list_metrics():
    """列出全部指标（含已停用的）—— 管理页面要看全部，否则没法把停用的打开"""
    return config_store.get_metrics(enabled_only=False)


@app.put("/api/metrics/{name}")
def api_upsert_metric(name: str, body: MetricBody):
    """
    新增或修改一个指标

    ★ 注意：这里【不传 enabled】—— 修改口径不会动开放状态。
      想改开放状态请用下面的 /api/metrics/{name}/enabled 接口。
      两件事分开，避免"改个口径把停用过的指标无声地打开了"。
    """
    config_store.upsert_metric({
        "name": name,
        "keywords": body.keywords,
        "definition": body.definition,
        "doc": body.doc,
        "tables": body.tables,
    })
    return {"ok": True, "name": name, "message": "已保存。★ 点『重建索引』后生效"}


@app.put("/api/metrics/{name}/enabled")
def api_set_metric_enabled(name: str, body: MetricEnabledBody):
    """
    开放 / 停用一个指标

    ★ 停用的效果：
      ① 该指标不进知识库 → AI 检索不到它的口径
      ② 用户问到它 → 在入口处被直接拒绝（不会让模型自己拼一个口径出来）
    """
    if not config_store.set_metric_enabled(name, body.enabled):
        raise HTTPException(status_code=404, detail=f"没找到指标：{name}")
    return {
        "ok": True,
        "name": name,
        "enabled": body.enabled,
        "message": "已切换。★ 点『重建索引』后生效",
    }


@app.delete("/api/metrics/{name}")
def api_delete_metric(name: str):
    """删除一个指标"""
    if not config_store.delete_metric(name):
        raise HTTPException(status_code=404, detail=f"没找到指标：{name}")
    return {"ok": True, "name": name}


# ═══════════════════════════════════════════════════════════
# ⑤ 表注释接口
# ═══════════════════════════════════════════════════════════
@app.get("/api/tables")
def api_list_tables(search: str = ""):
    _ensure_db()
    """
    列出【全部真实表】 + 业务注释 + 权限状态
    ★ 数据来源有三处，前端合并展示：
        db.list_tables()  （真实库的全部表名，保证没漏）
        table_comments    （人工/自动发现的业务注释）
        table_acl         （访问权限）
    ★ search 参数：按表名/注释过滤（不区分大小写），前端搜索框用
    """
    names = knowledge.business_tables()             # 全部真实业务表（已剔除技术性噪声表）
    comments = config_store.get_table_comments()    # {表名: 注释}
    sources = config_store.get_table_comment_sources()   # {表名: manual|ai}
    acl = config_store.get_table_acl()              # {表名: {visible, note}}

    kw = (search or "").strip().lower()
    rows = []
    for name in names:
        info = acl.get(name)                        # 取权限信息（可能没有）
        comment = comments.get(name, "")
        if kw and kw not in name.lower() and kw not in comment.lower():
            continue
        rows.append({
            "table": name,
            "comment": comment,
            "comment_source": sources.get(name, "") if comment else "",
            "visible": info["visible"] if info else config.ACL_DEFAULT_VISIBLE,
            "acl_note": info["note"] if info else "",
        })
    return rows


@app.put("/api/tables/{name}")
def api_set_table_comment(name: str, body: TableBody):
    """修改表的业务注释"""
    config_store.set_table_comment(name, body.comment)
    return {"ok": True, "table": name, "message": "已保存。★ 点『重建索引』后生效"}


# ═══════════════════════════════════════════════════════════
# ⑥ 表权限接口
# ═══════════════════════════════════════════════════════════
# ★ 开放权限时自动起草表业务注释
#   【为什么要自动做，而不是等用户去点按钮？】
#     表注释决定"模型能不能找对表"，但写它得通读几百个字段 ——
#     授权这步是用户唯一会主动碰"这张表"的时刻，此刻起草最顺手。
#     等用户想起来去补，往往已经是答错几次之后了。
#   【为什么放队列里串行跑，而不是每个表开一个线程？】
#     批量授权 50 张表时，50 个线程同时打 LLM 会被限流、也可能拖垮服务。
#     串行 + 去重足够：晚几秒拿到草稿，比把服务打挂好得多。
_DOC_QUEUE = queue.Queue()      # 待生成的表名
_DOC_PENDING = set()            # 已入队 / 正在生成（防重复入队）
_DOC_LOCK = threading.Lock()
_DOC_WORKER = None

# ★ 每张表的"开荒"进度：表注释 + 业务实体 各做到哪了
#   【为什么要把进度暴露出来？】
#     这两步都要调大模型（几秒到十几秒），用户点了「开放访问」之后
#     如果界面上什么都不动，会以为没生效 —— 必须让它看得见"正在生成什么、成了没有"。
_PREP_STATE = {}
_PREP_LOCK = threading.Lock()


def _set_prep(table, kind, status, detail=""):
    """记一下某张表某个产物（doc/entity）的生成状态"""
    with _PREP_LOCK:
        st = _PREP_STATE.setdefault(table, {"table": table})
        st[kind] = {"status": status, "detail": detail}
        st["at"] = time.time()


def _needs_auto_doc(name):
    """
    这张表要不要（能不能）自动生成 AI 表注释

    ★ 判断依据是注释的【来源】，不是"有没有注释"：
        - None      → 还没注释 → 生成（白名单时第一时间给它一个可用的中文描述）
        - "ai"      → 用户没改过 → 可重新生成覆盖（字段变了 / 上次生成质量差）
        - "manual"  → 用户手写或改过 → 绝不覆盖（那是人家精修过的，AI 碰不得）
    """
    return config_store.get_table_comment_source(name) != "manual"


def _doc_loop():
    """
    后台 worker：给新开放的表"开荒" —— 生成 AI 表注释 + 起草业务实体

    【为什么要连实体一起做？】
      开放一张表只是让它"能被检索到"，但要让它"能被认出来"，还得登记业务实体
      （语义路由靠"业务俗称 → 权威主表"匹配）。新表两样通常都没有，
      而两样需要的输入都是同一份字段清单 —— 一次做完最省事。
      漏掉实体登记的后果很隐蔽：表开放了、也能答，但用户用俗称提问时
      总是匹配不到，看起来像"这张表没用"。
    """
    while True:
        name = _DOC_QUEUE.get()
        try:
            # ── ① 表业务注释 ──
            if _needs_auto_doc(name):
                r = suggest.generate_table_comment(name)
                _set_prep(name, "doc", "done", (r.get("suggested") or "")[:80])
                print(f"[auto-doc] {name} 已生成临时表注释：{r['suggested'][:40]}…")
            else:
                _set_prep(name, "doc", "skipped", "已有人工注释，不覆盖")

            # ── ② 业务实体（语义层 P2 登记）──
            #   generate_entity 内部自带"已有人工版就不覆盖"的护栏，
            #   所以这里不用再判一次，也不会把人工登记的实体冲掉。
            er = suggest.generate_entity(name)
            if er.get("ok"):
                ent = er.get("entity") or {}
                _set_prep(name, "entity", "done",
                          f"{ent.get('name','')}（{'、'.join(ent.get('keywords') or [])}）")
                print(f"[auto-entity] {name} → {ent.get('name')} "
                      f"关键词={ent.get('keywords')}")
            else:
                #   不是失败，是被护栏挡下了（人工实体优先）—— 状态分开写，别混
                _set_prep(name, "entity", "skipped", er.get("reason", ""))
                print(f"[auto-entity] {name} 跳过：{er.get('reason')}")
        except Exception as e:
            #   ★ 不静默失败：表读不到、模型超时都要留痕，否则用户以为"没生成就是没戏"
            msg = str(e)[:140]
            _set_prep(name, "doc", "failed", msg)
            print(f"⚠️ [auto-doc] {name} 生成失败：{msg}")
        finally:
            #   实体可能已经写进库了 → 缺口清单必须作废，别让用户看到旧结果
            _clear_gaps_cache()
            with _DOC_LOCK:
                _DOC_PENDING.discard(name)
            _DOC_QUEUE.task_done()


def _queue_auto_doc(name):
    """把一张表排进后台生成队列；返回是否真的新入队"""
    global _DOC_WORKER          # ★ global 必须写在函数里第一次用到该名字之前
    if not config.AUTO_TABLE_DOC:
        return False
    with _DOC_LOCK:
        if name in _DOC_PENDING:
            return False
        _DOC_PENDING.add(name)
        if _DOC_WORKER is None or not _DOC_WORKER.is_alive():
            #   用 daemon：进程退出时不用等它，别把关闭流程卡住
            _DOC_WORKER = threading.Thread(target=_doc_loop, daemon=True, name="auto-doc")
            _DOC_WORKER.start()
    with _PREP_LOCK:
        _PREP_STATE[name] = {"table": name, "at": time.time(),
                             "doc": {"status": "pending", "detail": ""},
                             "entity": {"status": "pending", "detail": ""}}
    _DOC_QUEUE.put(name)
    return True


@app.get("/api/acl")
def api_list_acl():
    """查看当前访问策略和名单"""
    return {
        "default_visible": config.ACL_DEFAULT_VISIBLE,   # True=黑名单 / False=白名单
        "rules": config_store.list_table_acl(),          # [(表名, visible, note), ...]
    }


@app.put("/api/acl/{name}")
def api_set_acl(name: str, body: AclBody):
    """设置某张表是否允许 AI 访问"""
    config_store.set_table_acl(name, body.visible, body.note)

    # ★ 刚开放访问 → 后台自动"开荒"：生成【临时生效】的表注释 + 起草业务实体
    #   （两样都直接进 RAG，用户可改；已有人工版的一律不覆盖）
    doc_started = False
    if body.visible and _needs_auto_doc(name):
        doc_started = _queue_auto_doc(name)
    elif body.visible:
        #   表注释是人工版了，但实体可能还缺 → 仍然值得排一次队
        existing = config_store.get_entity_by_table(name)
        if not (existing and existing.get("source") == "manual"):
            doc_started = _queue_auto_doc(name)

    return {"ok": True, "table": name, "visible": body.visible,
            "doc_generating": doc_started,       # 前端据此显示"正在生成表注释 + 业务实体…"
            "entity_generating": doc_started,
            "message": "已保存。★ 点『重建索引』后生效"}


@app.delete("/api/acl/{name}")
def api_del_acl(name: str):
    """取消登记某张表（回到默认策略；白名单模式下=不可见）"""
    if not config_store.delete_table_acl(name):
        raise HTTPException(status_code=404, detail=f"没找到登记：{name}")
    return {"ok": True, "table": name, "message": "已取消登记。★ 点『重建索引』后生效"}


@app.get("/api/acl/prepare/{name}")
def api_acl_prepare_state(name: str):
    """
    查这张表"开荒"做到哪了（表注释 / 业务实体 各自的进度）

    ★ 前端在开放访问后轮询它，把"正在生成业务实体…"这类状态显示出来。
      没有这条接口，用户点完「开放访问」只看到表格刷新了一下，
      完全不知道后台在给他生成东西 —— 也就不会去看生成结果对不对。
    """
    with _PREP_LOCK:
        st = _PREP_STATE.get(name)
    return st or {"table": name, "doc": None, "entity": None,
                  "message": "这张表没有正在进行的生成任务"}


# ═══════════════════════════════════════════════════════════
# ⑥-b ★ RAG 知识库接口 —— 编辑"喂给模型的知识"
# ═══════════════════════════════════════════════════════════
#  【这个页面存在的意义：消除幻觉】
#    模型不会凭空知道"status=6 是已完成"，它只知道我们给它的东西。
#    所以 RAG 的效果不取决于模型多强，而取决于【我们喂了什么知识】。
#    三类知识在这里编辑：
#      ① 表结构信息（表注释 + 字段清单）
#      ② 字段信息（字段业务注释）★ 准确率最直接的杠杆
#      ③ 指标信息（业务口径，走 /api/metrics）
# ═══════════════════════════════════════════════════════════
def _kb_business_tables():
    """
    RAG 知识库关注的真实业务表（已剔除 ClickHouse 的分片/物化视图/备份等噪声表）

    ★ 规则放在 knowledge.business_tables()，页面 / 导出 / 自检脚本共用一份 ——
      避免"页面显示 3 张表、导出文件里 368 张"这种对不上账的情况。
    """
    return knowledge.business_tables()


@app.get("/api/kb/overview")
def api_kb_overview():
    """
    RAG 知识库总览：注释覆盖率 + 指标情况

    ★ 为什么把"覆盖率"放在最显眼的位置？
      字段注释是 text-to-SQL 准确率最直接的杠杆，但真实数仓里往往一条都没有
      （本库实测 0/18025）。把进度摆出来，才知道知识库现在什么水平、该往哪补。

    ★ 性能注意：这里【不能】用 db.fetch_schema() 数字段。
      拉全库 18025 行明细在 SSH 隧道下实测要 40 秒，页面没法用。
      改成让数据库做聚合（list_column_stats，每表一行），1 秒内返回。
    """
    names = _kb_business_tables()
    visible = config_store.filter_visible_tables(set(names), config.ACL_DEFAULT_VISIBLE)

    table_c = config_store.get_table_comments()      # 人工表注释
    col_c = config_store.get_column_comments()       # 人工字段注释
    db_c = db.fetch_comments(sorted(visible))        # 库自带注释（只抓会进 RAG 的表）
    db_table_c = db_c.get("tables", {})

    stats = db.list_column_stats()                   # {表名: {total, db_commented}}
    db_col_c = db_c.get("columns", {})               # {"表.字段": 库注释}

    # ★ 覆盖率要按【字段集合求并集】算，不能用 max(库注释数, 人工数)：
    #   人工补的往往正是库里没注释的字段（比如 status 的枚举含义），
    #   两者是"互补"关系。用 max 会把这些人工补的字段整批漏掉。
    def _keys_of(table, mapping):
        prefix = table + "."                         # 加点的前缀，避免 dwd_order 误匹配 dwd_order_all
        return {k for k in mapping if k.startswith(prefix)}

    per_table = {}
    total_cols = commented_cols = manual_cols = 0
    for t in visible:
        total = stats.get(t, {}).get("total", 0)
        db_keys = _keys_of(t, db_col_c)
        manual_keys = _keys_of(t, col_c)
        commented = len(db_keys | manual_keys)       # 并集：库注释 ∪ 人工注释
        per_table[t] = {
            "columns": total,
            "commented": commented,
            "manual": len(manual_keys),
        }
        #   ★ 人工注释是"覆盖"库注释，不是新增一行，所以这边也要按并集统计
        total_cols += total
        commented_cols += commented
        manual_cols += len(manual_keys)

    commented_tables = sum(1 for t in visible if table_c.get(t) or db_table_c.get(t))
    metrics = config_store.get_metrics(enabled_only=False)
    dicts = config_store.get_dict_entries(enabled_only=False)

    # ★ 孤儿注释：人工写了、但对应的表当前不可见（未授权，或压根不存在）。
    #   这类注释既进不了 RAG、也不会出现在导出文件里。
    #   不提示的话，用户会以为"我写的东西都在知识库里"，导出时才发现少了几条。
    #   本库实测有 5 条（早期演示留下的 dim_user / dwd_refund 等，这个库里没有这些表）。
    orphan_tables = {t for t in table_c if t not in visible}
    orphan_tables |= {k.rsplit(".", 1)[0] for k in col_c if k.rsplit(".", 1)[0] not in visible}
    orphan_notes = (sum(1 for t in table_c if t not in visible)
                    + sum(1 for k in col_c if k.rsplit(".", 1)[0] not in visible))

    return {
        "tables_visible": len(visible),              # 会进 RAG 的表数
        "tables_commented": commented_tables,        # 其中有业务注释的
        "columns_total": total_cols,                 # 这些表的字段总数
        "columns_commented": commented_cols,         # 其中有注释的（人工或数据库）
        "columns_manual": manual_cols,               # 其中人工维护的
        "metrics_total": len(metrics),
        "metrics_enabled": sum(1 for m in metrics if m["enabled"]),
        "dicts_total": len(dicts),                   # ★ 数据字典条目数
        "dicts_enabled": sum(1 for d in dicts if d["enabled"]),
        "orphan_notes": orphan_notes,                # 指向不可见表的人工注释条数
        "orphan_tables": sorted(orphan_tables),      # 具体是哪些表
        "per_table": per_table,                      # 每张表的字段覆盖明细
    }


@app.get("/api/kb/tables")
def api_kb_tables(search: str = "", only_visible: bool = True):
    """
    列出可编辑的表（供知识库页面左侧列表）

    参数：
        search       —— 按表名/注释过滤
        only_visible —— 默认 True，只看会进 RAG 的表（白名单里的）
                        False 则连未授权的也列出来（方便提前维护注释）
    """
    names = _kb_business_tables()
    table_c = config_store.get_table_comments()
    acl = config_store.get_table_acl()

    kw = (search or "").strip().lower()
    rows = []
    for name in names:
        info = acl.get(name)
        visible = info["visible"] if info else config.ACL_DEFAULT_VISIBLE
        if only_visible and not visible:
            continue
        comment = table_c.get(name, "")
        if kw and kw not in name.lower() and kw not in comment.lower():
            continue
        rows.append({
            "table": name,
            "comment": comment,
            "visible": visible,
            "in_index": visible,                     # 在 RAG 知识库里 = 当前可见
        })
    return rows


@app.get("/api/kb/table/{name}")
def api_kb_table_detail(name: str):
    """
    一张表的完整知识：表注释 + 全部字段（名字/类型/注释/来源）

    ★ comment_source 现在区分（表注释这一层）：
        "manual" —— 用户手写 / 在 AI 基础上改过的（最终版）
        "ai"     —— AI 自动生成的临时注释（已进 RAG，用户可改）
        ""       —— 还没有
      字段层（columns[].comment_source）现在也是四态了：
        manual / ai（AI 补的）/ db（库自带）/ ""（无）
    """
    table_c = config_store.get_table_comments()
    src_c = config_store.get_table_comment_sources()
    col_c = config_store.get_column_comments(name)
    col_s = config_store.get_column_comment_sources()   # {表.字段: manual|ai}
    db_c = db.fetch_comments([name])                 # ★ 只抓这一张表的库自带注释
    db_col_c = db_c.get("columns", {})

    acl = config_store.get_table_acl().get(name)
    visible = acl["visible"] if acl else config.ACL_DEFAULT_VISIBLE

    manual_table = table_c.get(name, "")

    columns = []
    for col, dtype in db.list_columns(name):
        key = f"{name}.{col}"
        manual = col_c.get(key, "")
        from_db = db_col_c.get(key, "")
        #   ★ column_comments 里现在可能装着 AI 生成的结果，
        #     直接标 manual 会让用户分不清哪些自己核实过 —— 按 source 列如实标。
        src = (col_s.get(key) or "") if manual else ("db" if from_db else "")
        columns.append({
            "column": col,
            "type": dtype,
            "comment": manual or from_db,            # 展示用：人工/AI 优先于库自带
            "comment_source": src,
            "db_comment": from_db,                   # 数据库自带的那份（字段层仍作"基本注释"参考）
        })

    return {
        "table": name,
        "comment": manual_table,
        "comment_source": src_c.get(name, "") if manual_table else "",
        #   ★ AI 自动生成时后端会把这个值直接写进 comment（临时生效），
        #     所以这里不再有单独的"suggested 参考层"—— 参考层已合并进 comment。
        "doc_generating": name in _DOC_PENDING,      # 后台是否正在生成（前端轮询用）
        "visible": visible,
        "columns": columns,
        "column_count": len(columns),
        "commented_count": sum(1 for c in columns if c["comment"]),
    }


@app.put("/api/kb/table/{name}")
def api_kb_set_table_comment(name: str, body: KbCommentBody):
    """
    保存表的业务注释（写进 RAG 知识库）

    ★ 只要人点了「保存」，就标记成 source=manual ——
      意味着"用户确认/修改过这份注释"，以后 AI 不再自动覆盖它。
    """
    config_store.set_table_comment(name, body.comment.strip(), source="manual")
    _clear_gaps_cache()            # 注释变了 → 缺口清单立刻作废，别让用户看到旧结果
    return {"ok": True, "table": name, "message": "已保存。★ 点『重建索引』后生效"}


@app.post("/api/kb/table/{name}/suggest-comment")
def api_kb_suggest_table_comment(name: str):
    """
    读这张表的字段名 + 注释，生成一句 AI 表注释并【直接写进 RAG】（临时生效）

    ★ 生成完标 source='ai' —— 页面会提示"AI 生成，可修改"；
      用户改过再保存 → source 变 'manual'，AI 就不再覆盖它。
    """
    try:
        return suggest.generate_table_comment(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成失败：{str(e)[:200]}")


# ═══════════════════════════════════════════════════════════
# ⑥-d 字段注释的 AI 补全（把「知识缺口」从清单变成可执行的一步）
# ═══════════════════════════════════════════════════════════
#   「知识缺口」算出 17 条该补的字段后，以前只能把这个清单摆给人看，
#   人得逐个去看真实取值、想含义、手打注释 —— 所以大多数缺口一直空着。
#   现在这一步可以一键跑：AI 拿着【真实取值】去归纳，人只需要复核。
#
#   ★ 为什么批量必须走后台队列，不能同步等？
#     一条要调一次模型（约 2 秒），17 条就是半分多钟。
#     HTTP 请求干等 30 秒会超时、用户会以为卡死。
#     这里沿用表注释那套「队列 + 串行」：
#       · 接口立刻返回 task_id
#       · 后台一条条跑完，前端轮询进度
#       · 串行而不是并发：几十个线程同时打 LLM 会被限流，也会拖垮服务
_FILL_LOCK = threading.Lock()
_FILL_TASKS = {}                # task_id -> {total, done, ok, failed, finished, items}
_fill_seq = [0]


def _fill_loop(tid, pairs):
    """后台 worker：逐个字段调 AI 补全"""
    _ensure_db()                                    # 隧道可能在中途掉，先确保
    task = _FILL_TASKS[tid]
    for table, column in pairs:
        try:
            r = suggest.generate_column_comment(table, column)
        except Exception as e:
            r = {"suggested": "", "values": [], "ok": False, "reason": str(e)[:120]}
        item = {"table": table, "column": column,
                "comment": r.get("suggested", ""), "ok": r.get("ok", False),
                "reason": r.get("reason", ""), "values": r.get("values", []),
                "advice": r.get("advice", "")}        # 例如 "dict" → 这条建议改用字典管
        with _FILL_LOCK:
            task["items"].append(item)
            task["done"] += 1
            if item["ok"]:
                task["ok"] += 1
            else:
                task["failed"] += 1
        tag = "✓ " + item["comment"][:36] if item["ok"] else "✗ " + item["reason"]
        print(f"[auto-fill] {table}.{column} → {tag}")
    task["finished"] = True
    print(f"[auto-fill] 任务 {tid} 完成：成功 {task['ok']} / 失败 {task['failed']} / 共 {task['total']}")


@app.post("/api/kb/column/{table}/{column}/suggest-comment")
def api_suggest_column_comment(table: str, column: str, overwrite: bool = False):
    """
    给【一个字段】生成 AI 业务注释，直接写进 column_comments(source='ai')

    ★ 和表注释完全同一套语义：生成即生效、已进 RAG、用户改过就不再被覆盖。

    ★ overwrite 参数：这条注释已经是人工版时，是否允许 AI 改写。
      默认 False（保护）；前端在用户明确确认过之后才传 True。
      见 suggest.generate_column_comment 里"为什么默认保护人工版"的说明。

    ★ ok=False 时不写库，把 reason 返回给前端（"取值过多，建议配字典" /
      "这条是你手写的" / "模型漏了某个取值" / "编造了不存在的取值"），
      让用户知道这次为什么没补上，而不是默默什么都不发生。
    """
    try:
        return suggest.generate_column_comment(table, column, overwrite=overwrite)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成失败：{str(e)[:200]}")


class FillBody(BaseModel):
    items: list = []            # [{"table":..., "column":...}, ...]


@app.post("/api/gaps/auto-fill")
def api_gaps_auto_fill(body: FillBody = None):
    """
    批量 AI 补全字段注释 → 立刻返回 task_id，后台串行跑

    ★ 前端拿 task_id 轮询 GET /api/gaps/auto-fill/{task_id} 看进度，
      跑完一次性拿到每条的成功 / 失败明细。
    """
    pairs = []
    for it in (body.items if body else []):
        t, c = (it or {}).get("table"), (it or {}).get("column")
        if t and c:
            pairs.append((t, c))
    if not pairs:
        raise HTTPException(status_code=400, detail="没有指定要补全的字段")
    if len(pairs) > 200:
        raise HTTPException(status_code=400, detail=f"一次最多 200 个字段，当前 {len(pairs)} 个")

    _fill_seq[0] += 1
    tid = str(_fill_seq[0])
    with _FILL_LOCK:
        _FILL_TASKS[tid] = {"total": len(pairs), "done": 0, "ok": 0, "failed": 0,
                            "items": [], "finished": False}
    threading.Thread(target=_fill_loop, args=(tid, pairs),
                     daemon=True, name=f"auto-fill-{tid}").start()
    return {"task_id": tid, "total": len(pairs),
            "message": f"已开始补全 {len(pairs)} 个字段，可继续做别的"}


@app.get("/api/gaps/auto-fill/{task_id}")
def api_gaps_fill_progress(task_id: str):
    """查批量补全任务的进度 + 每条明细"""
    task = _FILL_TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {k: task[k] for k in ("total", "done", "ok", "failed", "finished", "items")}


# ═══════════════════════════════════════════════════════════
# 表级缺口的"一键补全"：补表业务注释 / 补业务实体
# ═══════════════════════════════════════════════════════════
#  【它和上面的字段级补全什么关系？】
#    字段级补的是"这一列怎么算对"，表级补的是"这张表能不能被找对"。
#    两者都靠同一份字段清单喂给模型，所以实现上共用一套任务队列和进度接口格式。
class FillTableBody(BaseModel):
    items: list = []            # [{"table":..., "kind": "table_comment"|"entity"}, ...]


def _fill_table_loop(tid, pairs):
    """后台 worker：逐个（表, 缺口类型）调 AI 补全"""
    _ensure_db()                                    # 隧道可能在中途掉，先确保
    task = _FILL_TASKS[tid]
    for table, kind in pairs:
        try:
            if kind == "table_comment":
                r = suggest.generate_table_comment(table)
                item = {"table": table, "kind": kind,
                        "ok": bool(r.get("suggested")), "value": r.get("suggested", ""),
                        "reason": "" if r.get("suggested") else "模型没给出有效注释"}
            else:                                   # entity
                r = suggest.generate_entity(table)
                ent = r.get("entity") or {}
                item = {"table": table, "kind": kind, "ok": bool(r.get("ok")),
                        "value": ent.get("name", ""),
                        "keywords": ent.get("keywords", []),
                        "grain": ent.get("grain", ""),
                        "reason": r.get("reason", "")}
        except Exception as e:
            item = {"table": table, "kind": kind, "ok": False,
                    "value": "", "reason": str(e)[:120]}
        with _FILL_LOCK:
            task["items"].append(item)
            task["done"] += 1
            if item["ok"]:
                task["ok"] += 1
            else:
                task["failed"] += 1
        tag = ("✓ " + item["value"][:40]) if item["ok"] else ("✗ " + item["reason"])
        print(f"[fill-table] {table} / {kind} → {tag}")
    _clear_gaps_cache()          # ★ 补完必须作废缺口缓存，否则清单还是旧的
    task["finished"] = True
    print(f"[fill-table] 任务 {tid} 完成：成功 {task['ok']} / 失败 {task['failed']} / 共 {task['total']}")


@app.post("/api/gaps/fill-table")
def api_gaps_fill_table(body: FillTableBody = None):
    """
    一键补全【表级】缺口（表业务注释 / 业务实体）→ 立刻返回 task_id，后台串行跑

    ★ 为什么也做成后台任务而不是同步返回？
      一个实体要读字段清单 + 调一次大模型，几十张表就是几分钟。
      同步做浏览器会一直转圈直到超时；后台跑则"提交完就能继续干别的"，
      进度用和字段级补全同一个轮询接口格式，前端可以复用一套代码。
    """
    pairs = []
    for it in (body.items if body else []):
        t = (it or {}).get("table")
        k = (it or {}).get("kind") or "entity"
        if t and k in ("table_comment", "entity"):
            pairs.append((t, k))
    if not pairs:
        raise HTTPException(status_code=400, detail="没有指定要补全的表 / 缺口类型")
    if len(pairs) > 50:
        raise HTTPException(status_code=400, detail=f"一次最多 50 项，当前 {len(pairs)} 项")

    _fill_seq[0] += 1
    tid = str(_fill_seq[0])
    with _FILL_LOCK:
        _FILL_TASKS[tid] = {"total": len(pairs), "done": 0, "ok": 0, "failed": 0,
                            "items": [], "finished": False}
    threading.Thread(target=_fill_table_loop, args=(tid, pairs),
                     daemon=True, name=f"fill-table-{tid}").start()
    return {"task_id": tid, "total": len(pairs),
            "message": f"已开始补全 {len(pairs)} 项表级缺口，可继续做别的"}


@app.put("/api/kb/column/{table}/{column}")
def api_kb_set_column_comment(table: str, column: str, body: KbCommentBody):
    """
    保存字段的业务注释 —— ★ 这是消除幻觉最有效的一个接口

    例：给 dwd_tu_order_all.status 写上
        "订单状态：2待接单 3已接单 4配送中 5已送达 6已完成 7已取消"
    模型就不会再把"完单量"写成 status=7。
    """
    config_store.set_column_comment(table, column, body.comment)
    _clear_gaps_cache()            # ★ 补完这条注释，缺口清单里就该少一条
    return {"ok": True, "table": table, "column": column,
            "message": "已保存。★ 点『重建索引』后生效"}


@app.delete("/api/kb/column/{table}/{column}")
def api_kb_delete_column_comment(table: str, column: str):
    """删除字段的人工注释（回到"无人工注释"状态，数据库自带的注释会重新显示）"""
    config_store.delete_column_comment(table, column)
    _clear_gaps_cache()            # 清掉注释 → 这个字段可能重新变成缺口
    return {"ok": True, "table": table, "column": column,
            "message": "已清除。★ 点『重建索引』后生效"}


# ═══════════════════════════════════════════════════════════
# ⑥-c ★ 数据字典接口 —— "编码 → 中文含义"
# ═══════════════════════════════════════════════════════════
#  【它解决什么问题？】
#    表结构告诉模型"有个字段叫 app_entity_source"，
#    但没告诉它"101 是车拖车、201 是慧拉车"。
#    缺了这层，模型只能猜编码 —— 猜出来的是"能跑但全错"的 SQL。
#
#  【为什么可以一键导入？】
#    公司数仓里本来就有字典表（config.DICT_TABLE，默认 ods_sys_dict_data_all），
#    存着 99 个字典类型、近千条"编码→中文"。手抄不现实，直接导。
# ═══════════════════════════════════════════════════════════
@app.get("/api/kb/dicts")
def api_kb_dicts():
    """列出全部数据字典（含已停用的，管理页面要能看到才能重新打开）"""
    return config_store.get_dict_entries(enabled_only=False)


@app.put("/api/kb/dict/{name}")
def api_kb_upsert_dict(name: str, body: DictBody):
    """新增或修改一个数据字典（不传 enabled，改取值不会动开放状态）"""
    config_store.upsert_dict_entry({
        "name": name,
        "dict_type": body.dict_type,
        "field_hint": body.field_hint,
        "keywords": body.keywords,
        "items": body.items,
    })
    _clear_gaps_cache()            # 配了字典 → 对应字段就不再算缺口
    return {"ok": True, "name": name, "count": len(body.items),
            "message": "已保存。★ 点『重建索引』后生效"}


@app.put("/api/kb/dict/{name}/enabled")
def api_kb_set_dict_enabled(name: str, body: DictEnabledBody):
    """开放 / 停用一个数据字典"""
    if not config_store.set_dict_enabled(name, body.enabled):
        raise HTTPException(status_code=404, detail=f"没找到字典：{name}")
    _clear_gaps_cache()            # 停用字典 = 那个字段又变成缺口了
    return {"ok": True, "name": name, "enabled": body.enabled,
            "message": "已切换。★ 点『重建索引』后生效"}


@app.delete("/api/kb/dict/{name}")
def api_kb_delete_dict(name: str):
    """删除一个数据字典"""
    if not config_store.delete_dict_entry(name):
        raise HTTPException(status_code=404, detail=f"没找到字典：{name}")
    _clear_gaps_cache()            # 字典没了 → 对应字段可能重新变成缺口
    return {"ok": True, "name": name, "message": "已删除。★ 点『重建索引』后生效"}


@app.get("/api/kb/dict-sources")
def api_kb_dict_sources():
    """
    列出【数据库里可导入的字典类型】

    ★ 只返回"类型 + 条数 + 样例"，不返回全部取值：
      99 个类型的取值加起来近千条，先列类型（每类一行），
      用户在页面选中哪一个，再调下面的 import 去取那个类型的取值。
    """
    try:
        sources = db.list_dict_types()
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f"读取字典源表失败（{config.DICT_TABLE}）：{e}")
    # 标出哪些已经导入过了，避免重复导入
    imported = {d["dict_type"] for d in config_store.get_dict_entries(enabled_only=False)
                if d["dict_type"]}
    for s in sources:
        s["imported"] = s["dict_type"] in imported
    return {"table": config.DICT_TABLE, "sources": sources}


@app.post("/api/kb/dict/import")
def api_kb_import_dict(body: DictImportBody):
    """
    从数据库字典表一键导入某个 dict_type 的全部取值

    ★ 已存在同名条目时：更新取值，但【保留】它原来的 field_hint / keywords / 开放状态。
      为什么？因为 field_hint（这个字典用在哪个字段）是人工判断的结果，
      重新导入不该把它冲掉 —— 那是这个知识里最值钱的部分。
    """
    items = db.fetch_dict_items(body.dict_type)
    if not items:
        raise HTTPException(status_code=404,
                            detail=f"字典类型 {body.dict_type} 没有取到任何取值")

    name = (body.name or "").strip() or body.dict_type
    old = next((d for d in config_store.get_dict_entries(enabled_only=False)
                if d["name"] == name), None)

    config_store.upsert_dict_entry({
        "name": name,
        "dict_type": body.dict_type,
        "field_hint": body.field_hint or (old["field_hint"] if old else ""),
        "keywords": body.keywords or (old["keywords"] if old else []) or [name],
        "items": items,
    })
    _clear_gaps_cache()            # 导入了字典 → 对应字段就不再算缺口
    return {"ok": True, "name": name, "dict_type": body.dict_type,
            "count": len(items), "updated": bool(old),
            "message": f"已导入 {len(items)} 条取值。★ 记得填『适用字段』，再点『重建索引』"}


@app.get("/api/kb/table-columns")
def api_kb_table_columns(table: str = ""):
    """
    ★ 列出某张表的字段（给"从数据表导入"面板用）

    让用户选编码列 / 含义列时不用盲猜字段名。
    """
    if not table:
        raise HTTPException(status_code=400, detail="请传 table 参数")
    try:
        cols = db.list_columns(table)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"读取表字段失败：{e}")
    return {"table": table, "columns": [{"name": c, "type": t} for c, t in cols]}


@app.post("/api/kb/dict/import-table")
def api_kb_import_dict_from_table(body: TableImportBody):
    """
    ★ 从任意业务表一键导入一列取值作为数据字典

    适用场景：像 tu_record_type 这种"枚举表"，它本身就存着 (id, 含义)，
    但不在公司字典源表里（config.DICT_TABLE），手抄不现实 —— 直接查出来。

    ★ 已存在同名条目时：更新取值，但【保留】原来的 field_hint / keywords / 开放状态。
       （取值可重导，但"用在哪个字段"是人工判断，不该被冲掉。）
    """
    items = db.import_table_as_dict(
        body.table, body.value_col, body.label_col or None, body.limit)
    if not items:
        raise HTTPException(
            status_code=404,
            detail=f"表 {body.table} 的 {body.value_col} 没有取到任何去重取值")

    # dict_type 用 table:表.列 标记来源，便于溯源 / 重新导入，且不会和中央字典源表撞名
    dict_type = f"table:{body.table}.{body.value_col}"
    name = (body.name or "").strip() or f"{body.table}.{body.value_col}"
    old = next((d for d in config_store.get_dict_entries(enabled_only=False)
                if d["name"] == name), None)

    config_store.upsert_dict_entry({
        "name": name,
        "dict_type": dict_type,
        "field_hint": body.field_hint or (old["field_hint"] if old else ""),
        "keywords": body.keywords or (old["keywords"] if old else []) or [name],
        "items": items,
    })
    _clear_gaps_cache()            # 导入了字典 → 对应字段就不再算缺口
    return {"ok": True, "name": name, "dict_type": dict_type,
            "count": len(items), "updated": bool(old),
            "message": f"已从 {body.table} 导入 {len(items)} 条取值。"
                       f"★ 记得填『适用字段』，再点『重建索引』"}


# ═══════════════════════════════════════════════════════════
# ⑥-d ★ 业务实体 / 关系 / 文档 管理接口（Semantic Layer 的 P2/P4 注册表）
# ═══════════════════════════════════════════════════════════
@app.get("/api/kb/entities")
def api_kb_entities():
    """列出全部业务实体（含停用，管理页要能看到才能重新打开）"""
    return config_store.get_entities()


@app.put("/api/kb/entities/{name}")
def api_kb_upsert_entity(name: str, body: EntityBody):
    """
    新增/修改一个业务实体

    ★ 人工保存一律标 source='manual' —— 这样 AI 以后就不会再覆盖它。
      这正是"AI 起草 → 人复核 → 转人工版"这条链路的落点：
      人只要点开改一遍（哪怕只改一个字），这条口径就归人了。
    """
    config_store.upsert_entity({
        "name": name, "table_name": body.table_name, "grain": body.grain,
        "comment": body.comment, "keywords": body.keywords,
        "enabled": body.enabled,
    }, source="manual")
    # ★ 编辑改名：先把新名写进去，再删旧名 —— 顺序不能反，
    #   否则中途失败就两头都没有了（宁可留个重复，也不能丢数据）。
    renamed_from = ""
    if body.old_name and body.old_name != name:
        if config_store.delete_entity(body.old_name):
            renamed_from = body.old_name
    return {"ok": True, "name": name, "renamed_from": renamed_from,
            "message": "已保存。★ 点『重建索引』后生效"}


@app.delete("/api/kb/entities/{name}")
def api_kb_delete_entity(name: str):
    """删除一个业务实体"""
    if not config_store.delete_entity(name):
        raise HTTPException(status_code=404, detail=f"没找到实体：{name}")
    return {"ok": True, "name": name, "message": "已删除。★ 点『重建索引』后生效"}


@app.get("/api/kb/relationships")
def api_kb_relationships():
    """列出全部关联/Join 规则（含停用）"""
    return config_store.get_relationships(enabled_only=False)


@app.post("/api/kb/relationships")
def api_kb_upsert_relationship(body: RelationshipBody):
    """新增/修改一条关联规则（按 from_entity+to_entity 幂等）"""
    config_store.upsert_relationship({
        "from_entity": body.from_entity, "to_entity": body.to_entity,
        "join_sql": body.join_sql, "join_type": body.join_type,
        "cardinality": body.cardinality, "pre_agg_required": body.pre_agg_required,
        "rule_note": body.rule_note, "enabled": body.enabled,
    })
    # ★ 编辑时若改了「左右实体」，新写入的是一个【新行】，原 id 那行会残留 → 清理掉。
    #   ★ 绝不能无条件删除：若左右没变，upsert 是【原地更新同一行】，
    #     此时删 old_id 等于把刚保存的规则删掉。所以先比对键。
    removed_id = None
    if body.old_id:
        olds = [r for r in config_store.get_relationships(enabled_only=False)
                if r["id"] == body.old_id]
        if olds and (olds[0]["from_entity"], olds[0]["to_entity"]) != \
                (body.from_entity, body.to_entity):
            if config_store.delete_relationship(body.old_id):
                removed_id = body.old_id
    return {"ok": True, "removed_id": removed_id,
            "message": "已保存。★ 点『重建索引』后生效"}


@app.delete("/api/kb/relationships/{rel_id}")
def api_kb_delete_relationship(rel_id: int):
    """删除一条关联规则（按 id）"""
    if not config_store.delete_relationship(rel_id):
        raise HTTPException(status_code=404, detail=f"没找到关系：id={rel_id}")
    return {"ok": True, "id": rel_id, "message": "已删除。★ 点『重建索引』后生效"}


@app.get("/api/kb/docs")
def api_kb_docs():
    """列出全部业务文档（P4，含停用）"""
    return config_store.get_kb_docs(enabled_only=False)


@app.post("/api/kb/docs")
def api_kb_upsert_doc(body: KbDocBody):
    """新增/修改一条业务文档（P4）"""
    # ★ 带 id → 就地更新同一条（支持改标题，不会产生重复文档）；不带 → 新增
    config_store.upsert_kb_doc({
        "id": body.id, "title": body.title, "content": body.content,
        "keywords": body.keywords, "priority": body.priority, "enabled": body.enabled,
    })
    return {"ok": True, "id": body.id,
            "message": ("已更新。★ 点『重建索引』后生效" if body.id else
                        "已保存。★ 点『重建索引』后生效")}


@app.delete("/api/kb/docs/{doc_id}")
def api_kb_delete_doc(doc_id: int):
    """删除一条业务文档（按 id）"""
    if not config_store.delete_kb_doc(doc_id):
        raise HTTPException(status_code=404, detail=f"没找到文档：id={doc_id}")
    return {"ok": True, "id": doc_id, "message": "已删除。★ 点『重建索引』后生效"}


# ═══════════════════════════════════════════════════════════
# ⑥-e ★ AI 自动整合录入（用户输入口径逻辑 → AI 分类 → 自动落库并判优先级）
# ═══════════════════════════════════════════════════════════
@app.post("/api/kb/ai-ingest")
def api_ai_ingest(body: IngestBody):
    """把用户输入的一段口径逻辑解析并录入知识库"""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="输入不能为空")
    try:
        results = ai_ingest.ingest(text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI 解析失败：{e}")
    return {
        "ok": True,
        "results": results,
        "count": len(results),
        "message": f"已解析并录入 {len(results)} 条知识。★ 记得点『重建索引』生效",
    }


@app.post("/api/kb/ingest-sql")
def api_ingest_sql(body: SqlIngestBody):
    """★ SQL 投喂：管理员粘贴「取数问题 + 标准 SQL」→ AI 提炼为可复用知识并落库。

    ★ 为什么要单独一个入口（而不是让管理员自己写口径文字）？
      日常取数里，最可靠的知识载体其实是一条【已经跑对的标准 SQL】：
      表怎么选、字段怎么算、条件怎么过滤、时间用哪个字段，全在里面。
      但 SQL 不能直接进知识库（模型看不到"这条为什么对"）。
      所以这里做一步翻译：问题 + SQL → 指标/实体/关联/字段注释/字典/口径文档。
    """
    q = (body.question or "").strip()
    sql = (body.sql or "").strip()
    if not q or not sql:
        raise HTTPException(status_code=400, detail="取数问题与标准 SQL 都不能为空")
    try:
        results = ai_ingest.ingest_sql(q, sql, body.note or "")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI 解析失败：{e}")
    return {
        "ok": True,
        "results": results,
        "count": len(results),
        "message": f"已从该 SQL 提炼并录入 {len(results)} 条知识。★ 记得点『重建索引』生效",
    }


@app.post("/api/kb/suggest-join")
def api_suggest_join(body: SuggestJoinBody):
    """★ 关联/Join 规则：按左右表（或实体名）推荐关联字段，供前端一键拼出 join_sql。

    ★ 为什么要有它？
      手写 join_sql 又慢又容易错 —— 最典型的就是拿两张表各自的主键 id 去关联
      （曾导致过 `t.id = r.id` 的错误）。这里把「选表 → 推荐关联字段 → 确认」做成流程：
      表名自动带入，人只需要确认用哪个字段关联。
    """
    left = (body.left or "").strip()
    right = (body.right or "").strip()
    if not left or not right:
        raise HTTPException(status_code=400, detail="左右表/实体都不能为空")
    try:
        return join_suggest.suggest_join(left, right, body.hint or "")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"推荐失败：{e}")


@app.put("/api/kb/ingest-item")
def api_update_ingest_item(body: IngestItemBody):
    """编辑 AI 录入结果里的某条知识，重新落库"""
    r = ai_ingest.apply_item(body.type, body.data)
    return {"ok": True, "result": r, "message": "已更新。★ 记得点『重建索引』生效"}


@app.delete("/api/kb/ingest-item")
def api_delete_ingest_item(type: str, key: str = ""):
    """删除 AI 录入结果里的某条知识"""
    if not ai_ingest.delete_item(type, key):
        raise HTTPException(status_code=404, detail=f"没找到要删除的知识（type={type}, key={key}）")
    return {"ok": True, "message": "已删除。★ 记得点『重建索引』生效"}


# ═══════════════════════════════════════════════════════════
# ⑥-f ★ 全局知识检索（跨指标/实体/关系/字典/字段注释/文档）
# ═══════════════════════════════════════════════════════════
@app.get("/api/kb/search")
def api_kb_search(q: str = ""):
    """按关键词跨所有知识类型检索"""
    q = (q or "").strip().lower()
    if not q:
        return {"query": q, "count": 0, "results": []}

    results = []
    for m in config_store.get_metrics(enabled_only=False):
        if q in m["name"].lower() or q in m.get("definition", "").lower() \
                or any(q in k.lower() for k in (m.get("keywords") or [])):
            results.append({"type": "metric", "priority": "P1", "name": m["name"],
                            "desc": (m.get("definition") or "")[:80]})
    for e in config_store.get_entities():
        if q in e["name"].lower() or q in e.get("table_name", "").lower() \
                or q in e.get("comment", "").lower():
            results.append({"type": "entity", "priority": "P2", "name": e["name"],
                            "desc": e.get("table_name", "")})
    for r in config_store.get_relationships(enabled_only=False):
        if q in r["from_entity"].lower() or q in r["to_entity"].lower() \
                or q in r.get("join_sql", "").lower():
            results.append({"type": "relationship", "priority": "P2",
                            "name": f"{r['from_entity']}→{r['to_entity']}",
                            "desc": r.get("join_sql", "")})
    for d in config_store.get_dict_entries(enabled_only=False):
        if q in d["name"].lower() or q in d.get("field_hint", "").lower() \
                or any(q in k.lower() for k in (d.get("keywords") or [])):
            results.append({"type": "dict", "priority": "P3", "name": d["name"],
                            "desc": d.get("field_hint", "")})
    for k, v in config_store.get_column_comments().items():
        if q in k.lower() or q in v.lower():
            results.append({"type": "column_comment", "priority": "P3", "name": k,
                            "desc": v[:80]})
    for doc in config_store.get_kb_docs(enabled_only=False):
        if q in doc["title"].lower() or q in doc.get("content", "").lower():
            results.append({"type": "doc", "priority": "P4", "name": doc["title"],
                            "desc": (doc.get("content") or "")[:80]})

    return {"query": q, "count": len(results), "results": results}


# ═══════════════════════════════════════════════════════════
# ⑥-c ★ 知识优先级可视化（P0~P4 按层级聚合，供前端展示文本内容）
# ═══════════════════════════════════════════════════════════
@app.get("/api/kb/priorities")
def api_kb_priorities():
    """把知识库按 P0~P4 优先级聚合，供「知识优先级」可视化页展示各层级文本内容。"""
    # ── P0 系统规则（Prompt 配置，硬约束）──
    p0 = []
    for key, _ in config_store.list_prompts():
        try:
            content = config_store.get_prompt(key)
        except KeyError:
            continue
        p0.append({"title": key, "text": content or "（空）",
                   "meta": {"type": "系统规则"}})

    # ── P1 指标定义（Metric Registry，权威口径）──
    p1 = []
    for m in config_store.get_metrics(enabled_only=False):
        text = m.get("definition") or ""
        if m.get("doc"):
            text = (text + "\n\n补充说明：\n" + m["doc"]) if text else m["doc"]
        p1.append({"title": m["name"], "text": text or "（无定义）",
                   "meta": {"开放": "是" if m["enabled"] else "否",
                            "关联表": m.get("tables") or [],
                            "关键词": m.get("keywords") or []}})

    # ── P2 数据实体 / 表关系 / Join 规则 ──
    p2 = []
    for e in knowledge.get_entities():
        text = e.get("comment") or ""
        extra = []
        if e.get("table_name"): extra.append(f"物理表：{e['table_name']}")
        if e.get("grain"): extra.append(f"粒度：{e['grain']}")
        if extra:
            text = (text + "\n\n" + "；".join(extra)) if text else "；".join(extra)
        p2.append({"title": "实体：" + e["name"], "text": text or "（无说明）",
                   "meta": {"type": "实体", "关键词": e.get("keywords") or []}})
    for r in knowledge.get_relationships(enabled_only=False):
        parts = [f"from {r['from_entity']} → to {r['to_entity']}",
                 f"类型 {r['join_type']} / 基数 {r['cardinality']}",
                 f"预聚合：{'需要' if r['pre_agg_required'] else '不需要'}",
                 f"启用：{'是' if r['enabled'] else '否'}"]
        text = r.get("join_sql") or ""
        if r.get("rule_note"):
            text = (text + "\n\n规则说明：" + r["rule_note"]) if text else ("规则说明：" + r["rule_note"])
        p2.append({"title": f"关系：{r['from_entity']} → {r['to_entity']}",
                   "text": text or "（无 JOIN SQL）",
                   "meta": {"type": "Join规则", "属性": parts}})

    # ── P3 表字段 Schema（表注释 + 字段注释）──
    p3 = []
    for t, c in config_store.get_table_comments().items():
        p3.append({"title": "表：" + t, "text": c or "（无注释）",
                   "meta": {"type": "表注释"}})
    for key, c in config_store.get_column_comments().items():
        p3.append({"title": "字段：" + key, "text": c or "（无注释）",
                   "meta": {"type": "字段注释"}})

    # ── P4 普通业务文档（仅供参考）──
    p4 = []
    for d in config_store.get_kb_docs(enabled_only=False):
        p4.append({"title": d["title"] or ("文档#" + str(d["id"])),
                   "text": d.get("content") or "（无内容）",
                   "meta": {"开放": "是" if d["enabled"] else "否",
                            "优先级": d.get("priority", 4),
                            "关键词": d.get("keywords") or []}})

    tiers = [
        {"tier": 0, "name": "P0 系统规则",
         "desc": "最高优先级，硬约束。RAG 上下文与低优先级知识均不得覆盖。",
         "items": p0},
        {"tier": 1, "name": "P1 指标定义",
         "desc": "权威业务口径来源。模型必须按【MUST】口径计算，不得自行猜测。",
         "items": p1},
        {"tier": 2, "name": "P2 数据实体 / 表关系 / Join 规则",
         "desc": "实体映射与 Join 铁律，1:N 明细表须先预聚合再 JOIN。",
         "items": p2},
        {"tier": 3, "name": "P3 表字段 Schema",
         "desc": "表/字段业务注释，用于 Schema 检索与字段含义澄清。",
         "items": p3},
        {"tier": 4, "name": "P4 普通业务文档",
         "desc": "仅作补充上下文，仅供参考，不得覆盖 P0~P2。", "items": p4},
    ]
    return {"tiers": tiers,
            "counts": {f"p{t['tier']}": len(t["items"]) for t in tiers}}


# ═══════════════════════════════════════════════════════════
# ⑥-b ★ 知识库导出
# ═══════════════════════════════════════════════════════════
@app.get("/api/export")
def api_export(format: str = "json", scope: str = "all"):
    """
    导出知识库（浏览器直接下载文件）

    参数：
        format = json（结构化，备份/迁移） | md（Markdown，评审/分享）
        scope  = all（完整，含数据库自带注释） | manual（只导出人工维护的）

    ★ 返回值直接带 Content-Disposition，浏览器会当文件下载而不是打开。
      中文文件名用 RFC 5987 写进 filename*，同时给一个 ascii 兜底名 ——
      老客户端不认 filename*，只给中文名的话会得到一个乱码名或默认名。
    """
    fmt = "json" if str(format).lower() == "json" else "md"
    sc = "manual" if str(scope).lower() == "manual" else "all"

    data = exporter.collect(sc)                      # 汇总
    if fmt == "json":
        content = exporter.to_json(data)
        media = "application/json; charset=utf-8"
    else:
        content = exporter.to_markdown(data)
        media = "text/markdown; charset=utf-8"

    ascii_name, cn_name = exporter.filenames(sc, fmt)
    headers = {
        "Content-Disposition":
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(cn_name)}",
        # ★ 让前端能读到真实文件名（默认跨域/同源下 JS 读不到这个头）
        "Access-Control-Expose-Headers": "Content-Disposition",
    }
    return Response(content=content, media_type=media, headers=headers)


# ═══════════════════════════════════════════════════════════
# ⑥-c ★ 知识缺口扫描
# ═══════════════════════════════════════════════════════════
_GAPS_CACHE = {"at": 0.0, "data": None}
_GAPS_TTL = 180                                  # 缓存有效期（秒）


def _clear_gaps_cache():
    """
    知识库一改，缺口清单就过期了 —— 立刻作废，别让用户看到旧结果

    ★ 为什么必须清？
      用户补完一条注释、回头一看清单还是列的它，就会以为"没生效"，
      然后开始怀疑整个页面 —— 这种"看起来没反应"最伤信任。
    """
    _GAPS_CACHE["at"] = 0.0
    _GAPS_CACHE["data"] = None


@app.get("/api/kb/gaps")
def api_kb_gaps(refresh: bool = False):
    _ensure_db()
    """
    扫描知识缺口：哪些枚举字段最可能让模型算错

    ★ 为什么要缓存？
      一次扫描要对每张表跑几批 uniqExact，实测约 5 秒。
      用户在模块间切来切去不该每次都等 5 秒，所以结果缓存 3 分钟；
      任何一次知识库写入都会立刻作废缓存（见 _clear_gaps_cache），
      所以不会出现"改完了但清单没变"的情况。
    """
    now = time.time()
    if not refresh and _GAPS_CACHE["data"] is not None and now - _GAPS_CACHE["at"] < _GAPS_TTL:
        return _GAPS_CACHE["data"]
    data = gaps.scan()
    _GAPS_CACHE["at"] = now
    _GAPS_CACHE["data"] = data
    return data


# ═══════════════════════════════════════════════════════════
# ⑥-d ★ 问题反馈（错例复盘）
# ═══════════════════════════════════════════════════════════
#  【它补的是哪一环？】
#    「知识缺口」是机器扫出来的（枚举字段没写清取值），
#    「问题反馈」是人踩出来的（这个问题答错了）。两者都要：
#    机器扫不出"模型把车拖车当成了公司名"这种错，
#    人也想不到去逐张表检查每个枚举字段。
#
#  【为什么提交时要把上下文一起存？】
#    复盘最怕"只记得结果不对，想不起问了什么、SQL 长什么样"。
#    所以问题、SQL、结果、命中的表/指标/字典全部一起存下来。
class FeedbackBody(BaseModel):
    question: str = ""                    # 用户问的原话
    sql: str = ""                         # 当时生成的 SQL
    result: str = ""                      # 结果摘要
    exec_ok: bool = False                 # 当时是否执行成功
    reason: str = "other"                 # 错误类型（见 config_store.FEEDBACK_REASONS）
    detail: str = ""                      # 用户补充说明
    tables: list[str] = []                # 那时命中的表
    metrics: list[str] = []               # 那时命中的指标
    dicts: list[str] = []                 # 那时命中的字典


class FeedbackUpdateBody(BaseModel):
    status: str | None = None             # pending / resolved / ignored
    note: str | None = None               # 处理备注（改了哪条知识）


@app.post("/api/feedback")
def api_add_feedback(body: FeedbackBody):
    """提交一条问题反馈"""
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="问题不能为空")
    if body.reason not in config_store.FEEDBACK_REASONS:
        raise HTTPException(status_code=400, detail=f"未知的错误类型：{body.reason}")
    fid = config_store.add_feedback(body.model_dump())
    return {"ok": True, "id": fid,
            "message": "已记录。理由写清楚了，下次复盘就知道该补哪条知识"}


@app.get("/api/feedback")
def api_list_feedback(status: str | None = None):
    """
    反馈列表 + 错误类型字典 + 各状态计数

    ★ 为什么把 reasons 一起返回？
      前端就不用再抄一份错误类型的文案了（抄两份必然有一天不一致），
      将来加类型只改后端一处。
    """
    items = config_store.get_feedbacks(status=status or None)
    all_items = config_store.get_feedbacks()          # 计数看全量，不看过滤后的
    return {
        "reasons": config_store.FEEDBACK_REASONS,
        "items": items,
        "stats": {
            "total": len(all_items),
            "pending": sum(1 for i in all_items if i["status"] == "pending"),
            "resolved": sum(1 for i in all_items if i["status"] == "resolved"),
            "ignored": sum(1 for i in all_items if i["status"] == "ignored"),
        },
    }


@app.put("/api/feedback/{fid}")
def api_update_feedback(fid: int, body: FeedbackUpdateBody):
    """标记已处理 / 忽略，或补一句处理备注"""
    if body.status is not None and body.status not in ("pending", "resolved", "ignored"):
        raise HTTPException(status_code=400, detail=f"未知状态：{body.status}")
    if not config_store.update_feedback(fid, status=body.status, note=body.note):
        raise HTTPException(status_code=404, detail=f"没找到反馈：{fid}")
    return {"ok": True, "id": fid, "message": "已更新"}


@app.delete("/api/feedback/{fid}")
def api_delete_feedback(fid: int):
    """删除一条反馈（确认是误报时用）"""
    if not config_store.delete_feedback(fid):
        raise HTTPException(status_code=404, detail=f"没找到反馈：{fid}")
    return {"ok": True, "id": fid, "message": "已删除"}


# ═══════════════════════════════════════════════════════════
# ⑥-e ★ 优化建议（把反馈翻译成"该改哪条知识"）
# ═══════════════════════════════════════════════════════════
#  【补的是哪一环？】
#    反馈记下来了，但"记录"本身不会让系统变好 ——
#    得有人把它翻译成"该改哪条知识、改成什么样"。
#    这一步以前全压在人的脑子里，现在让模型先给一版草稿。
#
#  【为什么生成和应用要分成两个接口？】
#    生成是"机器提议"，应用是"人拍板"。分开之后责任清晰：
#    改错了是人改的，不是机器偷偷改的。
#    知识库是要长期维护的资产，任何改动都必须有人负责。
class SuggestBody(BaseModel):
    feedback_ids: list[int] = []          # 空 = 全部待处理的反馈


class SuggestApplyBody(BaseModel):
    kind: str                             # prompt / column_comment / metric
    target: str                           # role / sql_rules / "表.字段" / 指标名
    value: str                            # 用户确认（或改过）后的内容
    feedback_ids: list[int] = []
    note: str = ""                        # 处理备注


@app.post("/api/suggest")
def api_suggest(body: SuggestBody):
    """
    根据反馈生成优化建议（不落库，纯草稿）

    ★ 为什么建议不存库？
      建议是"草稿"—— 用户看过、改过、应用之后，它就以正式知识的形式存在了
      （写进了 prompt / 字段注释 / 指标口径）。
      再单独存一份"曾经建议过什么"，只会变成没人看的历史垃圾。
    """
    try:
        return suggest.generate(feedback_ids=body.feedback_ids or None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成建议失败：{str(e)[:200]}")


@app.post("/api/suggest/apply")
def api_suggest_apply(body: SuggestApplyBody):
    """应用一条建议（用户确认/修改后调用）"""
    try:
        r = suggest.apply_suggestion(
            body.kind, body.target, body.value,
            feedback_ids=body.feedback_ids, note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"应用失败：{str(e)[:200]}")

    _clear_gaps_cache()          # 知识改了 → 缺口清单作废
    r["message"] = ("已应用。★ 记得点右上角『重建索引』—— 不改索引，模型还是看旧知识")
    return r


# ═══════════════════════════════════════════════════════════
# ⑥-f ★ 取数历史 + 取数审核
# ═══════════════════════════════════════════════════════════
#  【为什么需要"审核"这一环？】
#    用户拿到一个数字，其实没有能力判断口径对不对 ——
#    "完单率 26.5%" 看起来很正常，但如果口径取错了（比如把取消单算进分母），
#    数字依然是 26.5%，只是它是错的。用户看不出，还会拿它去汇报。
#    所以要有一条通道：用户说"请帮我看看这个数"，管理员看完给明确结论。
#    审核不通过时给意见，用户就知道这个数不能用 —— 这比"静默错"安全得多。
#
#  【权限边界】
#    普通用户：看自己的历史 / 提交审核 / 催办 / 删自己的记录
#    管理员  ：看所有人的历史 / 给审核结论 / 删任意记录
#    —— 判断写在接口里（因为要按"记录归属"而不是"路径"来判）。
class ReviewBody(BaseModel):
    status: str                            # approved / rejected
    note: str = ""                         # 审核意见（建议写清口径对不对）


def _current_user(request: Request):
    """当前登录用户（中间件已保证存在）"""
    return getattr(request.state, "user", None) or {}


def _can_view(record, user):
    """能看这条记录吗：本人 or 管理员"""
    return bool(record) and (record["username"] == user.get("username")
                             or user.get("role") == "admin")


def _enrich_records(items):
    """
    给记录补两个"展示用"字段：

    ① display_name —— 审核页要显示姓名，只给登录名（liuhao）审核人根本对不上是谁。
    ② sql_formatted —— 排版后的 SQL。审核要读懂 SQL，一行拉到底根本没法看。

    ★ 为什么在这里算而不是入库时存？
      sql_formatted 是【展示层】产物（跟着 guard.format_sql 的实现变）。
      入库时算就等于把"当时的排版结果"固化了，将来排版逻辑改好，历史记录也享受不到。
      display_name 同理 —— 用户改显示名后，历史记录里应该显示新名字。
    """
    names = {u["username"]: (u.get("display_name") or u["username"])
             for u in config_store.list_users()}
    out = []
    for it in items:
        it = dict(it)
        it["display_name"] = names.get(it.get("username"), it.get("username") or "")
        try:
            it["sql_formatted"] = guard.format_sql(it.get("sql") or "")
        except Exception:
            it["sql_formatted"] = it.get("sql") or ""
        out.append(it)
    return out


@app.get("/api/history")
def api_history(request: Request, limit: int = 50, scope: str = "mine"):
    """
    取数历史

    scope=mine（默认）看自己的；scope=all 只有管理员能用。
    """
    user = _current_user(request)
    if scope == "all" and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有管理员能查看全员取数历史")
    username = None if scope == "all" else user.get("username")
    items = config_store.get_query_records(
        username=username, limit=max(1, min(limit, 200)), pending_first=False)

    # 统计：管理员看全公司待办，普通用户看自己提交的
    if user.get("role") == "admin":
        stats = {"pending": config_store.count_pending_reviews()}
        stats["mine"] = config_store.count_my_submissions(user.get("username"))
    else:
        stats = {"pending": 0, "mine": config_store.count_my_submissions(user.get("username"))}
    return {"items": _enrich_records(items), "stats": stats,
            "status_zh": config_store.REVIEW_STATUS_ZH}


@app.get("/api/history/{rid}")
@app.get("/api/records/{rid}")
def api_get_record(rid: int, request: Request):
    """取单条记录（本人或管理员）。权限收回后按【当前】策略重新校验，不再返回已无权访问的数据。"""
    user = _current_user(request)
    rec = config_store.get_query_record(rid)
    if not _can_view(rec, user):
        raise HTTPException(status_code=404, detail=f"没找到记录：{rid}")
    enriched = _enrich_records([rec])[0]
    if user.get("role") == "admin":
        return enriched
    # 非管理员：按当前权限重校验，DENY 字段的 SQL/结果/结论一律不再返回
    try:
        dec = _recheck_decision(rec, user)
    except Exception:
        dec = None
    if dec is not None and not dec["allowed"]:
        enriched["access_denied"] = True
        enriched["deny_reasons"] = dec["deny_reasons"]
        enriched["sql"] = ""
        enriched["sql_formatted"] = ""
        enriched["columns"] = []
        enriched["result_preview"] = ""
        enriched["answer"] = ""
    return enriched


@app.get("/api/records/{rid}/review")
def api_record_review_status(rid: int, request: Request):
    """
    ★ 轻量接口，专供前端轮询"审核状态有没有变"。

    为什么单独做一个而不复用 /api/records/{id}？
      轮询每几秒一次，返回整条记录（含 SQL、结果摘要）纯属浪费。
      只回审核相关的几个字段，请求和响应都小得多。
    """
    rec = config_store.get_query_record(rid)
    user = _current_user(request)
    if not rec:
        raise HTTPException(status_code=404, detail=f"没找到记录：{rid}")
    if not _can_view(rec, user):
        raise HTTPException(status_code=404, detail=f"没找到记录：{rid}")
    return {
        "id": rec["id"],
        "review_status": rec["review_status"],
        "review_status_zh": rec["review_status_zh"],
        "reviewer": rec["reviewer"],
        "review_note": rec["review_note"],
        "submitted_at": rec["submitted_at"],
        "reviewed_at": rec["reviewed_at"],
        "remind_count": rec["remind_count"],
        "reviewed": rec["review_status"] in ("approved", "rejected"),
    }


@app.post("/api/records/{rid}/submit")
def api_submit_review(rid: int, request: Request):
    """提交审核（只有记录本人能提）"""
    user = _current_user(request)
    try:
        rec = config_store.submit_for_review(rid, user.get("username"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "record": rec,
            "message": "已提交审核。管理员确认后，这个结果的状态会自动更新"}


@app.post("/api/records/{rid}/remind")
def api_remind_review(rid: int, request: Request):
    """提醒管理员审核（本人才能催，只统计次数、不改结论）"""
    user = _current_user(request)
    try:
        rec = config_store.remind_review(rid, user.get("username"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "record": rec,
            "message": f"已提醒管理员（第 {rec['remind_count']} 次）"}


@app.delete("/api/records/{rid}")
def api_delete_record(rid: int, request: Request):
    """删除记录：本人可删自己的，管理员可删任意"""
    user = _current_user(request)
    is_admin = user.get("role") == "admin"
    try:
        ok = config_store.delete_query_record(rid, None if is_admin else user.get("username"))
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"没找到记录：{rid}")
    return {"ok": True, "message": "已删除"}


# ═══════════════════════════════════════════════════════════
# ★ 结果导出（CSV / Excel）
# ═══════════════════════════════════════════════════════════
@app.get("/api/records/{rid}/export")
def api_export_record(rid: int, request: Request, format: str = "csv",
                      max_rows: int = 5000):
    """
    导出某次取数的【结果快照】。

    ★ 为什么不在导出时重新执行 SQL？
      ① 会多出一条执行路径，必须重新过只读/白名单/校验，容易绕过统一闸门；
      ② 用户要的是"当时看到的数"，重跑后数据可能已经变了。
      所以只读 `/api/ask` 成功时落下的快照；没有快照就明确提示。
    """
    rec = config_store.get_query_record(rid)
    if not rec or not _can_view(rec, _current_user(request)):
        raise HTTPException(status_code=404, detail=f"没找到记录：{rid}")
    snap = config_store.get_result_snapshot(rid)
    if not snap:
        raise HTTPException(
            status_code=404,
            detail="该记录没有可导出的结果快照（仅成功取数且已落快照的记录支持）")
    columns = snap.get("columns") or []
    rows = (snap.get("rows") or [])[:max(1, min(int(max_rows or 5000), 10000))]

    # ★ 导出独立鉴权：非管理员按【当前】权限重校验，DENY 列拒绝、MASK 列只导出脱敏值。
    user = _current_user(request)
    if user.get("role") != "admin":
        try:
            dec = _recheck_decision(rec, user, permission.ACTION_EXPORT)
        except Exception:
            dec = None
        if dec is not None and not dec["allowed"]:
            raise HTTPException(
                status_code=403,
                detail="当前账号没有导出该结果中敏感字段的权限，请联系管理员申请。")
        if dec is not None and dec.get("mask_map"):
            rows = permission.mask_rows(columns, rows, dec["mask_map"])

    fmt = (format or "csv").lower()
    stamp = time.strftime("%Y%m%d-%H%M%S")

    if fmt == "csv":
        buf = io.StringIO()
        buf.write("\ufeff")                 # BOM：Excel 打开中文不乱码
        writer = csv.writer(buf)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if v is None else v for v in row])
        data = buf.getvalue().encode("utf-8")
        media = "text/csv; charset=utf-8"
        ext = "csv"
    elif fmt in ("xlsx", "excel"):
        try:
            from openpyxl import Workbook
        except ImportError:
            raise HTTPException(status_code=500,
                                detail="Excel 导出依赖未安装（openpyxl）")
        wb = Workbook()
        ws = wb.active
        ws.title = "查询结果"
        ws.append(columns)
        for row in rows:
            ws.append(["" if v is None else
                       (v if isinstance(v, (int, float, str, bool)) else str(v))
                       for v in row])
        out = io.BytesIO()
        wb.save(out)
        data = out.getvalue()
        media = ("application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")
        ext = "xlsx"
    else:
        raise HTTPException(status_code=400, detail="format 只支持 csv / xlsx")

    name = f"zhishu_{rid}_{stamp}.{ext}"
    return Response(
        content=data, media_type=media,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


# ═══════════════════════════════════════════════════════════
# ★ 收藏（个人维度，当前开放 record）
# ═══════════════════════════════════════════════════════════
@app.get("/api/favorites")
def api_list_favorites(request: Request, object_type: str | None = None):
    user = _current_user(request)
    return {"items": config_store.list_favorites(user.get("username"), object_type)}


@app.get("/api/favorites/records")
def api_list_favorite_records(request: Request):
    """收藏取数页：返回收藏记录的问题、SQL、审核状态（联表 query_records）。"""
    user = _current_user(request)
    return {"items": config_store.list_favorite_records(user.get("username"))}


@app.post("/api/favorites")
def api_add_favorite(body: FavoriteBody, request: Request):
    user = _current_user(request)
    if body.object_type != "record":
        raise HTTPException(status_code=400, detail="暂只支持收藏取数记录")
    try:
        rid = int(body.object_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="object_id 必须是记录 id")
    rec = config_store.get_query_record(rid)
    if not rec or not _can_view(rec, user):
        raise HTTPException(status_code=404, detail="记录不存在或无权收藏")
    added = config_store.add_favorite(user.get("username"), "record", str(rid))
    return {"ok": True, "added": added}


@app.delete("/api/favorites/{object_type}/{object_id}")
def api_remove_favorite(object_type: str, object_id: str, request: Request):
    user = _current_user(request)
    removed = config_store.remove_favorite(user.get("username"), object_type, object_id)
    return {"ok": True, "removed": removed}


@app.get("/api/review/summary")
def api_review_summary(request: Request):
    """角标用：管理员=全公司待审数；普通用户=我自己待审/被驳回数"""
    user = _current_user(request)
    if user.get("role") == "admin":
        return {"role": "admin", "pending": config_store.count_pending_reviews()}
    mine = config_store.count_my_submissions(user.get("username"))
    return {"role": "user", "pending": mine["pending"], "rejected": mine["rejected"]}


@app.get("/api/review/queue")
def api_review_queue(status: str = "pending", limit: int = 100):
    """审核队列（管理员）—— 默认只看待审的"""
    only_sub = status in ("all", "")
    items = config_store.get_query_records(
        None, limit=max(1, min(limit, 200)),
        review_status=None if only_sub else status,
        only_submitted=only_sub,
    )
    return {"items": _enrich_records(items), "pending": config_store.count_pending_reviews(),
            "status_zh": config_store.REVIEW_STATUS_ZH}


@app.put("/api/review/{rid}")
def api_do_review(rid: int, body: ReviewBody, request: Request):
    """
    管理员给出审核结论

    ★ 记名 + 记时 + 记意见：两个月后还能查到"这条是谁在什么时候因为什么否掉的"。
    """
    user = _current_user(request)
    if body.status == "rejected" and not (body.note or "").strip():
        raise HTTPException(status_code=400,
                            detail="判定「不通过」时必须写清原因，否则用户不知道该改什么")
    try:
        rec = config_store.review_record(rid, user.get("username"), body.status, body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "record": rec,
            "message": "审核已通过，用户端状态实时更新" if body.status == "approved"
                       else "已驳回，请按意见修正知识库后让用户重新取数"}


# ═══════════════════════════════════════════════════════════
# ⑦ 索引重建
# ═══════════════════════════════════════════════════════════
@app.post("/api/index/rebuild")
def api_rebuild_index():
    """
    重建索引 —— 改完配置后必须调用
    ★ 为什么必须重建？
      检索用的向量是在"建索引时"算好的。
      改了指标定义/表注释/权限，向量和知识库都变了，必须重算。
    """
    index = rebuild_index()
    return {
        "ok": True,
        "tables": len(index["tables"]),
        "metrics": len(index["metrics"]),
        "dicts": len(index.get("dicts") or []),
        "allowed_tables": index["allowed_tables"],
    }


# ═══════════════════════════════════════════════════════════
# ⑧ 运行状态
# ═══════════════════════════════════════════════════════════
def _status_payload(index, user):
    """构造 /api/status 返回体（纯函数，便于测试）。

    ★ 表清单只给管理员：普通用户只需要知道"有多少张表可用"，
      拿到完整清单等于泄露数仓结构（可用于探测敏感表名）。
    """
    is_admin = bool(user and user.get("role") == "admin")
    payload = {
        "db_type": config.DB_TYPE,
        "db_impl": db.IMPL_NAME,
        "sql_dialect": config.SQL_DIALECT,
        "tables_in_index": len(index["tables"]),
        "metrics_in_index": len(index["metrics"]),
        "dicts_in_index": len(index.get("dicts") or []),
        "allowed_table_count": len(index.get("allowed_tables") or []),
        "llm_model": config.LLM_MODEL,
    }
    if is_admin:
        payload["allowed_tables"] = index["allowed_tables"]
    return payload


@app.get("/api/status")
def api_status(request: Request):
    """给前端显示当前系统状态（普通用户不返回表清单）"""
    return _status_payload(get_index(), getattr(request.state, "user", None))


# ═══════════════════════════════════════════════════════════
# ★ 数据权限管理中心（角色 / 数据域 / 敏感字段 / 字段策略 / 审计 / AI 助手）
#   全部默认走 admin 中间件（_required_role 默认 admin），普通用户仅 /api/permission/my-access。
# ═══════════════════════════════════════════════════════════
class RoleBody(BaseModel):
    display_name: str = ""
    description: str = ""


class UserRolesBody(BaseModel):
    roles: list[str] = []


class DomainBody(BaseModel):
    display_name: str = ""
    description: str = ""
    owner: str = ""
    enabled: int = 1


class SensitiveBody(BaseModel):
    domain_name: str = ""
    table_name: str = ""
    column_name: str = ""
    sensitivity: str = "high"
    aliases: list[str] = []
    note: str = ""
    owner: str = ""
    mapping_status: str = "confirmed"


class PolicyBody(BaseModel):
    subject_type: str = "role"      # role / user
    subject_name: str = ""
    table_name: str = ""
    column_name: str = ""
    access: str = "DENY"            # ALLOW / MASK / DENY
    action: str = "query"           # query / export / both
    mask_rule: str = ""
    domain_name: str = ""
    priority: int = 0
    enabled: int = 1
    change_note: str = ""


class PolicyEnabledBody(BaseModel):
    enabled: int = 1


class AiDraftBody(BaseModel):
    text: str = ""


class AiApplyBody(BaseModel):
    apply: int = 1                  # 1=确认并发布；0=驳回


class RowRuleBody(BaseModel):
    subject_type: str = "role"
    subject_name: str = ""
    table_name: str = ""
    filter_sql: str = ""
    description: str = ""
    enabled: int = 1


class UserAttrsBody(BaseModel):
    attributes: dict = {}


def _perm_meta():
    """字段级权限用的表元数据（SELECT * 展开 + 字段归属）。"""
    idx = get_index()
    return idx.get("tables") or []


def _recheck_decision(rec, user, action=permission.ACTION_QUERY):
    """对历史 SQL 按【当前】权限重新做字段级判定。"""
    return permission.evaluate(rec.get("sql") or "", user, _perm_meta(), action=action)


# ── 总览 ──
@app.get("/api/permission/overview")
def api_permission_overview():
    return {
        "roles": len(config_store.list_roles()),
        "domains": len(config_store.list_data_domains()),
        "sensitive": len(config_store.get_sensitive_resources()),
        "policies": len(config_store.list_permission_policies()),
        "audit": len(config_store.list_permission_audit(limit=1)),
    }


# ── 角色 ──
@app.get("/api/permission/roles")
def api_permission_roles():
    return {"items": config_store.list_roles()}


@app.post("/api/permission/roles")
def api_permission_create_role(body: RoleBody):
    return config_store.upsert_role(body.display_name or "", display_name=body.display_name,
                                    description=body.description)


@app.delete("/api/permission/roles/{name}")
def api_permission_delete_role(name: str):
    return config_store.delete_role(name)


# ── 用户角色绑定 ──
@app.get("/api/permission/user-roles")
def api_permission_user_roles():
    return {"items": config_store.list_user_role_bindings(),
            "users": config_store.list_users()}


@app.put("/api/permission/user-roles/{username}")
def api_permission_set_user_roles(username: str, body: UserRolesBody, request: Request):
    operator = (getattr(request.state, "user", None) or {}).get("username", "")
    return config_store.set_user_roles(username, body.roles, operator)


# ── 数据域 ──
@app.get("/api/permission/domains")
def api_permission_domains():
    return {"items": config_store.list_data_domains()}


@app.put("/api/permission/domains/{name}")
def api_permission_upsert_domain(name: str, body: DomainBody):
    return config_store.upsert_data_domain(name, body.display_name, body.description,
                                           body.owner, body.enabled)


@app.delete("/api/permission/domains/{name}")
def api_permission_delete_domain(name: str):
    return config_store.delete_data_domain(name)


# ── 敏感资源映射 ──
@app.get("/api/permission/sensitive")
def api_permission_sensitive(domain: str = "", table: str = "",
                             confirmed_only: bool = False):
    return {"items": config_store.get_sensitive_resources(
        domain=domain or None, table=table or None, confirmed_only=confirmed_only)}


@app.put("/api/permission/sensitive")
def api_permission_upsert_sensitive(body: SensitiveBody):
    return config_store.upsert_sensitive_resource(
        body.domain_name, body.table_name, body.column_name, body.sensitivity,
        body.aliases, body.note, body.owner, body.mapping_status)


@app.delete("/api/permission/sensitive/{rid}")
def api_permission_delete_sensitive(rid: int):
    return config_store.delete_sensitive_resource(rid)


# ── 字段访问策略 ──
@app.get("/api/permission/policies")
def api_permission_policies(include_disabled: bool = True):
    return {"items": config_store.list_permission_policies(include_disabled)}


@app.put("/api/permission/policies")
def api_permission_upsert_policy(body: PolicyBody, request: Request):
    operator = (getattr(request.state, "user", None) or {}).get("username", "")
    return config_store.upsert_permission_policy(
        body.subject_type, body.subject_name, body.table_name, body.column_name,
        body.access, body.action, body.mask_rule or None, body.domain_name or None,
        body.priority, body.enabled, operator, body.change_note)


@app.post("/api/permission/policies/{pid}/enabled")
def api_permission_toggle_policy(pid: int, body: PolicyEnabledBody, request: Request):
    operator = (getattr(request.state, "user", None) or {}).get("username", "")
    return config_store.set_policy_enabled(pid, body.enabled, operator)


@app.delete("/api/permission/policies/{pid}")
def api_permission_delete_policy(pid: int):
    return config_store.delete_policy(pid)


@app.get("/api/permission/policies/{pid}/versions")
def api_permission_policy_versions(pid: int):
    return {"items": config_store.get_policy_versions(pid)}


# ── 审计 ──
@app.get("/api/permission/audit")
def api_permission_audit(username: str = "", limit: int = 200):
    return {"items": config_store.list_permission_audit(username=username or None, limit=limit)}


# ── 我当前生效权限（普通用户可见）──
@app.get("/api/permission/my-access")
def api_permission_my_access(request: Request):
    user = getattr(request.state, "user", None) or {}
    roles = permission.user_roles(user)
    policies = config_store.get_active_policies()
    mine = [p for p in policies
            if (p["subject_type"] == "role" and p["subject_name"] in roles)
            or (p["subject_type"] == "user" and p["subject_name"] == user.get("username"))]
    sensitive = config_store.get_sensitive_resources()
    effective = []
    for r in sensitive:
        t, c = r["table_name"], r["column_name"]
        acc, rule = permission.effective_access({(t, c)}, mine, t, c)
        effective.append({"table": t, "column": c, "access": acc, "mask_rule": rule,
                          "domain": r.get("domain_name")})
    return {"username": user.get("username"), "roles": roles,
            "policies": mine, "effective": effective}


# ── AI 自然语言配置助手（P2）──
@app.post("/api/permission/ai-draft")
def api_permission_ai_draft(body: AiDraftBody, request: Request):
    user = getattr(request.state, "user", None) or {}
    rules, ambiguities, impact = permission.parse_nl_policy(
        body.text, config_store.get_sensitive_resources(), config_store.list_roles())
    draft_id = config_store.save_ai_draft(
        body.text, rules, ambiguities, impact, user.get("username"))
    return {"ok": True, "draft_id": draft_id, "rules": rules,
            "ambiguities": ambiguities, "impact": impact}


@app.get("/api/permission/ai-drafts")
def api_permission_ai_drafts(status: str = ""):
    return {"items": config_store.list_ai_drafts(status=status or None)}


@app.post("/api/permission/ai-drafts/{draft_id}/apply")
def api_permission_ai_draft_apply(draft_id: int, body: AiApplyBody, request: Request):
    """确认并发布 AI 草稿（把草稿规则真正写入策略表）；驳回则标记 rejected。"""
    user = getattr(request.state, "user", None) or {}
    drafts = config_store.list_ai_drafts()
    draft = next((d for d in drafts if d["id"] == draft_id), None)
    if not draft:
        raise HTTPException(status_code=404, detail=f"草稿不存在：{draft_id}")
    if not body.apply:
        config_store.update_ai_draft_status(draft_id, "rejected", user.get("username"), "驳回")
        return {"ok": True, "status": "rejected"}
    # 有歧义时不允许直接发布（任务书：模糊字段映射必须要求确认）
    if draft.get("ambiguities"):
        raise HTTPException(
            status_code=400,
            detail="草稿存在未解决的字段/语义歧义，须先确认映射后才能发布")
    applied = 0
    for r in (draft.get("parsed") or []):
        config_store.upsert_permission_policy(
            r.get("subject_type", "role"), r.get("subject_name", ""),
            r.get("table_name", ""), r.get("column_name", ""),
            r.get("access", "DENY"), r.get("action", "query"),
            r.get("mask_rule"), r.get("domain_name"),
            created_by=user.get("username"), change_note="AI 助手生成")
        applied += 1
    config_store.update_ai_draft_status(draft_id, "applied", user.get("username"), "已发布")
    return {"ok": True, "status": "applied", "applied": applied}


# ── 行级权限（P3 数据模型，P1 预留）──
@app.get("/api/permission/row-rules")
def api_permission_row_rules():
    return {"items": config_store.list_row_permission_rules()}


@app.put("/api/permission/row-rules")
def api_permission_upsert_row_rule(body: RowRuleBody, request: Request):
    operator = (getattr(request.state, "user", None) or {}).get("username", "")
    return config_store.upsert_row_permission_rule(
        body.subject_type, body.subject_name, body.table_name, body.filter_sql,
        body.description, body.enabled, operator)


# ── 用户已验证属性（行级权限的信任来源，仅管理员设置）──
@app.put("/api/users/{username}/attributes")
def api_set_user_attributes(username: str, body: UserAttrsBody):
    return config_store.set_user_attributes(username, body.attributes or {})


# ═══════════════════════════════════════════════════════════
# 直接运行时的入口（等价于 uvicorn server:app）
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
