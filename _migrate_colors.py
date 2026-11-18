#!/usr/bin/env python3
"""知数AI 前端配色迁移：硬编码 hex → var(--token)（DataBuddy 风）

策略：
  * 只替换「语义清晰 1:1 映射」的颜色（品牌蓝/语义色/文本灰/边框灰/浅底）。
  * 跳过含 `--color-` 的行（保护 :root token 定义，避免自引用）。
  * 故意保留：白色、P0~P4 优先级阶梯色、experience 的 golden/candidate 阶梯、
    深色文字变体（#a85c00/#00862a/#d92c2c 等，用于浅底上的深色文字，需保持对比度）。
"""
import re, os, sys

# hex(小写) -> 替换文本（注意：值统一用 var(--token)，token 值在 app.css :root 定义）
MAP = {
    # 品牌蓝
    "165dff": "var(--color-primary)",
    "0e42d2": "var(--color-primary-hover)",
    "4080ff": "var(--color-primary-hover)",
    # 语义色（主值）
    "00b42a": "var(--color-success)",
    "f53f3f": "var(--color-danger)",
    "ff7d00": "var(--color-warning)",
    # 文本
    "1f2329": "var(--color-text-primary)",
    "1d2129": "var(--color-text-primary)",
    "4e5969": "var(--color-text-secondary)",
    "86909c": "var(--color-text-muted)",
    "a9b0bd": "var(--color-text-muted)",
    "a9aeb8": "var(--color-text-muted)",
    # 边框 / 背景
    "e5e6eb": "var(--color-border)",
    "f5f6f8": "var(--color-bg-page)",
    "f7f8fa": "var(--color-bg-hover)",
    "fbfcfe": "var(--color-bg-hover)",
    "fbfbfc": "var(--color-bg-hover)",
    "f2f3f5": "var(--color-bg-subtle)",
    "e8ebf0": "var(--color-border)",
    "f0f1f4": "var(--color-border)",
    "eef0f4": "var(--color-border)",
    "eef0f3": "var(--color-border)",
    "f0f1f3": "var(--color-border)",
    "f0f2f6": "var(--color-border)",
    "d9d9d9": "var(--color-border-strong)",
    "c9cdd4": "var(--color-border-strong)",
    "d5dae3": "var(--color-border-strong)",
    # 蓝色系浅底（统一到 primary-subtle）
    "e8f3ff": "var(--color-primary-subtle)",
    "eaf1ff": "var(--color-primary-subtle)",
    "e9f0ff": "var(--color-primary-subtle)",
    "f0f5ff": "var(--color-primary-subtle)",
    "f2f6ff": "var(--color-primary-subtle)",
    "d8e8ff": "var(--color-primary-subtle)",
    "eef3ff": "var(--color-primary-subtle)",
    "f6f9ff": "var(--color-primary-subtle)",
    "e6edfb": "var(--color-primary-subtle)",
    "f2f5fa": "var(--color-primary-subtle)",
    # 语义浅底 / 浅边
    "e8ffea": "var(--color-success-bg)",
    "aff0b5": "var(--color-success-border)",
    "fff7e8": "var(--color-warning-bg)",
    "ffcf8b": "var(--color-warning-border)",
    "ffece8": "var(--color-danger-bg)",
    "fdcdc5": "var(--color-danger-border)",
    "bedaff": "var(--color-info-border)",
    "94bfff": "var(--color-info-border)",
}

# 精确匹配 #RRGGBB 或 #RGB（不区分大小写）
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")

def migrate_line(line):
    if "--color-" in line:  # 保护 token 定义行
        return line
    def repl(m):
        raw = m.group(0)
        hexpart = raw[1:]
        # 展开 3 位 -> 6 位小写
        if len(hexpart) == 3:
            hexpart = "".join(c * 2 for c in hexpart)
        key = hexpart.lower()
        return MAP.get(key, raw)  # 不在映射里 -> 原样保留
    return HEX_RE.sub(repl, line)

def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "frontend"
    targets = []
    for dirpath, _, files in os.walk(root):
        if ".bak" in dirpath:
            continue
        for f in files:
            if f.endswith((".css", ".html", ".js")):
                targets.append(os.path.join(dirpath, f))

    total = 0
    for path in targets:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        new_lines = [migrate_line(l) for l in lines]
        changed = sum(1 for a, b in zip(lines, new_lines) if a != b)
        if changed:
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(new_lines)
            total += changed
            print(f"{changed:4d} 处  {os.path.relpath(path)}")
    print(f"\n合计替换 {total} 处（按行计）")

if __name__ == "__main__":
    main()
