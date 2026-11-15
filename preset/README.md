# 归藏 agent preset

一个 DeepSeek Harness 的 **agent 预设**：把标准模式的能力面，换上素材归档工作的行为约束。

```
preset/
├── preset.yml        # 名字与描述（显示在预设选择器里）
└── agent.cordis.yml  # 组合文件：挂哪些插件、persona 写什么
```

## 安装

DSH 的用户预设目录是 `~/.dsh/.agent-presets/`，目录名必须是 `guicang`（= 预设 id），
改名会导致选择器里认不出。

本机的实际结构是：`~/.dsh/.agent-presets/<id>` 是一个 **junction**，指向
`~/.dsh/profiles/desktop/agent-presets/<id>`。所以两步走：

```powershell
# ① 把 preset 复制成真正的预设目录
Copy-Item -Recurse -Force .\preset "$env:USERPROFILE\.dsh\profiles\desktop\agent-presets\guicang"
# ② 在 .agent-presets 下建 junction（不需要管理员）
New-Item -ItemType Junction -Path "$env:USERPROFILE\.dsh\.agent-presets\guicang" `
         -Target "$env:USERPROFILE\.dsh\profiles\desktop\agent-presets\guicang"
```

然后**重启 DSH**，新建会话时在预设选择器里选「归藏」。

⚠️ 更新时也要重新复制一遍（预设只在 DSH 启动时读一次），复制完再重启。

## 先装技能，再开预设

persona 里让 agent「用 `skill` 工具加载 guicang」——那是指仓库里 `skills/guicang/` 这份技能。
装好它，预设才有领域知识：

```bash
python skills/guicang/scripts/install.py --target dsh
```

（`--target all` 会同时装到 Claude Code / Cursor / Codex / Gemini 的目录。）

## 为什么能力面照抄 standard

与内置 `standard` 预设的差别**只有 persona**。能力面跟着官方走，升级 DSH 时不会因为漏挂
一行工具而瘸腿；域知识（分类法、证据阈值、云同步）全部留在技能里按需加载，
persona 只写「这个人是谁、守什么规矩」——persona 常驻上下文每轮都付费，技能正文可以写长。

⚠️ 只用 `{{model}}` / `{{provider}}` 两个已注册变量。写 `{{cwd}}` 之类未注册变量会让整份
preset 渲染失败、新会话直接起不来。
