# 网页云、同步与导出

三件事，按需要一层层叠：**本机翻库**（`serve`）→ **手机看同一份**（token + 局域网）→
**异地备份**（`sync`）。导出是另一条线：把「选好的那批」变成剪辑软件能读的清单。

## serve：本机网页

```bash
guicang serve --open                        # 127.0.0.1:8787，自动开浏览器
guicang serve --host 0.0.0.0 --token 口令    # 手机/平板在同一 WiFi 下能开
```

首页给的是「素材墙」：缩略图网格 + 左侧筛选 + 底部勾选条。

- **筛选**：拍摄类型（带计数）、区域、活动、月、状态（自动/待定）、搜索框（文件名+地点+活动）。
- **勾选**：点卡片右上角勾，底部出现「已选 N · 存为选集 · 导出 · 清空」。
- **详情**：点卡片打开，左边大图/视频（视频直接播，支持拖进度——后端走 Range 206），
  右边是判定与证据三条。
- **选集**：存了之后在左侧出现，点一下就是「只看这批」。选集存在索引里，跟着库走。

**缩略图**：装了 Pillow 就现场生成照片缩略图；没装就退回原图（慢一点、费流量）。
视频缩略图需要 `ffmpeg` 在 PATH，否则显示占位图。装了懒加载 + 内存缓存，翻一遍不会重复解码。

### 鉴权

| 场景 | 行为 |
|---|---|
| `token` 空 + 绑定 `127.0.0.1` | 不校验（只有本机能连） |
| `token` 空 + 绑定 `0.0.0.0` | **自动生成一个随机口令并打印**，URL 里带上 `?t=...` |
| 设了 `token` | 所有 `/api/*` 与页面都要求口令，三种给法都行：`?t=口令`、`X-Guicang-Token: 口令`、cookie `guicang_token=口令` |

口令错了返回 401，页面提示「口令不对」。URL 里的 `?t=` 会被 localStorage 记住，手机加书签即可。

> 别把 `serve --host 0.0.0.0` 挂到公网。它是给自家 WiFi 用的。要外网访问，用对象存储 + 自己的
> 静态托管，或者套一层 Tailscale/Cloudflare Tunnel，别裸奔。

### HTTP 接口（写自己的客户端时用）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 单页应用 |
| GET | `/api/status` | 库统计（总数、待定数、总字节、根目录）、地点与活动清单、`last_scan` |
| GET | `/api/facets` | 五个维度的分面计数 |
| GET | `/api/assets?limit&offset&shot&region&activity&month&status&q&collection` | 列表 |
| GET | `/api/asset?id=` | 单条 + 证据（`evidence.shot/region/activity/branch`） |
| POST | `/api/review` | 改判定：`{"paths":[...], "shot_type"/"region"/"area"/"activity"/"edition"/"branch", "action":"set"\|"clear"}`，写 `manual.json` 并立刻增量重扫 |
| POST | `/api/rescan` | 重新扫描：`{"force":false}`，返回本次计数并写下 `last_scan` |
| GET | `/api/thumb?id=&w=` | 缩略图（JPEG） |
| GET | `/api/media?id=` | 原图/原视频，支持 `Range` |
| GET | `/api/collections` | 选集列表 |
| GET | `/api/collections/<id>` | 选集内容 |
| POST | `/api/collections` | 建选集（JSON：`{"name":..., "note":..., "ids":[...]}`） |
| POST | `/api/collections/<id>/items` | 往选集加（`{"ids":[...]}`） |
| DELETE | `/api/collections/<id>/items` | 从选集移 |
| DELETE | `/api/collections/<id>` | 删选集 |
| GET | `/api/export?collection=<id>&format=csv,json,txt` | 导出，返回文件 |

所有 `id` 都是索引里的整数主键，不是路径。

## sync：增量上云

两种后端，配置见 `references/taxonomy.md` 的 `[sync]` 段。

```bash
guicang sync --dry-run                 # 只报「将上传 N / 已最新 M / 失败 K / 约 X」
guicang sync                           # 真传
guicang sync --shot 航拍 --year 2026    # 只传一部分
```

- **增量判据**：`大小:修改时间(秒)`，状态存在索引的 kv 表里（键 `sync:<backend>`）。
  文件内容改了但大小和 mtime 都没变的情况不会重传——真要重传就改一下 mtime。
- **local 后端**：传到一个目录。网盘同步盘（坚果云/OneDrive）、移动硬盘都行；
  等于给「异地备份」多一层保险。
- **s3 后端**：内置 SigV4 签名，零第三方依赖。阿里云 OSS、腾讯云 COS、MinIO、AWS S3、
  Cloudflare R2 都能用（R2 的 `region` 填 `auto`）。
- 对象键是 `<prefix><素材相对路径>`，保留目录结构，别人/别的工具也能读懂。
- `presign` 只在 S3 后端返回预签名 URL；`local` 返回 `None`。
- 密钥只在本机 `.guicang\guicang.toml`。**这个文件不进版本库**（`.gitignore` 已排）。

## export：台账与清单

三件套，可以任意组合（`--format csv,json,txt`）：

| 格式 | 给谁用 | 内容 |
|---|---|---|
| `csv` | 人（Excel/飞书表格） | 17 列中文表头，带 BOM（Excel 直接打开不乱码） |
| `json` | 程序（`cutwright`、自写脚本） | 每条记录全字段 + 选集名 + 库根 |
| `txt` | 剪辑软件 | 每行一个**绝对路径**，可直接喂给时间线工具 |

CSV 的 17 列固定顺序：序号、文件名、拍摄时间、年、月、拍摄类型、区域、活动、届次、分支、
状态、机型、分辨率、时长(秒)、大小、相对路径、绝对路径。

```bash
guicang export --collection "开封航拍精选"        # 选集 id 或名字都行
guicang export --collection "开封航拍精选" --format csv,txt --out D:\交付
guicang export --shot 航拍 --year 2026 --format csv --out D:\台账     # 不建选集，按条件导
```

文件名自动带时间戳、不会覆盖旧台账：选集导出是 `<选集名>-<20261115-1830>.csv`，
条件导出是 `台账-<stamp>.csv` / `路径-<stamp>.txt`。
没有 `--out` 时一律落到 `.guicang\exports\`（配置目录里，不进分类树、不参与扫描）。

## 和 cutwright / 达芬奇 / 剪映怎么接

**结账点就是那份 `txt` 或 `json`**：归藏负责「挑出来的是哪几个文件」，剪辑软件负责「怎么剪」。

```bash
# 归藏这边：网页里勾完 → 存选集 → 导清单
guicang export --collection "菊花节成片" --format txt,json --out D:\交付\菊花节

# 剪辑那边：cutwright 按清单出片（技能 cutwright）
# 达芬奇：把 txt 里的路径批量拖进媒体池，或直接用 JSON 匹配
```

`txt` 是给「拖」的，`json` 是给「程序读」的。两者路径保持一致，不会出现「清单里 30 个、
实际只找到 28 个」那种对不上的情况——因为导出时读的是索引里的绝对路径，和树里的硬链接无关。

## 常见问题

**手机打不开 8787。** 三件事按顺序查：`--host 0.0.0.0` 给了吗、Windows 防火墙放行了吗
（第一次运行会弹窗，点「允许专用网络」）、手机和电脑是不是同一个 WiFi。

**页面里视频不播。** 少了 `ffmpeg`（缩略图）或浏览器不支持该编码（HEVC 在 Chrome 里常不播）。
换 Edge/Safari 试试，或直接下原片。

**缩略图全是同一个灰块。** Pillow 没装，且 `ffmpeg` 也不在 PATH。装一个就好：
`pip install "guicang[exif]"`。

**同步说「失败 N 个」。** 看 `--dry-run` 的输出里每条的错误：密钥错、bucket 不存在、
endpoint 少了协议头（要写 `https://`），这三种占九成。
