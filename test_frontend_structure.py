# -*- coding: utf-8 -*-
"""
test_frontend_structure.py —— 前端结构 测试

★ 背景
  前端原来是 7118 行单文件（HTML + CSS + JS 全在一起），2026-10-29 拆成
  index.html + static/app.css + static/experience.css + static/js/*.js。
  拆分本身是"搬家"，代码逐字节没动 —— 但搬家最容易出的问题不是代码写错，
  而是**少搬了一件、顺序搬反了、路径写错了**，而这三种错误都不会报错，
  只会表现为"页面某个按钮点了没反应"，靠人点很难发现。

★ 所以这个测试守四件事
  ① index.html 引用的每个静态文件都真实存在（防漏文件 / 拼错路径）
  ② 每个 JS 文件语法合法（防切割切在函数中间 / 编码损坏）
  ③ 每个 onclick 里调用的函数真的被某个 JS 定义（防某个模块没加载）
  ④ /static 免登录可访问，且目录穿越拿不到源码（防拆出权限洞）

★ 不测什么
  不测视觉与交互（没有前端测试框架）。这一层只保证"结构完整"，
  业务正确性仍由后端的 unit/config 测试保证。

运行：
    .venv/bin/python test_frontend_structure.py
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

FE = os.path.join(HERE, "frontend")
INDEX = os.path.join(FE, "index.html")

_passed = 0
_failed = []


def ok(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name} {detail}")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


HTML = read(INDEX)
# ★ 注释里的示例代码（如 "用 onclick=\"xxx()\""）不是真实结构，
#   检查前先剥掉 HTML 注释，否则测试会被自己的注释绊倒。
HTML_NC = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)

# ── 从 HTML 里抽引用 ──
CSS_REFS = re.findall(r'<link[^>]+href="(/static/[^"]+\.css)"', HTML)
JS_REFS = re.findall(r'<script[^>]+src="(/static/[^"]+\.js)"', HTML)


def local(url):
    """URL 路径 → 磁盘路径（/static/x → frontend/static/x）"""
    return os.path.join(FE, url.lstrip("/"))


# ═══════════════════════════════════════════════════════════
# ① 引用的文件都存在
# ═══════════════════════════════════════════════════════════
def test_refs_exist():
    ok(len(CSS_REFS) >= 1, "至少引用了一个 CSS", f"实际 {CSS_REFS}")
    ok(len(JS_REFS) >= 10, "脚本按域拆分（≥10 个）", f"实际 {len(JS_REFS)} 个")
    missing = [u for u in CSS_REFS + JS_REFS if not os.path.exists(local(u))]
    ok(not missing, "引用的静态文件全部存在", f"缺失：{missing}")

    # 反向：static 目录里不该有"没人引用"的孤儿文件（拆分后忘记接线）
    referenced = {os.path.basename(u) for u in CSS_REFS + JS_REFS}
    orphans = []
    for root, _, files in os.walk(os.path.join(FE, "static")):
        for fn in files:
            if fn.endswith((".css", ".js")) and fn not in referenced:
                orphans.append(os.path.relpath(os.path.join(root, fn), FE))
    ok(not orphans, "没有未接线的孤儿资源", f"孤儿：{orphans}")


# ═══════════════════════════════════════════════════════════
# ①-b 文档骨架完整
#   ★ 这条是补上的：第一次拆分时，重写文件头注释不小心把
#     <!DOCTYPE>/<html>/<head> 三行弄丢了。浏览器照样能渲染（怪异模式），
#     接口测试也全绿 —— 只有肉眼看才知道不对。所以必须由测试守住。
# ═══════════════════════════════════════════════════════════
def test_document_skeleton():
    ok(HTML.lstrip().lower().startswith("<!doctype html>"), "以 <!DOCTYPE html> 开头",
       repr(HTML[:40]))
    order = ["<html", "<head", "</head", "<body", "</body", "</html"]
    pos = [HTML.find(t) for t in order]
    ok(all(p >= 0 for p in pos), "标签齐全", f"缺失：{[t for t, p in zip(order, pos) if p < 0]}")
    ok(pos == sorted(pos), "标签顺序正确", f"位置 {pos}")
    # 粗查配对：div/section/main 的开闭数量必须一致（漏一个 </div> 会整页错位）
    for tag in ("div", "section", "main", "table", "button"):
        a = len(re.findall(rf"<{tag}[\s>]", HTML_NC))
        b = len(re.findall(rf"</{tag}>", HTML_NC))
        ok(a == b, f"<{tag}> 开闭配对", f"开 {a} / 闭 {b}")


# ═══════════════════════════════════════════════════════════
# ② HTML 里不该再有内联 style / script（拆分要彻底）
# ═══════════════════════════════════════════════════════════
def test_no_inline_code():
    # 允许 HTML 里保留 <style> 之外的样式属性，但不允许整块 <style>
    style_blocks = re.findall(r"<style[\s>]", HTML_NC)
    ok(not style_blocks, "index.html 无内联 <style> 块", f"发现 {len(style_blocks)} 处")
    inline_script = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", HTML_NC)
    ok(not inline_script, "index.html 无内联 <script> 块", f"发现 {len(inline_script)} 处")
    # CSS 一行都不许丢：拆分前 805 行，少于这个数说明搬漏
    css_dir = os.path.join(FE, "static")
    total_css = sum(len(read(os.path.join(css_dir, f)).split("\n"))
                    for f in os.listdir(css_dir) if f.endswith(".css"))
    ok(total_css >= 800, "CSS 总量未因拆分缩水", f"合计 {total_css} 行")
    js_total = 0
    for u in JS_REFS:
        js_total += len(read(local(u)).split("\n"))
    ok(js_total >= 4990, "JS 总量未因拆分缩水", f"合计 {js_total} 行")


# ═══════════════════════════════════════════════════════════
# ③ 每个 onclick 调用的函数都被某个 JS 定义
#    ★ 这条是"模块漏加载"的探测器：少一个 <script src>，
#      代码仍然语法正确、页面仍然能打开，只有那个按钮静默失灵。
# ═══════════════════════════════════════════════════════════
def test_handlers_defined():
    all_js = "\n".join(read(local(u)) for u in JS_REFS)
    # onclick="foo(" / onchange="foo(" ；允许单引号与前置空白
    used = set(re.findall(r'on(?:click|change|input|keydown|keyup|blur|focus)="\s*([A-Za-z_$][\w$]*)\s*\(', HTML_NC))
    # 少数写法是 onclick="if (...) ..." 这类内联语句，不是函数名，排除掉
    used -= {"if", "for", "while", "return", "this", "void", "switch"}
    missing = sorted(u for u in used if not re.search(
        rf"(?:function\s+{re.escape(u)}\s*\(|(?:let|const|var)\s+{re.escape(u)}\s*=)", all_js))
    ok(not missing, "所有内联事件处理器都有定义", f"未定义：{missing}")
    # 阈值不追求精确（页面会增删按钮），只防"正则失配 → 一条都没检测到"的假通过
    ok(len(used) >= 60, "检测到的处理器数量合理", f"实际 {len(used)} 个")


# ═══════════════════════════════════════════════════════════
# ④ 每个 JS 文件语法合法（node --check，最接近浏览器解析）
# ═══════════════════════════════════════════════════════════
def test_js_syntax():
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
    except Exception:
        # 没装 node 不算失败：明确跳过，而不是假装通过
        print("   ⚠ 未安装 node，跳过 JS 语法检查（不是通过，是没测）")
        return
    bad = []
    for u in JS_REFS:
        r = subprocess.run(["node", "--check", local(u)], capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(f"{u}: {r.stderr.strip().splitlines()[:1]}")
    ok(not bad, "所有 JS 通过 node --check", f"失败：{bad}")


# ═══════════════════════════════════════════════════════════
# ⑤ 静态资源免登录可访问 + 目录穿越拿不到源码
# ═══════════════════════════════════════════════════════════
def test_served_publicly():
    # ★ 这一段要起 app，属于"需本地配置库"级别；前四段是纯文件检查。
    #   起不来就明确跳过并说明原因，不伪装成通过。
    try:
        from fastapi.testclient import TestClient
        import server
    except Exception as e:
        print(f"   ⚠ 无法加载 app（{e}），跳过托管检查（不是通过，是没测）")
        return
    c = TestClient(server.app)          # 不带 token

    r = c.get("/")
    ok(r.status_code == 200, "首页可匿名访问", f"status={r.status_code}")

    bad = []
    for u in CSS_REFS + JS_REFS:
        rr = c.get(u)
        if rr.status_code != 200 or not rr.content:
            bad.append(f"{u}→{rr.status_code}")
    ok(not bad, "所有 CSS/JS 免登录可取", f"失败：{bad}")

    # 缓存策略：必须 no-cache（回源校验），否则改了 JS 用户看到的还是旧的
    cc = c.get(JS_REFS[0]).headers.get("cache-control", "")
    ok("no-cache" in cc, "静态资源不让浏览器盲缓存", f"Cache-Control={cc!r}")

    # 目录穿越：绝不能返回 frontend 之外的任何文件
    leaks = []
    for bad_url in ["/static/../server.py", "/static/%2e%2e/server.py",
                    "/static/js/../../agent_config.db", "/static/../.env"]:
        rr = c.get(bad_url)
        body = rr.content[:400]
        if rr.status_code == 200 or b"import os" in rr.content or b"SQLite format" in body:
            leaks.append(f"{bad_url}→{rr.status_code}")
    ok(not leaks, "目录穿越被拦截", f"泄漏：{leaks}")


def main():
    for fn in (test_refs_exist, test_document_skeleton, test_no_inline_code,
               test_handlers_defined, test_js_syntax, test_served_publicly):
        try:
            fn()
        except Exception as e:
            import traceback
            _failed.append(f"{fn.__name__} 异常：{e}\n{traceback.format_exc()}")
    print("=" * 60)
    if _failed:
        print(f"❌ 失败 {len(_failed)} 项（通过 {_passed} 项）")
        for f in _failed:
            print("  ❌", f)
        return 1
    print(f"🎉 全部通过（{_passed} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
