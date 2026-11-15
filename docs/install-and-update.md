# 安装与更新

## 三种装法

| 你要什么 | 做什么 |
|---|---|
| 只要命令行（在自己的 shell 里跑） | `pip install -e .`（仓库根目录） |
| 让 DSH 里的 agent 会用这套方法 | 装**技能**：`python skills/guicang/scripts/install.py --target dsh` |
| 让 DSH 里出现一个「归藏」预设（专门干归档的 agent） | 再装 **preset**，见 `preset/README.md` |

三者互不依赖：命令行是本体，技能是给 agent 看的说明书，preset 是把这个 agent 的默认人设换成归档员。
**先装本体，再装技能，最后装 preset** —— 顺序反了的话 agent 会照着技能去调一个还不存在的命令。

## 命令行本体

```powershell
cd C:\Users\life2\Documents\deepseek-harness\default-workspace\guicang
pip install -e ".[exif]"
guicang --version
```

- 不装 `[exif]` 也能用：EXIF 走手写解析，只是**缩略图**没有 Pillow 会退回原图（网页变慢）。
- 想跑测试：`pip install -e ".[dev]"` 后 `python -m unittest discover -s tests -t tests`。
- 不想污染环境：`pipx install -e .` 或 `python -m venv .venv` 后激活再装。

## 技能

```powershell
python skills\guicang\scripts\install.py --dry-run     # 先看会写哪
python skills\guicang\scripts\install.py --target dsh  # 装给 DSH
python skills\guicang\scripts\install.py --target all  # 顺带 Claude Code / Cursor / Codex / Gemini
```

- 真源永远是仓库里的 `skills/guicang/`，安装只是**复制**（Windows 上默认不软链，因为软链要开发者模式，
  "装不上"比"装了两份要记得同步"更糟）。
- 覆盖前会检查目标目录里 `SKILL.md` 的 `name` 是不是 `guicang`。不是就**拒绝覆盖**——防止把用户
  自己的同名项目目录删掉。确实要删加 `--force-any`。
- 装完在 DSH 里新开会话，`skill` 工具里应能看到 `guicang`。

## preset

见 [`preset/README.md`](../preset/README.md)。要点：目录名必须是 `guicang`，
DSH 只在启动时读一次预设，所以**装完要重启**。

本机结构（Windows）：

```
~/.dsh/profiles/desktop/agent-presets/guicang/     ← 真正的文件
~/.dsh/.agent-presets/guicang → (junction) → 上面那个
```

## 更新

```powershell
git pull
pip install -e .                                  # 跟进代码
python skills\guicang\scripts\install.py --target dsh --force
Copy-Item -Recurse -Force .\preset "$env:USERPROFILE\.dsh\profiles\desktop\agent-presets\guicang"
# 重启 DSH
```

**已经归过的库不用动。** 分类树上的链接、索引、`manual.json` 都跟代码无关；升级后跑一次
`guicang doctor` + `guicang report`，看分布有没有变化就够。真要按新分类法重排：

```powershell
guicang undo --yes        # 收走旧树（原件不动）
guicang scan              # 用新规则重算判定
guicang plan              # 看新形状
guicang apply --yes
```

## 迁移素材库（换盘、换根目录）

索引里存的是**相对路径**，所以整个库连 `.guicang\` 一起搬走，在新位置 `guicang scan` 就认得
（文件大小+mtime 一致即视为未变）。`_归藏\` 里的硬链接搬不了（跨盘会变成复制，且链接关系丢失），
正确做法是搬完 `undo` 再 `apply`，或者干脆不搬树、到新位置重建。

## 卸载

```powershell
pip uninstall guicang
Remove-Item "$env:USERPROFILE\.dsh\skills\guicang" -Recurse -Force
Remove-Item "$env:USERPROFILE\.dsh\.agent-presets\guicang" -Force      # 先删 junction
Remove-Item "$env:USERPROFILE\.dsh\profiles\desktop\agent-presets\guicang" -Recurse -Force
```

**素材库里的东西不会被卸载碰到**：`.guicang\`（配置+索引）与 `_归藏\`（链接树）都在素材根目录里，
要清就把 `_归藏\` 删掉（原件仍在），或 `guicang undo --yes` 收干净。

## 常见问题

**`guicang` 不是内部或外部命令。** 装到了别的 Python 环境。用 `python -m guicang --version` 确认，
再对当前环境的 pip 重装一次。

**`doctor` 说有 Pillow 缺失，但我装了。** `doctor` 报的是「当前跑 guicang 的那个 python」，
不是你家别的 Python。换 `python -m guicang doctor` 看是哪个解释器。

**Windows 上 `apply` 报跨卷。** 素材在 `D:`、`_归藏\` 在 `C:`。要么把树挪到素材盘
（`[library] tree` 只能设名字，不能设路径 —— 树永远在素材根目录下，所以换个根目录跑），
要么接受 `copy` 模式的额外空间。
