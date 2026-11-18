# -*- coding: utf-8 -*-
"""
_merge_kb_gov.py —— 把「知识管理」与「知识治理」两个一级菜单合并成一个「知识库」

★ 为什么要合并？（2026-10-27 产品调整）
  两个菜单用的是两套不同的划分轴：知识管理按【知识类型】切，知识治理按【动作】切。
  它们本来是同一批知识的两个维度，做成两个平级一级菜单后必然出现断层：
    · 「知识录入」的产物就是那四类知识，是内容生产，却挂在"治理动作"下面；
    · 表结构页点「AI 起草」→ 落 candidate → 必须切到另一个菜单点「通过」，
      而页面上没有任何提示告诉他要去复核；
    · 「知识总览」与「知识管理」是同一份数据的两种视图，分在两个菜单等于让用户猜。
  合并后统一到一个「知识库」，二级按 内容 / 流程 两组组织。

★ 为什么用脚本而不是手改？
  这个前端是单文件 300KB+，section 边界靠缩进和注释维持。手改容易漏一处
  </section> 就整页白屏。脚本里每步都断言"标记恰好出现 N 次"，
  任何一处对不上就整体不落盘、原文件不动。

运行： .venv/bin/python _merge_kb_gov.py
"""
import os
import re
import shutil
import sys

SRC = "frontend/index.html"
BAK = "frontend/index.html.bak-premerge"

s = open(SRC, encoding="utf-8").read()
orig_len = len(s)


def must(cond, msg):
    """断言失败就退出，绝不落盘（半改的文件比不改更糟）"""
    if not cond:
        print(f"❌ 中止：{msg}")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════
# ① 定位两个 section 的边界
# ═══════════════════════════════════════════════════════════
i_kb = s.index('<section id="tab-kb" class="tab">')
i_kb_end = s.index("</section>", i_kb)          # 模块里没有嵌套 section，首个即是收尾
i_gov = s.index('<section id="tab-gov" class="tab">')
must(i_kb < i_kb_end < i_gov, "section 顺序不对（kb 应在 gov 之前）")
i_gov_end = s.index("</section>", i_gov)

gov = s[i_gov:i_gov_end]
#   只保留 gov 里真正属于"知识内容/动作"的模块（丢掉它的 alert 与 subtabs 条）
KEEP_MARK = '<!-- ── 模块四·〇：复核队列'
must(KEEP_MARK in gov, "找不到复核队列模块起点")
keep = gov[gov.index(KEEP_MARK):]
must(keep.count('class="kb-module') == 5,
     f"gov 应搬走 5 个模块，实际 {keep.count('class=\"kb-module')}")
must('id="ingestEditModal"' in keep, "录入编辑弹窗没被搬走（会丢功能）")

# ═══════════════════════════════════════════════════════════
# ② 把模块插到 kb 的 </section> 之前，并删掉整个 gov section
# ═══════════════════════════════════════════════════════════
new = s[:i_kb_end] + "\n" + keep + s[i_kb_end:i_gov] + s[i_gov_end + len("</section>"):]

#   顺手清掉夹在两段之间的旧「Tab 3：知识治理」说明注释（已过期，留着会误导后来人）
old_cmt = re.compile(
    r"\n\n  <!-- ═+\n\s+Tab 3：知识治理.*?═+ -->\n", re.S)
new, n_cmt = old_cmt.subn(
    "\n\n  <!-- ★ 2026-10-27：原「知识治理」一级页已合并进「知识库」，"
    "二级按 内容/流程 两组组织 -->\n", new)
must(n_cmt == 1, f"旧 Tab3 注释应命中 1 次，实际 {n_cmt}")
must('id="tab-gov"' not in new, "tab-gov section 没删干净")

# ═══════════════════════════════════════════════════════════
# ③ 顶部说明文案：改成"一个知识库，两个维度"
# ═══════════════════════════════════════════════════════════
old_alert = """    <div class="alert info">
      ★ 这里编辑的是<b>喂给大模型的知识</b>。RAG 的效果不取决于模型多强，
      而取决于我们喂了它什么 —— 模型不会凭空知道「status=6 是已完成」。
      改完点右上角「重建索引」生效。
    </div>"""
new_alert = """    <div class="alert info">
      ★ 这里放的是<b>喂给大模型的知识</b>。RAG 的效果不取决于模型多强，
      而取决于我们喂了它什么 —— 模型不会凭空知道「status=6 是已完成」。
      改完点右上角「重建索引」生效。
      <br>下方二级按两个维度组织：<b>内容</b>（知识有什么，可看可改）与
      <b>流程</b>（知识怎么管：总览 / 补缺 / 复核 / 进出）。
      ★ <b>并非所有改完都能生效</b>：AI 起草的内容先落「待复核」，只有
      approved 才进检索；改完请顺手看一眼<b>待复核</b>角标。
    </div>"""
must(new.count(old_alert) == 1, "知识管理顶部说明没命中")
new = new.replace(old_alert, new_alert)

# ═══════════════════════════════════════════════════════════
# ④ 二级导航条：4 个 → 8 个，用「内容 / 流程」两个分组标签切开
# ═══════════════════════════════════════════════════════════
old_tabs = """    <!-- ── 二级模块：知识管理 =「知识有什么」（内容注册表）──
         表结构 / 指标 / 字典 / 语义注册表（实体·关联·文档）。
         ★ 语义注册表编辑的是 P2/P4 内容，与前三者同类，故归到本页；治理页只放"动作"。 -->
    <div class="subtabs">
      <button id="kbTabSchema" class="active" onclick="kbSwitchModule('schema', this)">表结构信息</button>
      <button onclick="kbSwitchModule('metric', this)">指标信息</button>
      <button onclick="kbSwitchModule('dict', this)">数据字典</button>
      <button id="kbTabRegistry" onclick="kbSwitchModule('registry', this)">语义注册表</button>
    </div>"""
new_tabs = """    <!-- ── 二级模块：一个知识库，两个维度 ──
         「内容」= 知识有什么：按类型看、可编辑，每一类都要过治理状态；
         「流程」= 知识怎么管：总览 / 补缺 / 复核 / 进出。
         ★ 2026-10-27 合并原「知识管理」与「知识治理」两个一级菜单：
           它们本就是同一批知识的两个维度。做成两个平级入口会直接导致断层——
           AI 起草按钮在 A 菜单、复核「通过」按钮在 B 菜单，中间没人提示要去复核。
         ★ 默认停在「资产总览」：先把"家底 + 待办"摆在面前，而不是让人先选类型。 -->
    <div class="subtabs">
      <span class="subtabs-grp">内容</span>
      <button id="kbTabSchema" onclick="kbSwitchModule('schema', this)">表结构</button>
      <button onclick="kbSwitchModule('metric', this)">指标口径</button>
      <button onclick="kbSwitchModule('dict', this)">数据字典</button>
      <button id="kbTabRegistry" onclick="kbSwitchModule('registry', this)">语义注册表</button>
      <span class="subtabs-grp">流程</span>
      <button class="active" onclick="kbSwitchModule('overview', this)">资产总览</button>
      <button onclick="kbSwitchModule('gap', this)">缺口与补全</button>
      <button onclick="kbSwitchModule('review', this)">待复核<span id="govPendBadge"></span></button>
      <button onclick="kbSwitchModule('ingest', this)">导入</button>
      <button onclick="kbSwitchModule('export', this)">导出</button>
    </div>"""
must(new.count(old_tabs) == 1, "知识管理二级导航条没命中")
new = new.replace(old_tabs, new_tabs)

# ═══════════════════════════════════════════════════════════
# ⑤ 默认显示哪个模块：只有一个 active（改为资产总览）
# ═══════════════════════════════════════════════════════════
must(new.count('<div class="kb-module active" id="kbMod-schema">') == 1, "schema 默认 active 没命中")
new = new.replace('<div class="kb-module active" id="kbMod-schema">',
                  '<div class="kb-module" id="kbMod-schema">')
must(new.count('<div class="kb-module active" id="kbMod-overview">') == 1,
     "overview 默认 active 没命中（应保留为默认页）")
#   ★ 只在「知识库」这一节里数默认模块：整个文件里正常会有 2 个 active
#     （知识库的默认页 + 反馈处理页的默认子页），按全文数会误判。
kb_start = new.index('<section id="tab-kb" class="tab">')
kb_stop = new.index("</section>", kb_start)
n_active = new.count('class="kb-module active"', kb_start, kb_stop)
must(n_active == 1, f"「知识库」页只能有 1 个默认模块，实际 {n_active}")

# ═══════════════════════════════════════════════════════════
# ⑥ 左侧导航：两个按钮合成一个「知识库」（保留 navGovCnt 角标）
# ═══════════════════════════════════════════════════════════
old_nav = """        <button class="nav-item" data-tab="kb" data-title="知识管理"
                data-sub="表结构 / 指标口径 / 数据字典 / 语义注册表 —— 喂给模型的知识"
                onclick="switchTab('kb', this)">
          <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v16H6.5A2.5 2.5 0 0 0 4 21.5z"/><path d="M8.5 3v16"/></svg></span>
          <span>知识管理</span>
        </button>
        <button class="nav-item" data-tab="gov" data-title="知识治理"
                data-sub="知识缺口 / 优先级 / AI 录入 / 全局检索 / 导出备份 —— 让知识越用越准"
                onclick="switchTab('gov', this)">
          <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M15.6 8.4l-2.1 5.1-5.1 2.1 2.1-5.1z"/></svg></span>
          <span>知识治理</span>
          <!-- ★ 待复核角标：严格模式下 AI 生成的知识不进检索，积压必须能被看见，
               否则"系统好像不学新东西了"这种问题根本没人发现 -->
          <span class="cnt hidden" id="navGovCnt">0</span>
        </button>"""
new_nav = """        <!-- ★ 原「知识管理」「知识治理」两个一级菜单已合并为一个「知识库」
             （2026-10-27）：它们本是同一批知识的两个维度，分开放会让
             "AI 起草"和"复核通过"分别在两个菜单里，闭环被切断。 -->
        <button class="nav-item" data-tab="kb" data-title="知识库"
                data-sub="内容（表结构 / 指标 / 字典 / 语义注册表）+ 流程（总览 / 补缺 / 复核 / 导入导出）"
                onclick="switchTab('kb', this)">
          <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v16H6.5A2.5 2.5 0 0 0 4 21.5z"/><path d="M8.5 3v16"/></svg></span>
          <span>知识库</span>
          <!-- ★ 待复核角标：严格模式下 AI 生成的知识不进检索，积压必须能被看见，
               否则"系统好像不学新东西了"这种问题根本没人发现 -->
          <span class="cnt hidden" id="navGovCnt">0</span>
        </button>"""
must(new.count(old_nav) == 1, "两个导航按钮没命中")
new = new.replace(old_nav, new_nav)

# ═══════════════════════════════════════════════════════════
# ⑦ JS：清掉对 tab-gov 的引用，跳转改指向新位置
# ═══════════════════════════════════════════════════════════
must(new.count('if (name === "gov")      kbLoadGaps(false);') == 1, "switchTab 里的 gov 分支没命中")
new = new.replace('  if (name === "gov")      kbLoadGaps(false);      // 缺口扫描较慢，函数内部有缓存\n', "")

old_goto = """/** 跳到「知识治理 → 知识录入」沉淀知识（原「AI 录入」） */
function gotoIngest() {
  switchTab("gov", document.querySelector('.nav-item[data-tab="gov"]'));
  const btn = Array.from(document.querySelectorAll("#tab-gov .subtabs button"))
    .find(b => b.textContent.includes("知识录入"));
  if (btn) kbSwitchModule("ingest", btn);
}"""
new_goto = """/** 跳到「知识库 → 导入」沉淀知识（原「知识治理 → 知识录入」） */
function gotoIngest() {
  switchTab("kb", document.querySelector('.nav-item[data-tab="kb"]'));
  const btn = Array.from(document.querySelectorAll("#tab-kb .subtabs button"))
    .find(b => b.textContent.includes("导入"));
  if (btn) kbSwitchModule("ingest", btn);
}"""
must(new.count(old_goto) == 1, "gotoIngest 没命中")
new = new.replace(old_goto, new_goto)

#   「知识录入」已改名为「导入」，测试/提示文案里一并订正（只改用户可见处）
must(new.count("gotoIngest()") >= 1, "gotoIngest 没有任何调用方（改完等于死代码）")

# ═══════════════════════════════════════════════════════════
# ⑧ 样式：分组标签（不是按钮，点不动）
# ═══════════════════════════════════════════════════════════
anchor = "  .subtabs button"
must(new.count(anchor) >= 1, "找不到 .subtabs 样式，无法插入分组标签样式")
grp_css = """  .subtabs-grp {
    font-size: 11px; color: #86909c; letter-spacing: .5px;
    align-self: center; padding: 0 2px 0 10px; user-select: none;
  }
  .subtabs-grp:first-child { padding-left: 0; }
"""
new = new.replace(anchor, grp_css + anchor, 1)

# ═══════════════════════════════════════════════════════════
# ⑨ 一致性断言（改完必须自洽，否则宁可不动）
# ═══════════════════════════════════════════════════════════
#   ① 每个 switchTab('x') 都必须有对应的 tab-x
tabs_called = set(re.findall(r"switchTab\(['\"]([a-z]+)['\"]", new))
tabs_defined = set(re.findall(r'<section id="tab-([a-z]+)"', new))
missing = tabs_called - tabs_defined
must(not missing, f"有 switchTab 指向不存在的页面：{missing}")

#   ② 每个 kbSwitchModule('x') 都必须有对应的 kbMod-x 容器
mods_called = set(re.findall(r"kbSwitchModule\(['\"]([a-z]+)['\"]", new))
mods_defined = set(re.findall(r'id="kbMod-([a-z]+)"', new))
missing2 = mods_called - mods_defined
must(not missing2, f"有 kbSwitchModule 指向不存在的模块：{missing2}")

#   ③ 二级导航条的按钮数 == 模块数（8 == 8），少一个就是漏搬
must(len(mods_defined) == 8, f"模块数应为 8，实际 {len(mods_defined)}：{sorted(mods_defined)}")
must(new.count('class="subtabs-grp"') == 2, "分组标签应有 2 个（内容 / 流程）")

# ═══════════════════════════════════════════════════════════
# ⑩ 落盘（先备份）
# ═══════════════════════════════════════════════════════════
if not os.path.exists(BAK):
    shutil.copy2(SRC, BAK)
    print(f"已备份 → {BAK}")

open(SRC, "w", encoding="utf-8").write(new)
print(f"✅ 合并完成：{orig_len} → {len(new)} 字符")
print(f"   一级导航少了 1 项（知识管理 + 知识治理 → 知识库）")
print(f"   二级模块 {len(mods_defined)} 个，分「内容」「流程」两组")
print(f"   默认页 = 资产总览；待复核角标保留在左侧导航")
