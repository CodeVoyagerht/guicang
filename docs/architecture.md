# 架构

## 一句话

**原件不动 + 索引是真相 + 证据驱动判定**。树是索引的一次物化，可以随时推倒重建。

## 数据流

```
原件（D:\素材\2024-05-18 开封古城\...）
   │  probe.py：EXIF / MP4 mvhd / .SRT 遥测 / 文件名日期   ← 只读
   ▼
Probe（时间、GPS、机型、宽高、时长、旁车事实）
   │  classify.py：拍摄类型 / 区域 / 活动 / 届次，各给一串带权重的证据
   ▼
Decision（shot_type + conf + evidence，region …，activity + edition，branch，status）
   │  manual.py：人工钉住的判定在这里覆盖（conf=1.0）
   │  taxonomy.py：Decision + 时间 → 相对路径
   ▼
Store（.guicang/index.db：assets / collections / journal / kv）
   │  materialize.py：plan（零副作用）→ apply（硬链接/复制/移动）→ undo
   ▼
_归藏\ 分类树  ──►  webapp.py（serve：翻/筛/选集）  ──►  export.py（CSV/JSON/txt）
                     storage.py（sync：本机目录 / S3）──► 云
```

两层之间的东西都可以单独替换：想换分类法只动 `taxonomy.py` + 配置，想换判定只动 `classify.py`，
索引和树不用重建。

## 为什么不移动原件

归档工具最不能接受的失败是"我的原件被你搬走了"。所以：

| 模式 | 磁盘代价 | 原件 | `undo` 可撤 |
|---|---|---|---|
| `hardlink`（默认） | 同盘 0 字节 | 原地 | ✅ |
| `copy` | 全量 | 原地 | ✅（删副本） |
| `move` | 0 | **被搬走** | ❌ 明确拒绝撤销 |

跨盘时 `hardlink` 无法成立，`plan` 自动降级 `copy` 并在报告里写清楚——不是静默降级。

## 索引 schema（要点）

```sql
assets(id TEXT PRIMARY KEY, rel_path UNIQUE, size, mtime, kind,
       captured_at, captured_source, captured_conf,
       shot_type, shot_conf, shot_evidence,
       region, region_conf, region_evidence,
       activity, edition, activity_conf, activity_evidence,
       target_rel, status, branch, notes, ...)
collections(id, name, note, created_at, updated_at)
collection_items(collection_id, asset_id, ordinal)
journal(id, run_id, op, src_rel, dst_rel, detail, created_at)   -- apply/undo 账本
kv(key, value)                                                  -- sync 状态等
```

- `rel_path` 唯一，是原件的身份（相对素材根，正斜杠）。
- 五段 `*_evidence` 存 JSON 数组，`explain` 直接读它——**不重算**，保证"当时为什么这么判"可回放。
- `journal` 记每次 `apply` 的 `run_id`，`undo --run` 靠它精确回退，`_prune_empty_dirs` 顺带收空目录。
- `kv` 存 `sync:<backend>` 的文件指纹（`大小:mtime`），同步靠它判增量。

## 增量与重算

`scan` 对每个文件算 `(size, mtime)` 指纹：

- 指纹变了 → 重读文件（`probe_file`），重建 `Probe`。
- 指纹没变 → **从索引里重建 `Probe`**（`_from_record`），跳过读文件但仍然走一遍
  `decide → manual.apply → target_rel`。

第二点是刻意的：改了 `places.toml`、改了活动别名、用 `review --set` 钉了一条，
都应该立刻生效，不该逼用户 `--force` 全量重读。`--force` 只留给"文件内容变了但大小和 mtime
都没变"这种极端情况。

## 网页为什么不引前端框架

单页三件套（`web/ui.html` + `ui.css` + `ui.js`，原生 JS，零构建）。

理由：这是个**本地工具**，不是产品。没有构建步骤意味着 `pip install` 完就能用、改一行刷新就见效、
十年后还能跑。代价是代码没有类型检查——用 92 条测试兜住行为，而不是用框架兜住结构。

后端用 `http.server`（标准库）+ `BaseHTTPRequestHandler`，视频走 Range 206，
缩略图懒生成并缓存到 `.guicang/thumbs`。

## 零依赖的边界

| 功能 | 做法 | 不装会怎样 |
|---|---|---|
| EXIF / TIFF | 手写 IFD 解析（`exif.py`） | — |
| MP4 时间/坐标/时长 | 手写 moov 盒子跳读（不吞整段视频） | — |
| DJI `.SRT` 遥测 | 正则（兼容 `[rel_alt: 92.0 abs_alt: 132.0]` 这种同行多字段） | — |
| 缩略图 | Pillow → ffmpeg → 原图三级降级 | 网页变慢、费流量 |
| 视频时长 | mvhd 的 timescale/duration | 显示空 |
| 云同步 | 手写 SigV4 | — |

`ffprobe` 是**可选增强**：装了就能补上旋转、更准的时长；没装也能跑（本机就没有 ffprobe，
所有测试都在没有它的前提下通过）。

## 一条测试的哲学

92 条 `unittest`，不依赖 pytest、不依赖真实素材——`demo.py` 现场合成带真实 EXIF 的 JPEG、
带 `mvhd` 与 `ISO6709` 的 MP4、带遥测的 SRT。测试断言的是**行为**：

- 硬链接的 inode 与原文件相同、`bytes_copied == 0`；
- `undo` 之后树目录清空、原件一个不少；
- 同名文件自动加 `-1`，不覆盖；
- MP4 时间不做时区换算（这条钉死了曾经踩过的坑）；
- 待定原因里必须出现「地点」二字（否则人看不懂为什么没归）。

CI 在 Windows + Linux、Python 3.11 + 3.13 上跑同一套，外加一条端到端冒烟
（demo → scan → plan → apply → undo）。
