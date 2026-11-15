---
name: guicang
description: 把摄影/航拍素材按「区域 or 活动 → 时间 → 拍摄类型」归好位，并在本机网页里翻库、筛选、挑选集、导出台账，还能同步到对象存储。Use when 用户要整理素材库、给一堆照片视频分类归档、按地点或活动找素材、问「去年开封的航拍在哪」、要挑一批素材交给剪辑、要建素材台账/选集清单、或要把素材备份上云；do not use for 单张修图调色、剪辑成片、或需要肉眼审美判断的选片。
license: MIT
compatibility: 需要 Python 3.11+。核心零第三方依赖；缩略图与网页预览可选 Pillow（`pip install guicang[exif]`），视频预览可选 ffmpeg；云同步支持本机目录或任意 S3 兼容对象存储（内置 SigV4 签名，无需 boto3）。
metadata:
  author: CodeVoyagerht
  version: "0.1.0"
  repo: https://github.com/CodeVoyagerht/guicang
---

# 归藏 guicang

**原件一根手指都不碰，分类树只是硬链接。** 索引是真相，树可以随时推倒重建。

素材进库后分成两条主枝，这是天天要的那套分法：

```
<素材根>\_归藏\
├─ 区域\郑州奥体中心\2026\2026-05\航拍\DJI_0001.MP4
│        └ 地点         年    月     拍摄类型
├─ 活动\开封菊花文化节\2026-第44届\2026-10\航拍\DJI_0032.MP4
│        └ 活动名         届次          月     拍摄类型
└─ 待定\2026\2026-05\航拍\DJI_0002.MP4        ← 没把握的，不瞎归
```

两条枝的每一层都能改：地点清单、活动清单、时间粒度（年/月/日）、拍摄类型取值，全在
`.guicang\` 下的 TOML 里。**分类法不是焊死的**，用户改主意只改配置，不重装。

## 什么时候用

| 用户说的是 | 用不用 | 怎么做 |
|---|---|---|
| "我这个盘里的素材太乱了，帮我分类" | ✅ | 工作流 A |
| "按地点分、再按年月分、再分航拍地拍" | ✅ | 工作流 A（就是默认分法） |
| "菊花文化节每年都办，把历届素材归到一起" | ✅ | 工作流 A，活动清单里加别名 |
| "找去年开封的航拍素材" | ✅ | 工作流 B（`serve` 里筛，或 `export`） |
| "挑 30 条给我剪一条片子" | ✅ | 工作流 B，挑完导清单交给 `cutwright` |
| "这些素材能备份到云上吗" | ✅ | 工作流 C |
| "这条为什么被归到延时了" | ✅ | `guicang explain <路径>` |
| "这张照片帮我调个色" | ❌ | 不是归类问题，直接说去 Lightroom/达芬奇 |
| "帮我把这条片剪出来" | ❌ | 交给 `cutwright` 技能，这里只负责把素材挑好、清单导好 |

## 铁律（不许跳）

1. **先 `doctor`，再 `scan`。** 不知道配置和索引是什么状态就开始归位，等于盲开。
   `doctor` 会告诉你缺 Pillow、缺 ffmpeg、配置写坏在哪一行。
2. **`plan` 没看过就不许 `apply`。** `plan` 是零副作用的预演：多少条、多少字节、哪些重名。
   `apply` 是真动磁盘——先把计划念给用户看。
3. **`apply` 默认 `hardlink`，绝不默认 `move`。** 硬链接同盘零占用、原件仍在。`move` 会搬走原件，
   必须先征得用户同意，并提醒「`undo` 撤不回 move」。
4. **待定就报待定，不许瞎猜。** 「待定」不是失败，是**诚实**。原始素材里航拍和地拍混在一个文件夹、
   没 GPS 没机型，机器就是看不出来——把它列给用户，用 `review` 一条条钉住，比编一个 78% 的
   假答案强。
5. **不许说"应该都归好了"。** 要么跑 `report` 拿到数字（多少条 auto、多少待定、按什么分的），
   要么承认没验证。**交付必须带证据**：条数 + 分布 + 待定清单 + 树里任取一条的完整路径。

## 工作流 A：第一次接手一堆素材

```bash
guicang init --root D:\素材      # ① 建 .guicang\guicang.toml + places.toml + activities.toml
# ② 打开 places.toml 填真实地点（名字、别名、经纬度、半径），activities.toml 填常办活动
guicang doctor --root D:\素材    # ③ 自检
guicang scan --root D:\素材      # ④ 只读原件，写索引（8 个文件约 1 秒）
guicang report --root D:\素材    # ⑤ 看分布与待定原因
```

`--root` 写在子命令前后都行（`guicang --root X scan` 与 `guicang scan --root X` 等价）；
已经在素材根目录或它下面某个子目录里跑时，可以全省略——它会往上找 `.guicang\`。

`guicang` 不在 PATH 时（pip 装完常见），`python -m guicang` 完全等价，别在这上面卡住。

拿到 `report` 之后**别急着 apply**，先看两件事：

- **待定数量**。多到离谱（比如一半）说明 `places.toml` 没填或填错——地名对不上、坐标偏差超过半径，
  都会让整条路径落进 `待定\`。这时候补配置，再 `scan --force`。
- **拍摄类型的分布**。全是「地拍」说明机型识别没命中：DJI 素材的文件名是 `DJI_0001.MP4` 但
  EXIF 被删过，就只能靠 `.SRT` 的高度或目录名里的「航拍」二字。

```bash
guicang review                                   # 列待定，每条都带「钉住」命令模板
guicang review --set "随手拍/IMG_1001.JPG" --region 开封古城 --shot 地拍
guicang plan                                     # 预演：8 条 / 0B / 0 冲突
guicang apply --yes                              # 真落位（硬链接）
guicang report                                   # 落位后再看一遍
```

`review --set` 会把判定写进 `.guicang\manual.json` 并**立刻重算那一条**，下次 `scan` 也不会被覆盖——
人工判定永远压过机器推断。

## 工作流 B：翻库、挑素材、交出去

```bash
guicang serve --open                    # 本机 8787，浏览器里翻库
guicang serve --host 0.0.0.0 --token 我的口令   # 手机连同一个 WiFi 也能翻
```

网页里能干的事：按拍摄类型/地点/活动/月/状态筛选、看缩略图与视频、勾选、存成**选集**、
导出台账。选集存在索引里，导出三件套：

```bash
guicang export --collection "开封航拍精选" --format csv,txt
# → 开封航拍精选.csv（中文表头，Excel 直接开）+ 开封航拍精选.txt（每行一个绝对路径）
```

`txt` 那份是给剪辑软件的：**达芬奇/剪映拖不进去一万个文件，但能读一个路径清单**。
把清单交给 `cutwright` 技能，就能按这份清单出片。

不建选集、只想按筛选条件导一份台账：

```bash
guicang export --shot 航拍 --year 2026 --format csv --out D:\台账
```

## 工作流 C：上云（本机备份盘 或 对象存储）

```toml
# .guicang\guicang.toml
[sync]
backend = "local"            # local | s3

[sync.local]
dir = "E:\\归藏备份"          # backend=local 时用这个

[sync.s3]                    # backend="s3" 时填这一段
endpoint = "https://oss-cn-hangzhou.aliyuncs.com"
region = "cn-hangzhou"
bucket = "my-bucket"
access_key = "..."
secret_key = "..."
prefix = "guicang/"
```

```bash
guicang sync --dry-run        # 先看会传多少、多少已是最新
guicang sync --shot 航拍      # 只同步一部分
```

同步是**增量**的（按大小+修改时间），原件按 `<prefix><相对路径>` 上传，不会动本地。
密钥只存在本机 `.guicang\guicang.toml`；这个文件**不要提交进 git**。

## 工作流 D：判定不对怎么办

```bash
guicang explain "2024-05-19 开封古城/城墙/IMG_8842.JPG"   # 逐条证据 + 权重
guicang review --set "<那条路径>" --shot 航拍 --region 开封古城
guicang review --set "<那条路径>" --clear                 # 撤销人工判定，回到机器推断
guicang scan --force                                      # 改了 places/activities 之后强制重算
```

`explain` 的输出长这样，一眼能看出是哪条规则赢了：

```
拍摄类型 航拍（0.84）
  · camera = DJI Mavic 3 Pro（0.84）
  · srt:rel_alt 92.0m > 8m（0.80）
区域 开封古城（0.78）
  · path:开封古城（0.78）
```

## 判定规则速查

**拍摄类型**，从上往下第一条命中的赢，`0.75` 以上才算自动：

| 顺序 | 证据 | 权重 |
|---|---|---|
| 1 | 路径里有 `FPV` / `Avata` | 0.90 |
| 2 | 路径里有 `延时` / `timelapse` / `hyperlapse` | 0.88 |
| 3 | 路径里有 `航拍` / `无人机` / `AERIAL` | 0.86 |
| 4 | 路径里有 `地拍` / `地面` / `手持` | 0.84 |
| 5 | 机型是无人机（`FC\d{4}`、Mavic/Mini/Air/Phantom/Inspire/Matrice/Neo、御/精灵） | 0.84 |
| 6 | `.SRT` 里 `rel_alt > 8m` | 0.80 |
| 7 | 机型是地面设备（Osmo/Pocket/Action/Ronin/iPhone/Canon/SONY…） | 0.80 |
| 8 | 文件名 `DJI_xxxx` 但读不到机型 → **航拍 0.55（待定）** | 0.55 |
| 9 | 什么都没有 → 地拍 0.50（待定） | 0.50 |

**区域**：GPS 落在某地点半径内（0.95 起，边缘 0.62）> 路径里出现地点名或别名（0.66+）。都没有 → 待定。

**活动**：路径里出现活动名或别名（≥0.7 才升到「活动」枝，否则留在「区域」枝）。
届次从「第39届」或「第十一届」里取；取不到就用拍摄年份当那一段（`2026`）。

细节（含每个正则、每个阈值、每个字段的含义）看 `references/evidence.md`。

## 常用命令

| 命令 | 干什么 | 动磁盘？ |
|---|---|---|
| `guicang init [--force]` | 写配置骨架 | 只写 `.guicang\` |
| `guicang doctor` | 自检依赖/配置/索引/树 | 否 |
| `guicang scan [--force] [--quiet]` | 读原件写索引 | 否（只读原件） |
| `guicang report [--csv]` | 分布 + 待定原因 | 否（`--csv` 写一份台账） |
| `guicang review` | 列待定 / `--set` 钉判定 | 写 `manual.json` + 索引 |
| `guicang explain <路径>` | 逐条证据 | 否 |
| `guicang plan [--mode] [--only-review] [--json]` | 预演落位 | 否 |
| `guicang apply --yes [--mode]` | 真落位 | **是** |
| `guicang undo [--run ID] --yes` | 撤回落位 | **是**（只删树里的链接） |
| `guicang serve [--host --port --token --open]` | 本机网页云 | 否 |
| `guicang sync [--dry-run]` | 增量上云 | 只读本地 |
| `guicang export [--collection] [--format]` | 台账/选集 | 写输出目录 |

## 常见坑

- **`_归藏\` 不能被 scan 自己吃回去。** 硬链接也是文件，扫进索引就会无限膨胀。工具已排除
  `.guicang` 与 `_归藏`，自己写脚本时也别越过这条线。
- **硬链接不能跨盘。** 素材在 `D:`、`_归藏\` 也在 `D:` 才能零占用；跨盘时 `plan` 会自动降级成
  `copy`，并在报告里说清楚（几百 GB 的复制要提前告诉用户）。
- **`.SRT` 是航拍的证人。** DJI 的 `.srt` 里有相对高度。用户删过 `.srt` 就只能靠机型或目录名。
- **同名文件不会互相覆盖。** 重名自动加 `-1`、`-2`。`plan` 里有 `冲突` 计数，那个数字是"改名了"，不是"出错"。
- **时间几乎不用信文件修改时间。** 拷贝过一轮的素材 mtime 全是拷贝当天。优先 EXIF / MP4 创建时间 /
  文件名里的日期，mtime 只当 0.35 权重的兜底。

## 参考

- `references/taxonomy.md` — 分类法、`places.toml` / `activities.toml` / `guicang.toml` 全字段
- `references/evidence.md` — 证据链、阈值、机型表、时间来源优先级
- `references/cloud.md` — 网页云、鉴权、局域网、S3 同步、选集与导出衔接
