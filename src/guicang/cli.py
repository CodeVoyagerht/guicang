"""命令行入口。所有动作都从这里进：扫、看、排、落、退、挑、开网页、同步。

设计上只守两条：
1. 任何会写磁盘的动作，先出计划，再要一次确认（``--yes`` 才跳过）。
2. 任何输出都是给人看的——中文、分档、带下一步该敲什么。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import socket
import sys
from pathlib import Path

from . import __version__
from .classify import SHOT_TYPES
from .config import CONFIG_DIR, TIME_DEPTHS, ConfigError, load_config, write_templates
from .export import export_collection, write_csv, write_json, write_pathlist
from .manual import clear_manual, load_manual, manual_path, set_manual
from .materialize import BACKUP_MODES, MODES, apply_plan, build_plan, mirror_tree, undo_run
from .paths import human_size, rel_posix
from .report import text_report, write_pending_csv
from .scanner import scan
from .store import Filters, Store
from .storage import sync_records

DEFAULT_LIMIT = 40


# ── 小工具 ──────────────────────────────────────────────────────────────────

def _resolve_root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    env = os.environ.get("GUICANG_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    here = Path.cwd()
    for candidate in (here, *here.parents):
        if (candidate / CONFIG_DIR).is_dir():
            return candidate
    return here


def _config(args):
    return load_config(_resolve_root(getattr(args, "root", None)))


def _confirm(text: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("  这个环境没法交互确认，真的要动磁盘请加 --yes。")
        return False
    try:
        answer = input(f"{text} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes", "是", "好")


def _pct(part: int, total: int) -> str:
    return f"{part / total * 100:5.1f}%" if total else "  0.0%"


def _print_plan(plan, config) -> None:
    counts = plan.counts()
    print(f"目标树：{config.tree_root}")
    print(f"模式：{plan.mode}（{'仅建硬链接，零额外空间' if plan.mode == 'hardlink' else '复制' if plan.mode == 'copy' else '移动原件'}）")
    for action, number in counts.items():
        if number:
            print(f"  {action:<8}{number:>7}")
    if plan.conflicts:
        print(f"  同名改名{plan.conflicts:>7}")
    if plan.skipped:
        print(f"  跳过    {plan.skipped:>7}")
    print(f"  合计    {len(plan.items):>7} 个，约 {human_size(plan.total_bytes)}")
    shown = 0
    for item in plan.items:
        if shown >= DEFAULT_LIMIT:
            break
        tail = f"  ← {item.note}" if item.note else ""
        try:
            shown_target = item.target.relative_to(plan.root)
        except ValueError:
            shown_target = item.target
        print(f"    {item.rel_path}\n      → {shown_target}{tail}")
        shown += 1
    if len(plan.items) > shown:
        print(f"    …… 其余 {len(plan.items) - shown} 条略（--json 看全量）")


# ── 命令 ────────────────────────────────────────────────────────────────────

def cmd_init(args) -> int:
    root = _resolve_root(args.root)
    root.mkdir(parents=True, exist_ok=True)
    written = write_templates(root, force=args.force)
    config = load_config(root)
    config.config_dir.mkdir(parents=True, exist_ok=True)
    print(f"素材根目录：{root}")
    if written:
        for path in written:
            print(f"  写入 {path.relative_to(root)}")
    else:
        print("  配置已存在，没动它（要重置加 --force）")
    print("\n下一步：")
    print("  1) 改 .guicang/places.toml：把常去的地点、经纬度、半径写进去")
    print("  2) 改 .guicang/activities.toml：把重复举办的活动和它的别名写进去")
    print("  3) guicang scan    扫描并判定")
    print("  4) guicang report  看分布和待定清单")
    print("  5) guicang plan    看分类树会长成什么样（不动磁盘）")
    print("  6) guicang apply --yes   真的建树")
    return 0


def cmd_scan(args) -> int:
    config = _config(args)
    with Store(config.index_path) as store:
        total = {"n": 0}

        def progress(rel: str, decision) -> None:
            total["n"] += 1
            if not args.quiet and total["n"] % 50 == 0:
                print(f"  已读 {total['n']} 个…", flush=True)

        report = scan(config.root, config, store, force=args.force, progress=progress)
    print(report.summary())
    if report.errors:
        print("\n出错：")
        for line in report.errors[:10]:
            print(f"  {line}")
        if len(report.errors) > 10:
            print(f"  …… 其余 {len(report.errors) - 10} 条")
    print(f"\n索引：{config.index_path}")
    if report.needs_review:
        print(f"有 {report.needs_review} 个拿不准，运行 guicang review --list 看原因")
    return 0


def cmd_report(args) -> int:
    config = _config(args)
    with Store(config.index_path) as store:
        if store.count(Filters()) == 0:
            print("索引还是空的，先跑 guicang scan")
            return 1
        print(text_report(config, store))
        pending = store.query(Filters(status="needs_review"), limit=args.limit)
        if args.csv and pending:
            target = config.config_dir / "待定清单.csv"
            write_pending_csv(store, target)
            print(f"\n待定清单已导出：{target}")
    return 0


def cmd_plan(args) -> int:
    config = _config(args)
    with Store(config.index_path) as store:
        records = store.all()
        if not records:
            print("索引还是空的，先跑 guicang scan")
            return 1
        plan = build_plan(config, records, mode=args.mode, only_review=args.only_review)
    if args.json:
        print(json.dumps({
            "mode": plan.mode,
            "counts": plan.counts(),
            "total_bytes": plan.total_bytes,
            "items": [{
                "rel_path": item.rel_path,
                "target": str(item.target),
                "action": item.action,
                "note": item.note,
            } for item in plan.items],
        }, ensure_ascii=False, indent=2))
        return 0
    _print_plan(plan, config)
    print("\n这只是计划，磁盘没动。满意就：guicang apply --yes")
    return 0


def cmd_apply(args) -> int:
    config = _config(args)
    with Store(config.index_path) as store:
        records = store.all()
        if not records:
            print("索引还是空的，先跑 guicang scan")
            return 1
        plan = build_plan(config, records, mode=args.mode, only_review=args.only_review)
        if not plan.items:
            print("没有可落位的素材。")
            return 0
        _print_plan(plan, config)
        if not _confirm(f"\n确认按上面的计划落位 {len(plan.items)} 个文件？", args.yes):
            print("已取消，磁盘没动。")
            return 1
        report = apply_plan(plan, store, dry_run=False)
    print("\n" + report.summary())
    for line in report.errors[:10]:
        print(f"  {line}")
    if report.run_id:
        print(f"\n要撤回这一批：guicang undo --run {report.run_id}")
    return 0


def cmd_undo(args) -> int:
    config = _config(args)
    with Store(config.index_path) as store:
        run_id = args.run or ""
        if not run_id:
            runs: list[str] = []
            for row in store.journal(limit=300):
                detail = str(row.get("detail") or "")
                if not detail.startswith("run:"):
                    continue
                rid = detail[4:]
                if rid and rid not in runs:
                    runs.append(rid)
            if not runs:
                print("没有可撤回的记录。")
                return 0
            run_id = runs[0]
            print(f"最近一次落位：{run_id}（要撤别的加 --run）")
        removed, dirs, ignored, errors = undo_run(store, run_id, dry_run=True)
        print(f"撤回 {run_id}：将删除 {removed} 个链接/副本，跳过 {ignored} 个（原件与移动过的文件不删）")
        if args.yes:
            removed, dirs, ignored, errors = undo_run(store, run_id, dry_run=False, tree_root=config.tree_root)
            print(f"已删除 {removed} 个，收走空目录 {dirs} 个，跳过 {ignored} 个")
        elif not _confirm(f"确认删除这 {removed} 个链接/副本？（原件不动）", args.yes):
            print("已取消。")
            return 1
    for line in errors[:10]:
        print(f"  {line}")
    return 0


def cmd_review(args) -> int:
    config = _config(args)
    if args.set:
        rel = args.set.replace("\\", "/")
        if args.clear:
            clear_manual(config.root, rel)
            print(f"已清掉 {rel} 的人工判定")
        else:
            if not any([args.shot, args.region, args.area, args.activity, args.edition, args.branch]):
                print("至少要给一个 --shot / --region / --area / --activity / --edition / --branch")
                return 1
            set_manual(config.root, rel, shot_type=args.shot, region=args.region,
                       area=args.area, activity=args.activity, edition=args.edition,
                       branch=args.branch)
            print(f"已记下 {rel} 的人工判定，下次 scan 生效：")
            for key, value in (load_manual(config.root).get(rel) or {}).items():
                print(f"  {key} = {value}")
        print(f"人工判定文件：{manual_path(config.root)}")
        # 钉住就是为了马上看到结果，别让人再去记一条 scan
        with Store(config.index_path) as store:
            report = scan(config.root, config, store)
        print(f"已重新判定：待定 {report.needs_review} 条。")
        return 0

    with Store(config.index_path) as store:
        rows = store.query(Filters(status="needs_review"), limit=args.limit)
        total = store.count(Filters(status="needs_review"))
    if not rows:
        print("没有待定的素材，全部自动归类了。")
        return 0
    print(f"待定 {total} 个（列出前 {len(rows)} 个）：\n")
    for record in rows:
        print(f"  [{record.id}] {record.rel_path}")
        where = "/".join(part for part in (record.province, record.city, record.region,
                                           record.area) if part) or "未识别"
        print(f"      拍摄类型 {record.shot_type or '?'}（{record.shot_conf or 0:.2f}）"
              f" · 区域 {where}（{record.region_conf or 0:.2f}）")
        if record.camera:
            print(f"      机型 {record.camera} · GPS {record.gps_source or '无'}")
        if record.notes:
            print(f"      原因 {record.notes}")
        hint = [f'--set "{record.rel_path}"']
        hint.append(f"--shot {record.shot_type}" if record.shot_type else "--shot <航拍/地拍/延时/FPV/其他>")
        hint.append(f'--region "{record.region}"' if record.region else '--region "<地点名>"')
        if record.region and not record.area:
            hint.append('--area "<园区区域>"')
        print("      钉住：guicang review " + " ".join(hint))
        print(f"      如果它其实是抖音素材：guicang review --set \"{record.rel_path}\" --branch 抖音")
    print("\n也可以不改判定、直接把待定素材单独整理：guicang plan --only-review")
    return 0


def cmd_explain(args) -> int:
    config = _config(args)
    rel = args.rel.replace("\\", "/")
    with Store(config.index_path) as store:
        record = store.get_asset(rel)
    if record is None:
        print(f"索引里没有 {rel}。先 guicang scan，注意路径是相对素材根目录的。")
        return 1
    print(f"{record.rel_path}\n")
    print(f"  时间      {record.captured_at or '未知'}（来源 {record.captured_source or '无'}，"
          f"把握 {record.captured_conf or 0:.2f}）")
    print(f"  拍摄类型  {record.shot_type or '?'}（{record.shot_conf or 0:.2f}）")
    # 一条素材在地点上的完整身份：省 → 市 → 地点 → 园区区域
    chain = " → ".join(part for part in (record.province, record.city, record.region, record.area) if part)
    print(f"  区域      {chain or '未识别'}（{record.region_conf or 0:.2f}）")
    if record.activity:
        print(f"  活动      {record.activity} {record.edition or ''}（{record.activity_conf or 0:.2f}）")
    branch_label = {"region": "区域", "activity": "活动", "douyin": "抖音"}.get(record.branch or "", record.branch or "?")
    print(f"  分支      {branch_label}（{record.branch_conf or 0:.2f}）· 状态 {record.status}")
    print(f"  落位      {record.target_rel or '—'}")
    if record.camera:
        print(f"  机型      {record.camera}")
    if record.lat is not None and record.lon is not None:
        print(f"  坐标      {record.lat:.5f}, {record.lon:.5f}（来源 {record.gps_source or '无'}）")
    for title, raw in (("拍摄类型", record.shot_evidence), ("区域", record.region_evidence),
                       ("活动", record.activity_evidence), ("分支", record.branch_evidence)):
        rows = _loads(raw)
        if not rows:
            continue
        print(f"\n  {title}的证据：")
        for row in rows:
            print(f"    {row.get('weight', 0):.2f}  {row.get('detail', '')}")
    return 0


def _loads(raw) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def cmd_serve(args) -> int:
    config = _config(args)
    host = args.host or config.web_host
    port = args.port or config.web_port
    token = args.token or config.web_token
    if not token and host not in ("127.0.0.1", "localhost", "::1"):
        token = secrets.token_urlsafe(12)
        print(f"绑在 {host} 上，已临时生成访问 token。")
    # 口令要写回 config：Handler 是从 config.web_token 里取口令的，
    # 只在本地变量里算一遍等于没设。
    config.web_token = token
    from .webapp import run

    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{display_host}:{port}/"
    if token:
        url += f"?t={token}"
    print(f"归藏已启动：{url}")
    if host in ("0.0.0.0", "::"):
        for ip in _local_ips():
            print(f"  手机同网段可开：http://{ip}:{port}/{'?t=' + token if token else ''}")
    print("按 Ctrl+C 停。")
    run(config, host=host, port=port, verbose=args.verbose, open_browser=args.open)
    return 0


def _local_ips() -> list[str]:
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips


def cmd_sync(args) -> int:
    config = _config(args)
    if not config.sync.ready:
        print("云端还没配好。在 .guicang/guicang.toml 的 [sync] 段填 backend 与地址：")
        print("  本机目录备份：backend = \"local\"  + [sync.local] dir = \"D:\\\\归藏备份\"")
        print("  对象存储：backend = \"s3\" + [sync.s3] endpoint/bucket/access_key/secret_key")
        return 1
    with Store(config.index_path) as store:
        filters = Filters(
            region=args.region or None, activity=args.activity or None,
            shot_type=args.shot or None, year=args.year or None,
        )
        records = store.query(filters, limit=args.limit)
        if not records:
            print("没有选中素材。")
            return 1
        print(f"准备同步 {len(records)} 个，约 {human_size(sum(r.size or 0 for r in records))}…")
        report = sync_records(config, store, records, dry_run=args.dry_run)
    if args.dry_run:
        total = sum(r.size or 0 for r in records)
        print(f"\n将上传 {report.pending} 个 · 已是最新 {report.skipped} 个 · 失败 {report.failed} 个 · "
              f"约 {human_size(total if report.pending else 0)}")
        print("（这是演练，没有真的上传；去掉 --dry-run 才会传）")
    else:
        print(f"\n上传 {report.uploaded} · 跳过 {report.skipped} · 失败 {report.failed} · "
              f"共 {human_size(report.bytes_sent)}")
    for line in report.errors[:10]:
        print(f"  {line}")
    return 0 if not report.failed else 1


def cmd_backup(args) -> int:
    """把分类树原样镜像到备份盘。主库怎么分，备份盘就怎么分。"""
    config = _config(args)
    target = Path(args.to).expanduser() if args.to else config.backup_root
    if target is None:
        print("还没配备份盘。两种办法：")
        print("  ① 在 .guicang/guicang.toml 里加：")
        print("       [backup]")
        print("       dir = \"E:\\\\归藏备份\"")
        print("  ② 或者这次直接给路径：guicang backup --to E:\\归藏备份")
        return 1
    target = target.resolve()
    with Store(config.index_path) as store:
        if not args.yes and not args.dry_run:
            if not _confirm(f"把 {config.tree_root} 镜像到 {target}？", args.yes):
                print("已取消。")
                return 1
        report = mirror_tree(config, store, target, mode=args.mode, dry_run=args.dry_run)
    if args.dry_run:
        print(f"演练：会镜像到 {target}（模式 {args.mode}），先不写盘。")
        print(report.summary())
    else:
        print(report.summary())
        print(f"备份盘：{target}")
        for line in report.errors[:10]:
            print(f"  {line}")
    return 0 if not report.errors else 1


def _resolve_collection(store: Store, wanted: str) -> tuple[int, str] | None:
    """选集可以按 id 也可以按名字给：人记名字，脚本记 id，两种都得能用。"""
    if wanted.isdigit():
        for collection in store.collections():
            if collection.id == int(wanted):
                return collection.id, collection.name
        return None
    found = store.find_collection(wanted)
    if found:
        return found.id, found.name
    matches = [c for c in store.collections() if wanted in c.name]
    if len(matches) == 1:
        return matches[0].id, matches[0].name
    return None


def cmd_export(args) -> int:
    config = _config(args)
    out_dir = Path(args.out).expanduser().resolve() if args.out else config.config_dir / "exports"
    count = 0
    with Store(config.index_path) as store:
        if args.collection:
            found = _resolve_collection(store, args.collection)
            if found is None:
                print(f"没有叫「{args.collection}」的选集。现有："
                      + ("、".join(c.name for c in store.collections()) or "还没有选集"))
                return 1
            collection_id, name = found
            paths = export_collection(store, collection_id, out_dir,
                                      formats=tuple(part.strip() for part in args.format.split(",") if part.strip()))
            count = len(store.collection_items(collection_id))
            print(f"选集「{name}」共 {count} 条。")
        else:
            filters = Filters(
                region=args.region or None, activity=args.activity or None,
                shot_type=args.shot or None, year=args.year or None,
                month=args.month or None, status=args.status or None,
            )
            records = store.query(filters, limit=100000)
            if not records:
                print("筛选条件没命中任何素材。")
                return 1
            count = len(records)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
            paths = []
            for fmt in (args.format or "csv").split(","):
                fmt = fmt.strip()
                if fmt == "csv":
                    paths.append(write_csv(records, out_dir / f"台账-{stamp}.csv"))
                elif fmt == "json":
                    paths.append(write_json(records, out_dir / f"台账-{stamp}.json", config=config))
                elif fmt == "txt":
                    paths.append(write_pathlist(records, out_dir / f"路径-{stamp}.txt"))
                elif fmt:
                    print(f"不认识的格式 {fmt}，跳过")
    for path in paths:
        print(f"  {path}  ({human_size(path.stat().st_size)})")
    print(f"\n导出 {count or len(paths)} 个素材到 {out_dir}")
    if count <= 0:
        print("（选集是空的，先 guicang serve 里挑几条）")
    return 0


def cmd_doctor(args) -> int:
    config = _config(args)
    print(f"归藏 {__version__} · Python {sys.version.split()[0]}")
    print(f"素材根目录 {config.root}")
    ok = True
    if not config.config_dir.is_dir():
        print("  ✗ 还没有 .guicang/，先 guicang init")
        ok = False
    else:
        print(f"  ✓ 配置目录 {config.config_dir}")
        print(f"    地点 {len(config.places)} 个 · 活动 {len(config.activities)} 个"
              f" · 时间粒度 {config.time_depth}")
        if not config.places:
            print("    ! places.toml 是空的，所有素材都会进「待定」")
    try:
        from PIL import Image  # noqa: F401
        print("  ✓ Pillow 可用（照片缩略图）")
    except ImportError:
        print("  - 没装 Pillow：网页里照片缩略图会退回原图，视频要 ffmpeg")
    import shutil
    if shutil.which("ffprobe"):
        print("  ✓ ffprobe 可用（视频分辨率、时长）")
    else:
        print("  - 没有 ffprobe：视频的时长/分辨率只能靠 MP4 盒子读，读不到就留空")
    if shutil.which("ffmpeg"):
        print("  ✓ ffmpeg 可用（视频缩略图）")
    else:
        print("  - 没有 ffmpeg：视频卡片没有画面，只能点开看")
    if config.sync.ready:
        print(f"  ✓ 云端 {config.sync.backend}")
    else:
        print("  - 云端未配置（只影响异地备份与外网访问，不影响整理）")
    if config.index_path.is_file():
        with Store(config.index_path) as store:
            stats = store.stats()
        print(f"  ✓ 索引 {config.index_path}：{stats.get('assets', 0)} 个素材，"
              f"{human_size(stats.get('bytes', 0))}")
        pending = (stats.get("status") or {}).get("needs_review", 0)
        if pending:
            print(f"    ! {pending} 个待定：guicang review --list")
    else:
        print("  - 还没建索引：guicang scan")
    if config.tree_root.exists():
        print(f"  ✓ 分类树 {config.tree_root}")
    else:
        print("  - 分类树还没建：guicang plan 看一眼，再 guicang apply --yes")
    return 0 if ok else 1


# ── 参数 ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    # --root 在子命令前后都能用：两处都注册，子命令那一处覆盖全局那一处。
    # 用户不会记得住 argparse 的「全局选项必须写在子命令前面」这条规矩。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS,
                        help=f"素材根目录（默认往上找含 {CONFIG_DIR} 的目录）")

    parser = argparse.ArgumentParser(
        prog="guicang",
        description="归藏：把素材按 区域 / 活动 → 时间 → 拍摄类型 归好位。原件不动。",
    )
    parser.add_argument("--version", action="version", version=f"guicang {__version__}")
    parser.add_argument("--root", help=f"素材根目录（默认往上找含 {CONFIG_DIR} 的目录）")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, parents=[common])

    p = add("init", "在素材根目录写配置骨架")
    p.add_argument("--force", action="store_true", help="覆盖已有配置")
    p.set_defaults(func=cmd_init)

    p = add("scan", "扫描素材，写入索引（只读原件）")
    p.add_argument("--force", action="store_true", help="没变的文件也重读（改了清单之后用）")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = add("report", "看分布、待定原因")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--csv", action="store_true", help="顺带导出待定清单")
    p.set_defaults(func=cmd_report)

    p = add("plan", "看分类树会建在哪（不动磁盘）")
    p.add_argument("--mode", choices=MODES, default="hardlink")
    p.add_argument("--only-review", action="store_true", help="只处理待定的")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = add("apply", "真的建分类树")
    p.add_argument("--mode", choices=MODES, default="hardlink")
    p.add_argument("--only-review", action="store_true")
    p.add_argument("--yes", action="store_true", help="跳过确认")
    p.set_defaults(func=cmd_apply)

    p = add("undo", "撤回一次落位（原件不删）")
    p.add_argument("--run", default="", help="运行 id，默认最近一次")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_undo)

    p = add("review", "看待定清单，或人工钉住某条判定")
    p.add_argument("--list", action="store_true", help="列待定（默认行为）")
    p.add_argument("--set", default="", metavar="相对路径", help="给某个文件钉判定")
    p.add_argument("--shot", choices=SHOT_TYPES, help="拍摄类型")
    p.add_argument("--region", help="区域（地点名）")
    p.add_argument("--area", help="园区里的区域名（places.toml 的 areas 里列过的）")
    p.add_argument("--activity", help="活动名")
    p.add_argument("--edition", help="届次，如 2026-第3届")
    p.add_argument("--branch", choices=["抖音", "区域", "活动"], help="强制进哪棵树（抖音素材单独一棵）")
    p.add_argument("--clear", action="store_true", help="清掉这条的人工判定")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_review)

    p = add("explain", "为什么这条素材被这样归类")
    p.add_argument("rel", metavar="相对路径")
    p.set_defaults(func=cmd_explain)

    p = add("serve", "开本机网页：翻库、筛选、挑素材")
    p.add_argument("--host", default="")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--token", default="", help="访问口令，给手机用时设一个")
    p.add_argument("--open", action="store_true", help="顺便打开浏览器")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = add("sync", "把素材送到云端（本机目录或对象存储）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--region")
    p.add_argument("--activity")
    p.add_argument("--shot", choices=SHOT_TYPES)
    p.add_argument("--year")
    p.add_argument("--limit", type=int, default=100000)
    p.set_defaults(func=cmd_sync)

    p = add("backup", "把分类树原样镜像到备份盘")
    p.add_argument("--to", default="", help="备份根目录（默认用 [backup] dir）")
    p.add_argument("--mode", choices=BACKUP_MODES, default="hardlink",
                   help="hardlink=同盘零占用（默认），copy=真复制")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_backup)

    p = add("export", "导出台账 / 选集清单")
    p.add_argument("--collection", default="", help="选集 id 或名字")
    p.add_argument("--out", default="", help="输出目录")
    p.add_argument("--format", default="csv", help="csv,json,txt 可组合")
    p.add_argument("--region")
    p.add_argument("--activity")
    p.add_argument("--shot", choices=SHOT_TYPES)
    p.add_argument("--year")
    p.add_argument("--month")
    p.add_argument("--status", choices=["auto", "needs_review"])
    p.set_defaults(func=cmd_export)

    p = add("doctor", "自检：依赖、配置、索引、分类树")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except ConfigError as error:
        print(f"配置有问题：{error}")
        print("改好再跑；不知道自己改坏了什么，可以把 .guicang/guicang.toml 删掉重跑 guicang init。")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
