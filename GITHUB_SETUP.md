# 知数AI · GitHub 最后配置卡（SSH 方案，做完即可发版）

> 代码、发版脚本、CI、文档都已就绪。项目已改用 **SSH 方式**连接 GitHub（走 443 端口，已配好），  
> SSH 网络通道我已验证是通的。只剩 **1 件必须你本人操作**的事：把公钥加到 GitHub。
>
> 为什么不用 PAT / 网页设置页：当前环境打开 GitHub 设置页会 404（未登录 session），  
> 而 WorkBuddy 的连接器 token 无写权限。SSH 是障碍最小的一条路。



---

## 第 1 步（唯一必做）：添加 SSH 公钥

打开：**<https://github.com/settings/keys>**

> 这个地址登录后就能打开（不像仓库 settings 页会 404）。如果右上角没登录，先登录账号  
> `zhangtao630095771-byte`。

点绿色按钮 **New SSH key**，按下面填：

| 字段       | 填什么                      |
| -------- | ------------------------ |
| Title    | `zhangtao-mac`（随便起，方便认）  |
| Key type | `Authentication Key`（默认） |
| Key      | 粘贴下面这串公钥 ↓               |

**公钥（整行复制）：**

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIC3lVRuQCQeZdT2NFQ9RxU7nywrMXysv1UZZw2M1zDFg zhangtao-github
```

点 **Add SSH key** 完成。

> 公钥在文件 `~/.ssh/id_ed25519.pub` 里，私钥不会离开你的电脑，安全。

---

## 第 2 步：执行发版（1 分钟）

回到终端，我先帮你验证公钥是否生效——你可以直接让我跑，或自己执行：

```bash
ssh -T git@github.com
```

看到 `Hi zhangtao630095771-byte! You've successfully authenticated` 就成功了。然后发版：

```bash
cd /Users/zhangtao/Downloads/autosql/zhishu-ai
./release.sh 1.0.0
```

脚本会自动做：

1. ✅ SSH 认证预检（公钥没加会直接提示，不白跑）
2. ✅ 密钥扫描（发现真实 key/密码就拒绝提交）
3. ✅ git add + commit + 打 `v1.0.0` tag
4. ✅ push 主干 + push tag

看到 `🎉 发版 v1.0.0 完成！` 就成了。

---

## 第 3 步（可选）：仓库改私有 + 建 Release

**改私有**（建议，公司内部项目）：登录后打开  
<https://github.com/zhangtao630095771-byte/zhishu-ai/settings>  
→ 底部 Danger Zone → Change repository visibility → Private。

> 注意：这个页面**必须先在浏览器登录 GitHub** 才能打开；没登录时会显示 404，这是 GitHub 的正常行为，不是链接错了。

**建 Release**：打开脚本打印的链接，或  
<https://github.com/zhangtao630095771-byte/zhishu-ai/releases/new>

- Tag 选 `v1.0.0`，标题 `v1.0.0`，正文贴 `CHANGELOG.md` 的 `[1.0.0]` 段落，Publish。

---

## 完成后的日常发版

```bash
# 1. 改代码、更新 VERSION 和 CHANGELOG.md
# 2. 一条命令发版（SSH 认证，无需 token）
./release.sh 1.0.1
```

服务器更新、回滚步骤见 [RELEASE.md](RELEASE.md)。

---

## 常见问题

**Q：`Permission denied (publickey)`？**  
A：公钥还没加到 GitHub，或加错了。回第 1 步，确认粘贴的是上面那整行 `ssh-ed25519 ...`。

**Q：`Hi xxx! You've successfully authenticated, but GitHub does not provide shell access`？**  
A：这是**成功**的提示（GitHub 不允许 SSH 交互式登录是正常的），可以放心发版。

**Q：设置页还是 404？**  
A：在浏览器里登录 GitHub 账号后再访问。未登录访问任何仓库设置页都会 404。

**Q：SSH 连不上（timeout）？**  
A：本项目 `~/.ssh/config` 走 `ssh.github.com:443`。确认网络放行 443；公司网络若有代理/防火墙限制 SSH，需要网络管理员放行。
