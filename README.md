# 归藏 guicang

**素材归位，原件不动。** 把多年攒下的照片和视频，按「区域或活动 → 时间 → 拍摄类型」
归成一棵看得懂的树，再在本机网页里翻它、筛它、挑它，最后导出一份能直接喂给剪辑软件的清单。

```
_归藏\
├─ 区域\郑州奥体中心\2026\2026-05\航拍\DJI_0001.MP4
├─ 活动\开封菊花文化节\2026-第44届\2026-10\航拍\DJI_0032.MP4
└─ 待定\2026\2026-05\航拍\DJI_0002.MP4      ← 没把握的，不瞎归
```

三条硬承诺：

1. **原件一根手指都不碰。** 分类树是硬链接（同盘零占用），`undo` 只删树里的链接。
2. **判定带证据。** 每条素材的拍摄类型/地点/活动都有一串带权重的证据，`explain` 能逐条摊开。
3. **不确定就进待定。** 置信度不到 0.75 的不归位，列给人看——宁可少归一条，不错归一条。

## 它解决的是哪种乱

不是"文件没改名"，是**找不回来**：去年十月在开封拍的菊花节航拍，夹在三万张照片里，
知道它存在、知道大概什么时候拍的，就是翻不到。归藏把"当时在场的判断"（地点、届次、机位）
用 EXIF/遥测/机型固化进索引，之后按任何一个维度都能立刻筛出来。

## 快速开始

```bash
pip install -e ".[exif]"          # 装命令行（Pillow 可选，管缩略图）
python -m guicang.demo D:\试跑     # 生成一个 8 个文件的合成素材库，够跑通全流程

guicang init --root D:\试跑        # 写配置骨架（places.toml / activities.toml）
guicang doctor --root D:\试跑      # 自检
guicang scan --root D:\试跑        # 只读原件，写索引
guicang report --root D:\试跑      # 分布 + 待定原因
guicang plan --root D:\试跑        # 预演：多少条、多少字节、几个重名
guicang apply --root D:\试跑 --yes # 真落位（硬链接）
guicang serve --root D:\试跑 --open                       # 浏览器里翻库、勾选、存选集
guicang export --root D:\试跑 --collection 精选 --format csv,txt   # 台账 + 剪辑清单
```

`--root` 写在子命令前后都行；已经在素材根目录（或它下面）里跑时全省略——会往上找 `.guicang\`。

真实素材库上，第一次跑建议按这个顺序，并且**先看 `report` 再 `apply`**：
一半素材落进待定，说明 `places.toml` 没填或半径给小了，先补配置再归。

## 分类法

| 维度 | 取值 | 可配置？ |
|---|---|---|
| 主枝 | `区域` / `活动` / `待定` | `[region]` `[activity]` 开关 |
| 区域第一层 | 地点清单里的名字（不开城市层级） | `places.toml`，带经纬度与半径 |
| 活动第一层 | 活动清单里的名字 | `activities.toml`，重复举办的活动写这里 |
| 活动第二层 | `2026-第44届`，认不届次就 `2026` | 从目录名自动认「第N届」/「第十一届」 |
| 时间 | `年\年-月`（默认），可切 `年` 或 `年\年-月\年-月-日` | `[time] depth` |
| 拍摄类型 | 航拍 / 地拍 / 延时 / FPV / 其他 | 代码里的 5 类，加类要改 `SHOT_TYPES` |

判定优先级（拍摄类型）：目录名里的「延时/航拍/地拍」> 无人机机型 > `.SRT` 相对高度 > 地面机型 >
`DJI_` 前缀（0.55，落待定）> 默认地拍（0.50，落待定）。完整规则见
[`skills/guicang/references/evidence.md`](skills/guicang/references/evidence.md)。

## 命令

| 命令 | 干什么 | 动磁盘？ |
|---|---|---|
| `guicang init [--force]` | 写配置骨架 | 只写 `.guicang\` |
| `guicang doctor` | 自检依赖/配置/索引/树 | 否 |
| `guicang scan [--force]` | 读原件写索引 | 否 |
| `guicang report [--csv]` | 分布、待定原因 | 否 |
| `guicang review [--set ...]` | 列待定 / 人工钉判定 | 写 `manual.json` |
| `guicang explain <路径>` | 逐条证据 | 否 |
| `guicang plan [--mode] [--json]` | 预演落位 | 否 |
| `guicang apply --yes [--mode]` | 真落位（默认 hardlink） | **是** |
| `guicang undo --yes` | 撤回落位（只删链接） | **是** |
| `guicang serve` | 本机网页云（筛选/预览/选集/导出） | 否 |
| `guicang sync [--dry-run]` | 增量同步到本机目录或 S3 兼容存储 | 只读本地 |
| `guicang export [--collection]` | CSV / JSON / 路径清单 | 写输出目录 |

## 网页云

`guicang serve` 起一个零构建的单页应用：缩略图墙、五维筛选、视频直接播（Range 206）、
勾选成选集、一键导出台账。

```bash
guicang serve --open                      # 本机
guicang serve --host 0.0.0.0 --token 口令  # 手机同一 WiFi 也能翻
```

没设口令又绑定到非回环地址时，会自动生成一个随机口令并打印——**不裸奔**。
接口清单见 [`skills/guicang/references/cloud.md`](skills/guicang/references/cloud.md)。

## 上云

`[sync] backend = "local" | "s3"`，全量增量同步（大小 + mtime 判据，状态存索引的 kv 表）。
S3 走内置 SigV4 签名，零第三方依赖，阿里云 OSS / 腾讯云 COS / MinIO / R2 都能用。

## 目录

```
src/guicang/
  config.py      TOML 配置（写坏会指到行号，不静默退默认）
  probe.py       EXIF / MP4 mvhd / SRT 遥测 → Probe
  classify.py    拍摄类型、区域、活动、届次的证据链
  taxonomy.py    决策 → 树路径（区域 / 活动 / 待定）
  scanner.py     遍历、旁车挂靠、增量、人工判定覆盖
  materialize.py plan / apply / undo（硬链接·复制·移动）
  store.py       SQLite 索引（assets / collections / journal / kv）
  webapp.py      网页云 + 缩略图 + Range 视频
  storage.py     SigV4 + 本机目录/S3 两种后端
  export.py      CSV（中文表头、带 BOM）/ JSON / 路径清单
  demo.py        合成素材库（自写 EXIF/MP4/SRT，不依赖外部素材）
skills/guicang/  技能：SKILL.md + references（分类法 / 证据 / 云端）+ install.py
preset/          DSH agent 预设（能力面照抄 standard，只换 persona）
tests/           92 条 unittest，零依赖
```

## 设计取舍

- **索引是真相，树可重建。** 分类法会变（用户已经改过一次：去掉城市层级）。索引不变、树随时推倒，
  这个决定让"改分类法"从迁移事故变成一个命令。
- **默认硬链接而不是移动。** 归档工具最不可接受的失败是"我的原件被你搬走了"。硬链接同盘零成本，
  `undo` 干净；跨盘自动降级成复制并如实说。
- **待定桶是一等公民。** 归档工具最大的罪不是归错，是**假装归对了**。
- **零依赖优先。** EXIF/TIFF/MP4/SRT 全是手写解析（含 moov 盒子跳读，不吞整段视频），
  S3 是手写 SigV4——装得上的工具比功能多的工具活得久。

## 开发

```bash
python -m unittest discover -s tests -t tests     # 92 条，约 3 秒
python -m guicang.demo tests/_tmp/demo --clean     # 重新造一份演示库
```

## 许可

MIT。见 [LICENSE](LICENSE)。
