"""DRG 分组器命令行工具（便于无前端调试）。

用法：
    python scripts/run_cli.py import                # 导入 xlsx → SQLite
    python scripts/run_cli.py group --zyzd A01.001 --nl 35 --xb 1
    python scripts/run_cli.py batch cases.xlsx     # 批量分组
    python scripts/run_cli.py benchmark --n 1000   # 性能基准
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

# 让脚本能找到 app 包
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import DEFAULT_RULES_XLSX  # noqa: E402
from app.db.session import init_db, get_session  # noqa: E402
from app.engine.grouper.pipeline import Case, group_case  # noqa: E402
from app.engine.index import build_index  # noqa: E402
from app.importers.xlsx_importer import import_rules_xlsx  # noqa: E402


def cmd_import(args):
    init_db()
    session = get_session()
    try:
        summary = import_rules_xlsx(args.xlsx or DEFAULT_RULES_XLSX, session)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        session.close()


def cmd_group(args):
    init_db()
    session = get_session()
    try:
        idx = build_index(session)
        case = Case(
            zyzd=args.zyzd,
            zyss=args.zyss,
            qtzd_list=[x for x in (args.qtzd or "").split(",") if x.strip()],
            qtss_list=[x for x in (args.qtss or "").split(",") if x.strip()],
            nl=args.nl, xb=args.xb, xsrtl=args.xsrtl, xsrtz=args.xsrtz,
        )
        result = group_case(case, idx)
        print(f"MDC:    {result.mdc_code}")
        print(f"ADRG:   {result.adrg_code}")
        print(f"DRG:    {result.drg_code}")
        print(f"CC:     {result.cc_level}")
        print(f"状态:   {result.error_type}")
        print(f"耗时:   {result.duration_ms}ms")
        if args.verbose:
            print("\n证据链:")
            print(json.dumps(result.evidence, ensure_ascii=False, indent=2))
    finally:
        session.close()


def cmd_batch(args):
    import openpyxl
    init_db()
    session = get_session()
    try:
        idx = build_index(session)
        wb = openpyxl.load_workbook(args.input, read_only=True, data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            print("empty file")
            return
        header = [str(h or "").strip() for h in rows[0]]
        col = {h: i for i, h in enumerate(header)}

        out_path = args.output or str(Path(args.input).with_suffix(".result.csv"))
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([
                "case_id", "MDC", "ADRG", "DRG", "CC",
                "状态", "耗时ms", "错误",
            ])
            for i, row in enumerate(rows[1:], start=1):
                if not row or not row[col.get("病案号", 0)]:
                    continue
                case = Case(
                    zyzd=str(row[col.get("主要诊断", col.get("ZYZD", 1))] or "").strip(),
                    zyss=str(row[col.get("主要手术", col.get("ZYSS", 2))] or "").strip() or None,
                    qtzd_list=[x.strip() for x in str(row[col.get("其他诊断", 3)] or "").replace(";", ",").split(",") if x.strip()],
                    qtss_list=[x.strip() for x in str(row[col.get("其他手术", 4)] or "").replace(";", ",").split(",") if x.strip()],
                    nl=_try_int(row[col.get("年龄", 5)] if col.get("年龄") is not None else None),
                    xb=_try_int(row[col.get("性别", 6)] if col.get("性别") is not None else None),
                )
                res = group_case(case, idx)
                w.writerow([
                    row[col.get("病案号", 0)],
                    res.mdc_code, res.adrg_code, res.drg_code, res.cc_level,
                    res.error_type, res.duration_ms, res.error_msg or "",
                ])
                if i % 100 == 0:
                    print(f"  processed {i}")
        print(f"done → {out_path}")
    finally:
        session.close()


def cmd_benchmark(args):
    init_db()
    session = get_session()
    try:
        idx = build_index(session)
        # 用真实感数据循环：随机主诊断
        import random
        sample_codes = ["A01.001", "I50.900", "J18.900", "K35.800", "S72.000"]
        cases = []
        for _ in range(args.n):
            cases.append(Case(
                zyzd=random.choice(sample_codes),
                zyss=random.choice(["33.5100", "00.6600", None]),
                qtzd_list=random.sample(list(idx.set_index.keys()), k=min(3, len(idx.set_index))),
                nl=random.randint(1, 90),
                xb=random.choice([1, 2]),
            ))
        t0 = time.perf_counter()
        results = [group_case(c, idx) for c in cases]
        elapsed = time.perf_counter() - t0
        success = sum(1 for r in results if r.error_type == "success")
        fallback = sum(1 for r in results if r.error_type == "fallback")
        err = sum(1 for r in results if r.error_type == "error")
        print(f"总耗时: {elapsed:.3f}s   每条: {elapsed/len(cases)*1000:.2f}ms")
        print(f"成功: {success}  兜底: {fallback}  异常: {err}")
    finally:
        session.close()


def _try_int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def main():
    p = argparse.ArgumentParser(description="DRG 3.0 分组器 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import", help="导入规则 xlsx → SQLite")
    s.add_argument("--xlsx")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("group", help="单条病案分组")
    s.add_argument("--zyzd", required=True)
    s.add_argument("--zyss")
    s.add_argument("--qtzd", help="其他诊断，逗号分隔")
    s.add_argument("--qtss", help="其他手术，逗号分隔")
    s.add_argument("--nl", type=int)
    s.add_argument("--xb", type=int)
    s.add_argument("--xsrtl", type=int)
    s.add_argument("--xsrtz", type=int)
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_group)

    s = sub.add_parser("batch", help="批量分组 xlsx")
    s.add_argument("input")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_batch)

    s = sub.add_parser("benchmark", help="性能基准")
    s.add_argument("--n", type=int, default=1000)
    s.set_defaults(func=cmd_benchmark)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
