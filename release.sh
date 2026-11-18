#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# release.sh —— 知数AI 一键发版脚本（本地提交 + 打 tag + push）
#
# 用法：
#   ./release.sh 1.0.0          # 发 v1.0.0
#
# 它会依次做：
#   ① 检查工作区、网络、认证、待提交文件里的真实密钥（不通过直接拒绝）
#   ② git add -A + commit
#   ③ 打 annotated tag v<版本号>
#   ④ push 到 origin main + push tag
#
# 认证（本项目 origin 走 SSH，走 443 端口 + ~/.ssh/id_ed25519，无需 PAT）：
#   ./release.sh 1.0.0
# 前提：公钥已添加到 GitHub 账号（见 GITHUB_SETUP.md）。
# 若改回 HTTPS 远程，可用 GITHUB_TOKEN 环境变量传 PAT：
#   GITHUB_TOKEN=ghp_xxx ./release.sh 1.0.0
#
# ★ 设计原则：宁可不发，也不把密钥推到公开仓库。
#   安全检查任一失败 → 立即退出，不产生任何提交。
# ═══════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")"

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    echo "用法：./release.sh <版本号，如 1.0.0>"
    exit 1
fi
if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "❌ 版本号格式应为 MAJOR.MINOR.PATCH（如 1.0.0），收到：$VERSION"
    exit 1
fi
TAG="v$VERSION"
REMOTE_URL="$(git remote get-url origin 2>/dev/null || echo '')"
if [ -z "$REMOTE_URL" ]; then
    echo "❌ 没有配置 origin 远程仓库"
    exit 1
fi
# 从 remote URL 解析 owner/repo（兼容 https 与 ssh 两种写法）
REPO_PATH="$(echo "$REMOTE_URL" | sed -E 's#^.*[:/]([^/]+/[^/]+?)(\.git)?$#\1#')"
echo "目标仓库：$REPO_PATH"
echo "目标 tag ：$TAG"
echo ""

# ── ①-a 工作区检查 ────────────────────────────────────────
echo "① 安全检查…"
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "   ℹ️  工作区有改动，属正常（就是本次要提交的）"
fi
# 网络 + SSH 认证预检（origin 走 SSH，走 443 端口）
if echo "$REMOTE_URL" | grep -q '^git@'; then
    SSH_OUT="$(ssh -o BatchMode=yes -o ConnectTimeout=12 -T git@github.com 2>&1 || true)"
    if echo "$SSH_OUT" | grep -qi 'Permission denied (publickey)'; then
        echo "   ❌ SSH 连到了 GitHub，但公钥未授权"
        echo "      请把 ~/.ssh/id_ed25519.pub 的内容添加到 GitHub："
        echo "      https://github.com/settings/keys  （New SSH key → 粘贴公钥）"
        exit 1
    elif echo "$SSH_OUT" | grep -qiE 'successfully authenticated|Hi .*!'; then
        echo "   ✅ SSH 认证通过"
    elif echo "$SSH_OUT" | grep -qiE 'timeout|could not|refused|no route'; then
        echo "   ⚠️  SSH 连接异常：$SSH_OUT"
        echo "      本项目 ~/.ssh/config 走 443 端口，请确认网络放行了 ssh.github.com:443"
        exit 1
    else
        echo "   ℹ️  SSH 探测返回：$SSH_OUT"
    fi
elif command -v curl >/dev/null 2>&1; then
    # HTTPS 远程：预检 github.com:443（内网代理常只放行 api.github.com）
    CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 10 https://github.com 2>/dev/null || echo 000)"
    if [ "$CODE" = "000" ]; then
        echo "   ❌ 网络连不上 github.com:443 —— git push 会失败"
        echo "      换能直连 GitHub 的网络，或配置代理："
        echo "      git config --global http.proxy http://你的代理:端口"
        exit 1
    fi
    echo "   ✅ 网络可达 github.com"
fi

# ── ①-b 待提交文件密钥扫描（★ 最关键）────────────────────
# 列出将被提交的文件（含暂存与未跟踪，排除 ignored）
FILES="$(git ls-files --cached --others --exclude-standard)"
LEAK=0
# 扫真实 API key（sk- 后跟 20 位以上字母数字）
if printf '%s\n' "$FILES" | xargs -r grep -lE 'sk-[a-zA-Z0-9]{20,}' 2>/dev/null | grep -q .; then
    echo "   ❌ 发现疑似真实 API Key（sk-...），拒绝提交！"
    printf '%s\n' "$FILES" | xargs -r grep -lE 'sk-[a-zA-Z0-9]{20,}' 2>/dev/null | sed 's/^/      /'
    LEAK=1
fi
# 扫明文密码赋值（排除 .example 模板与文档）
if printf '%s\n' "$FILES" | grep -vE '\.example$|\.md$' | xargs -r grep -lE '^(SSH_PASSWORD|CK_PASSWORD|DEEPSEEK_API_KEY)=[^$<[:space:]]' 2>/dev/null | grep -q .; then
    echo "   ❌ 发现明文密码赋值，拒绝提交！"
    printf '%s\n' "$FILES" | grep -vE '\.example$|\.md$' | xargs -r grep -lE '^(SSH_PASSWORD|CK_PASSWORD|DEEPSEEK_API_KEY)=[^$<[:space:]]' 2>/dev/null | sed 's/^/      /'
    LEAK=1
fi
if [ "$LEAK" -ne 0 ]; then
    echo ""
    echo "   请把密钥移到 .env（已被 gitignore 排除），确认后重试。"
    exit 1
fi
echo "   ✅ 待提交文件未发现真实密钥"

# ── ①-c 备份垃圾扫描 ──────────────────────────────────────
BAK="$(printf '%s\n' "$FILES" | grep -ciE '\.bak|bak-' || true)"
if [ "$BAK" -gt 0 ]; then
    echo "   ⚠️  待提交文件里有 $BAK 个 .bak 备份文件，已由 .gitignore 排除，正常应为 0"
fi

# ── ② 写版本号 + 提交 ─────────────────────────────────────
echo "② 提交改动…"
echo "$VERSION" > VERSION
git add -A
if ! git diff --cached --quiet; then
    git commit -m "chore: release $TAG" || { echo "❌ commit 失败"; exit 1; }
    echo "   ✅ 已提交"
else
    echo "   ℹ️  没有需要提交的改动（可能上次已提交）"
fi

# ── ③ 打 tag ──────────────────────────────────────────────
echo "③ 打 tag $TAG …"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "   ℹ️  tag $TAG 已存在，跳过"
else
    git tag -a "$TAG" -m "Release $TAG" || { echo "❌ 打 tag 失败"; exit 1; }
    echo "   ✅ 已打 tag"
fi

# ── ④ push ────────────────────────────────────────────────
echo "④ 推送到 GitHub…"
# 若通过 GITHUB_TOKEN 传入 PAT，用一次性 credential helper 注入，避免 token 落盘/进历史。
# 注意：helper 里的 token 只存在于本进程命令行，push 结束即随进程消失。
PUSH_ENV=()
if [ -n "${GITHUB_TOKEN:-}" ]; then
    TOKEN_HELPER='!f(){ echo username=x-access-token; echo password='"$GITHUB_TOKEN"'; }; f'
    PUSH_ENV=(-c "credential.helper=$TOKEN_HELPER")
    echo "   ℹ️  使用 GITHUB_TOKEN 环境变量认证"
fi
if git "${PUSH_ENV[@]}" push origin main 2>&1 | sed 's/^/   /'; then
    echo "   ✅ 主干已推送"
else
    echo "   ❌ push main 失败"
    echo "      排查：① 网络能否访问 github.com:443  ② PAT 是否有 repo 写权限  ③ 仓库是否已满/被锁"
    exit 1
fi
if git "${PUSH_ENV[@]}" push origin "$TAG" 2>&1 | sed 's/^/   /'; then
    echo "   ✅ tag 已推送"
else
    echo "   ❌ push tag 失败"
    exit 1
fi

echo ""
echo "🎉 发版 $TAG 完成！"
echo "   下一步：去 GitHub Releases 页面基于 $TAG 创建 Release"
echo "   https://github.com/$REPO_PATH/releases/new?tag=$TAG"
