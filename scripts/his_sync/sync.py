"""HIS → DRG 同步脚本。

部署在 88.3 桥接机上，每天凌晨 02:00 由 Windows 任务计划程序触发。
工作流程：
  1. 读环境变量（连 HIS + DRG 接收地址）
  2. SQL 关键字黑名单审计（防御）
  3. pyodbc 连 HIS（read-only intent）
  4. 跑只读 SQL 拉 [昨天 00:00, 今天 00:00) 的病案
  5. 字段映射 + 转 JSON
  6. httpx POST 给 DRG /api/sync/from-his
  7. 写本地同步日志 + 失败时 exit 1（让任务计划告警）

用法：
  python sync.py                              # 默认：拉昨日
  python sync.py --date 2026-03-15            # 补跑指定日期
  python sync.py --date 2026-03-15 --dry-run  # 只打 SQL 结果不 POST
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import sys
import time
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

# ────────── 日志 ──────────

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"sync-{datetime.now():%Y%m%d}.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("his_sync")

# ────────── ICD 编码映射（国临版 → 医保版） ──────────
# HIS 送来的是国临版 2.0 编码（如 I84.201），DRG 3.0 规则集合（set_members）
# 用医保版 2.0 编码（如 K64.811）。二者需要通过对照表转换。
# 源表：1_ICD10国临版2.0对照医保版2.0_0125(1)(1)(1).xlsx
# 生成脚本：scripts/build_icd_mapping.py
#
# 路径查找顺序（按顺序尝试，命中即用）：
#   1. $ICD_MAPPING_PATH 环境变量（部署机自定义覆盖）
#   2. 项目标准布局：<repo>/backend/data/icd_mapping.json（parents[2]/backend/data）
#   3. 脚本同目录（88.3 平铺部署：sync.py + 两份 json 放同一目录）
def _resolve_mapping(filename: str) -> Path:
    """映射表路径解析（按顺序尝试）：
    1. $ICD_MAPPING_PATH 环境变量（自定义覆盖）
    2. 项目标准布局：<repo>/backend/data/<file>（要求 parents[2] 深度足够）
    3. 脚本同目录（88.3 平铺部署）

    ⚠️ parents[2] 在深度不足（如脚本直接放在 C:\\ 下）会直接 IndexError，
    所以标准布局的求值要套在深度检查里。
    """
    env = os.environ.get("ICD_MAPPING_PATH")
    if env:
        return Path(env)
    p = Path(__file__).resolve()
    if len(p.parents) >= 3:
        std = p.parents[2] / "backend" / "data" / filename
        if std.exists():
            return std
    flat = p.parent / filename
    return flat


MAPPING_PATH = _resolve_mapping("icd_mapping.json")

# 手术（ICD9）映射：HIS 送来国临版 3.0 手术码，DRG 用医保版 2.0
# 源表：ICD9国临版3.0对照医保版2.0_0125.xlsx
# 生成脚本：scripts/build_icd_op_mapping.py
OP_MAPPING_PATH = _resolve_mapping("icd_op_mapping.json")

_mapping_cache: dict[str, str] | None = None
_op_mapping_cache: dict[str, str] | None = None


def load_icd_mapping() -> dict[str, str]:
    """启动时一次性加载。失败打 WARN 返回空 dict（不让同步整体挂掉）。"""
    global _mapping_cache
    if _mapping_cache is not None:
        return _mapping_cache
    if not MAPPING_PATH.exists():
        log.warning(
            "ICD 映射表不存在：%s（HIS 编码将按原值推送，DRG 分组可能命中失败）",
            MAPPING_PATH,
        )
        _mapping_cache = {}
        return _mapping_cache
    try:
        with open(MAPPING_PATH, encoding="utf-8") as f:
            data = json.load(f)
        log.info("加载 ICD 诊断映射表 %d 条：%s", len(data), MAPPING_PATH.name)
        _mapping_cache = data
        return _mapping_cache
    except Exception as e:
        log.warning("加载 ICD 诊断映射表失败：%s（HIS 编码将按原值推送）", e)
        _mapping_cache = {}
        return _mapping_cache


def load_icd_op_mapping() -> dict[str, str]:
    """ICD9 手术映射：国临版 3.0 → 医保版 2.0。"""
    global _op_mapping_cache
    if _op_mapping_cache is not None:
        return _op_mapping_cache
    if not OP_MAPPING_PATH.exists():
        log.warning(
            "ICD9 手术映射表不存在：%s（手术码将按原值推送，DRG 分组可能命中失败）",
            OP_MAPPING_PATH,
        )
        _op_mapping_cache = {}
        return _op_mapping_cache
    try:
        with open(OP_MAPPING_PATH, encoding="utf-8") as f:
            data = json.load(f)
        log.info("加载 ICD9 手术映射表 %d 条：%s", len(data), OP_MAPPING_PATH.name)
        _op_mapping_cache = data
        return _op_mapping_cache
    except Exception as e:
        log.warning("加载 ICD9 手术映射表失败：%s（手术码将按原值推送）", e)
        _op_mapping_cache = {}
        return _op_mapping_cache


def apply_mapping(code: str | None) -> str | None:
    """单个 ICD10 诊断编码过映射。None/空 → 原值；查不到 → 返回原码（不丢信息）。

    key 归一：大写 + 去末尾点（与 build_icd_mapping._norm 一致）。
    """
    if not code:
        return code
    m = load_icd_mapping()
    if not m:
        return code
    key = str(code).strip().upper().rstrip(".")
    return m.get(key, code)


def apply_mapping_list(codes: list[str]) -> list[str]:
    """批量版本。同 apply_mapping，逐个查。"""
    if not codes:
        return codes
    return [apply_mapping(c) for c in codes]


def apply_op_mapping(code: str | None) -> str | None:
    """单个 ICD9 手术编码过映射（国临 3.0 → 医保 2.0）。"""
    if not code:
        return code
    m = load_icd_op_mapping()
    if not m:
        return code
    key = str(code).strip().upper().rstrip(".")
    return m.get(key, code)


def apply_op_mapping_list(codes: list[str]) -> list[str]:
    """批量手术编码映射。"""
    if not codes:
        return codes
    return [apply_op_mapping(c) for c in codes]


# ────────── HIS SQL（同步脚本唯一接触的 SQL，必须只读） ──────────

HIS_QUERY = r"""
WITH visits AS (
    SELECT *
    FROM TPATIENTVISIT
    WHERE FWORKRQ >= ? AND FWORKRQ < ?
),
ops AS (
    SELECT
        a.FPRN, a.FTIMES,
        MAX(CASE WHEN b.FPX = '1' THEN b.FOPCODE END)                       AS main_op_code,
        STUFF((
            SELECT ',' + b2.FOPCODE
            FROM TOPERATION b2
            WHERE b2.FPRN = a.FPRN AND b2.FTIMES = a.FTIMES AND b2.FPX <> '1'
            ORDER BY CAST(b2.FPX AS INT)
            FOR XML PATH(''), TYPE
        ).value('.', 'NVARCHAR(MAX)'), 1, 1, '')                             AS other_op_codes
    FROM visits a
    INNER JOIN TOPERATION b
        ON a.FPRN = b.FPRN AND a.FTIMES = b.FTIMES
    GROUP BY a.FPRN, a.FTIMES
),
diags AS (
    SELECT
        a.FPRN, a.FTIMES,
        MAX(CASE WHEN b.FZDLX = '1' THEN b.FICDM END)                        AS main_diag_code,
        STUFF((
            SELECT ',' + b2.FICDM
            FROM TDIAGNOSE b2
            WHERE b2.FPRN = a.FPRN AND b2.FTIMES = a.FTIMES AND b2.FZDLX <> '1'
            ORDER BY b2.FZDLX
            FOR XML PATH(''), TYPE
        ).value('.', 'NVARCHAR(MAX)'), 1, 1, '')                             AS other_diag_codes
    FROM visits a
    INNER JOIN TDIAGNOSE b
        ON a.FPRN = b.FPRN AND a.FTIMES = b.FTIMES
    GROUP BY a.FPRN, a.FTIMES
)
SELECT
    ISNULL(CAST(a.FPRN AS VARCHAR(20)), 'NULL') + '-' + ISNULL(CONVERT(VARCHAR(8), a.FCYDATE, 112), 'NA') AS case_id,
    a.FAGE                                                                   AS age_raw,
    a.FRYTZ                                                                  AS xsrtz_raw,
    a.FSEX                                                                   AS xb,
    a.FRYDATE                                                                AS admission_date,
    a.FCYDATE                                                                AS discharge_date,
    a.FCYDEPT                                                                AS discharge_department,
    a.FWORKRQ                                                                AS update_time,
    d.main_diag_code                                                         AS zyzd,
    d.other_diag_codes                                                       AS qtzd_list,
    o.main_op_code                                                           AS zyss,
    o.other_op_codes                                                         AS qtss_list
FROM visits a
INNER JOIN diags d ON a.FPRN = d.FPRN AND a.FTIMES = d.FTIMES
LEFT  JOIN ops   o ON a.FPRN = o.FPRN AND a.FTIMES = o.FTIMES
""".strip()


# ────────── SQL 关键字审计（第二道防线） ──────────

# 第一层：账号 db_datareader（数据库端）
# 第二层：以下黑名单（脚本端，防止 SQL 字符串被改坏）
FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "CREATE", "EXEC", "EXECUTE", "GRANT", "REVOKE", "MERGE",
    "BULK INSERT", "OPENROWSET", "OPENDATASOURCE", "OPENQUERY",
    "XP_", "SP_", "RESTORE", "BACKUP", "DBCC", "SHUTDOWN",
    "KILL", "RECONFIGURE", "DENY",
)


def assert_sql_readonly(sql: str) -> None:
    """确保 SQL 是只读的。

    步骤：
    1. 去掉 -- 行注释
    2. 去掉 /* ... */ 块注释（嵌套不处理，SQL Server 不允许嵌套）
    3. 去掉字符串字面量 '...'（避免字符串里塞关键字绕过）
    4. 用词边界扫黑名单
    """
    stripped = re.sub(r"--[^\n]*", "", sql)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    stripped = re.sub(r"'(?:''|[^'])*'", "''", stripped)

    upper = stripped.upper()
    for kw in FORBIDDEN_KEYWORDS:
        # 词边界匹配，避免误杀（例如 INSERTED 是列名，但 UPDATE/DELETE 等不允许）
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, upper):
            raise RuntimeError(
                f"SQL 安全审计失败：发现禁止关键字 '{kw}'。"
                "此同步脚本禁止任何 DML/DDL/DCL 操作，只允许 SELECT。"
            )

    if not re.search(r"\bSELECT\b", upper):
        raise RuntimeError("SQL 必须以 SELECT 开头")


# ────────── HIS 行 → DRG payload ──────────


def normalize_date(v):
    """HIS 返回的 datetime/date → 'YYYY-MM-DD' 字符串；None 透传。

    用于本地日志/展示。POST 给 DRG 时直接传 ISO 字符串（见 to_iso）。
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    return str(v)


def normalize_dt(v):
    """HIS 返回的 datetime → 'YYYY-MM-DD HH:MM:SS' 字符串；None 透传。

    用于本地日志/展示。POST 给 DRG 时直接传 ISO 字符串（见 to_iso）。
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def to_iso(v):
    """datetime/date → ISO 字符串；其余原样返回。

    httpx 的 json= 用 stdlib json.dumps，不认 datetime 对象；这里统一转 ISO，
    DRG 端 Pydantic 会自动解析回 datetime/date。
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def split_codes(v):
    """'A01.001,B02.002,B99' → ['A01.001','B02.002','B99']，统一大写去末尾点。"""
    if not v:
        return []
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", errors="ignore")
    s = str(v).strip()
    if not s:
        return []
    out = []
    for x in s.replace(";", ",").split(","):
        x = x.strip().upper().rstrip(".")
        if x:
            out.append(x)
    return out


def parse_age(v) -> tuple[int | None, int | None]:
    """HIS FAGE 解析 → (nl 年龄, xsrtl 新生儿日龄)。

    单位由首字母决定（HIS 老编码约定）：
    - 'Y' / 'y' 开头 → 整段数字是「岁」（年龄字段）
    - 'D' / 'd' 开头 → 整段数字是「天」（新生儿日龄；年龄 = 0）
    - 'H' / 'h' 开头 → 整段数字是「小时」（新生儿小时龄；年龄=0，日龄≈0）
    - 'M' / 'm' 开头 → 整段数字是「月」（婴儿月龄；按 30 天折算日龄，年龄=0）
    - 无前缀 → 直接是数字（按岁处理），兼容 '74岁' / ' 74 '

    返回：(nl, xsrtl)；无法解析 → (None, None)
    """
    if v is None:
        return None, None
    if isinstance(v, (int, float)):
        return int(v), 0
    s = str(v).strip()
    if not s:
        return None, None

    # 提取首字母（去空格后）
    head = s[0].upper()
    rest = s[1:].strip()
    # rest 里只取数字（容忍 '74岁'、'16 天' 等中文单位）
    digits = re.sub(r"[^\d]", "", rest)

    def to_int(s: str) -> int | None:
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None

    if head == "Y":
        return to_int(digits), 0
    if head == "D":
        return 0, to_int(digits)
    if head == "H":
        # 小时龄算 0 天（HIS 没有单独的「小时龄」字段给 DRG，按 0 处理）
        return 0, 0
    if head == "M":
        d = to_int(digits)
        return 0, (d * 30) if d is not None else None

    # 无前缀：纯数字/中文 '岁'，按岁处理
    return to_int(re.sub(r"[^\d]", "", s)), 0


def normalize_age(v) -> int | None:
    """兼容旧接口：只返回 nl（年龄）。新代码请用 parse_age 拿 (nl, xsrtl)。"""
    nl, _ = parse_age(v)
    return nl


def normalize_sex(v):
    """HIS FSEX 是 VARCHAR：'男'/'女'/'M'/'F'/'1'/'2' → 1/2/9。

    1=男 2=女 9=未知（与 DRG DTO 约定一致）。
    """
    if v is None:
        return None
    if isinstance(v, int):
        return v if v in (1, 2, 9) else None
    s = str(v).strip()
    if not s:
        return None
    # 中文
    if s in ("男", "男性", "M", "m", "Male", "male"):
        return 1
    if s in ("女", "女性", "F", "f", "Female", "female"):
        return 2
    if s in ("未知", "未说明", "Unknown", "unknown", "U", "u"):
        return 9
    # 数字代码
    if s in ("1", "2", "9"):
        return int(s)
    return None


def his_row_to_case(row: dict) -> dict:
    """HIS 一行 → DRG /api/sync/from-his 期望的 JSON。

    - 日期/时间 → ISO 字符串（httpx 的 json= 走 stdlib json.dumps，
      不认 datetime/date 对象；Pydantic 收到 ISO 字符串能自动解析）
    - FAGE 解析：'Y74'→74岁、'D16'→0岁+16天、'H8'→0岁+0天、'M6'→0岁+180天
    - xb HIS 是 VARCHAR（'男'/'女'），需 normalize 成 int
    - FRYTZ 新生儿入院体重（克），XSRTZ < 2500 触发早产分组
    - ICD 编码过国临→医保映射表（apply_mapping），确保 DRG 规则集合能命中
    - **手术编码走单独的 ICD9 国临→医保映射**（apply_op_mapping），不能用诊断表
    """
    nl, xsrtl = parse_age(row.get("age_raw"))
    return {
        "case_id": (row.get("case_id") or "").strip(),
        "zyzd": apply_mapping((row.get("zyzd") or "").strip().upper().rstrip(".")) or "",
        "zyss": apply_op_mapping((row.get("zyss") or "").strip().upper().rstrip(".")) or None,
        "qtzd_list": apply_mapping_list(split_codes(row.get("qtzd_list"))),
        "qtss_list": apply_op_mapping_list(split_codes(row.get("qtss_list"))),
        "nl": nl,
        "xb": normalize_sex(row.get("xb")),
        "xsrtl": xsrtl,
        "xsrtz": to_int(row.get("xsrtz_raw")),
        "admission_date": to_iso(row.get("admission_date")),
        "discharge_date": to_iso(row.get("discharge_date")),
        "discharge_department": (str(row.get("discharge_department") or "")).strip() or None,
        "update_time": to_iso(row.get("update_time")),
    }


def to_int(v):
    """HIS VARCHAR 数字字段（FRYTZ 等）→ int；空/None/非数字 → None。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


# ────────── 主流程 ──────────


def fetch_from_his(start_dt: datetime, end_dt: datetime):
    """连 HIS 跑查询，返回 list[dict]。"""
    import pyodbc

    host = os.environ.get("HIS_DB_HOST")
    user = os.environ.get("HIS_DB_USER")
    pwd = os.environ.get("HIS_DB_PASSWORD")
    db = os.environ.get("HIS_DB_NAME")
    if not all([host, user, pwd, db]):
        raise RuntimeError(
            "环境变量未配置完整：需要 HIS_DB_HOST / HIS_DB_USER / HIS_DB_PASSWORD / HIS_DB_NAME"
        )

    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={host},1433;"                    # ★ 逗号+端口 = 强制 TCP，绕过 Named Pipes
        f"DATABASE={db};"
        f"UID={user};"
        f"PWD={pwd};"
        f"ApplicationIntent=ReadOnly;"            # 只读意图（连接级防护）
    )
    log.info("连接 HIS: %s/%s (ReadOnly)", host, db)
    conn = pyodbc.connect(conn_str, timeout=30, autocommit=True)
    try:
        with conn.cursor() as cur:
            # ★ 进一步防长锁
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
            # ★ pyodbc autocommit=True 默认 ARITHABORT=OFF，含聚合 CASE 的查询会拒绝
            cur.execute("SET ARITHABORT ON")
            log.info("执行 HIS 查询（窗口 [%s, %s)）", start_dt, end_dt)
            cur.execute(HIS_QUERY, start_dt, end_dt)
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            log.info("HIS 返回 %d 条", len(rows))
            return rows
    finally:
        conn.close()


def post_to_drg(payload: dict, retries: int = 3) -> dict:
    """POST 给 DRG /api/sync/from-his，带重试。

    4xx 是客户端问题，重试无意义；5xx / 网络错误才重试。
    """
    import httpx

    base = os.environ["DRG_API_URL"].rstrip("/")
    url = f"{base}/api/sync/from-his"
    timeout = httpx.Timeout(60.0, connect=10.0)

    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            log.info("POST %s (attempt %d/%d, %d cases)",
                     url, attempt, retries, len(payload.get("cases", [])))
            r = httpx.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            # 4xx 直接返回 body，不再重试（payload 错再怎么试也是错）
            body = e.response.text[:1000]
            raise RuntimeError(
                f"POST {url} 返回 {e.response.status_code}：{body}"
            ) from e
        except Exception as e:        # noqa: BLE001
            last_exc = e
            log.warning("POST 失败 (attempt %d): %s", attempt, e)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"POST {url} 重试 {retries} 次仍失败: {last_exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="补跑指定日期（YYYY-MM-DD），默认昨天")
    parser.add_argument("--days-back", type=int, default=1,
                        help="同步窗口向前回溯天数（默认 1=仅昨天；与 --date 共用时取 1）")
    parser.add_argument("--dry-run", action="store_true", help="只查不推")
    parser.add_argument("--batch-size", type=int, default=200,
                        help="每批推多少条病案给 DRG，默认 200")
    args = parser.parse_args()

    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date else date.today() - timedelta(days=1)
    )
    days_back = args.days_back
    if days_back > 1 and args.date:
        # 显式指定 --date 时，--days-back 不生效（避免覆盖用户意图）
        days_back = 1
    start_dt = datetime.combine(target_date - timedelta(days=days_back - 1), dt_time(0, 0, 0))
    end_dt = datetime.combine(target_date + timedelta(days=1), dt_time(0, 0, 0))
    log.info("同步窗口：%s → %s（days_back=%d）", start_dt, end_dt, days_back)

    # 1. SQL 安全审计
    assert_sql_readonly(HIS_QUERY)
    log.info("SQL 安全审计通过（只读）")

    # 2. 拉 HIS
    rows = fetch_from_his(start_dt, end_dt)
    if not rows:
        log.info("HIS 无新增病案，退出")
        return 0

    # 3. 字段映射
    cases = []
    skipped = 0
    for r in rows:
        c = his_row_to_case(r)
        if not c["case_id"]:
            log.warning("跳过无 case_id 行: %r", r)
            skipped += 1
            continue
        cases.append(c)
    log.info("映射后 %d 条病案（跳过 %d 条无 case_id）", len(cases), skipped)

    if args.dry_run:
        log.info("[DRY-RUN] 不实际推送；首条样例：%s", json.dumps(cases[0], ensure_ascii=False, indent=2))
        return 0

    # 4. 分批 POST
    source = f"{socket.gethostname()}({os.environ.get('HIS_DB_HOST','')})"
    overall = {"received": 0, "inserted": 0, "updated": 0,
               "succeeded": 0, "fallback": 0, "failed": 0,
               "log_id": None, "batches": 0}

    for i in range(0, len(cases), args.batch_size):
        batch = cases[i:i + args.batch_size]
        payload = {
            "source": source,
            "start_date": target_date.isoformat(),
            "end_date": end_dt.date().isoformat(),
            "cases": batch,
        }
        resp = post_to_drg(payload)
        overall["received"] += resp.get("received", 0)
        overall["inserted"] += resp.get("inserted", 0)
        overall["updated"] += resp.get("updated", 0)
        overall["succeeded"] += resp.get("succeeded", 0)
        overall["fallback"] += resp.get("fallback", 0)
        overall["failed"] += resp.get("failed", 0)
        overall["log_id"] = resp.get("log_id")
        log.info(
            "批次 %d/%d 完成：log_id=%s 状态=%s 收=%d 插=%d 更=%d 成=%d 兜=%d 败=%d",
            (i // args.batch_size) + 1,
            (len(cases) + args.batch_size - 1) // args.batch_size,
            resp.get("log_id"), resp.get("status"),
            resp.get("received"), resp.get("inserted"),
            resp.get("updated"), resp.get("succeeded"),
            resp.get("fallback"), resp.get("failed"),
        )

    log.info(
        "本次同步完成：log_id=%s 收=%d 插=%d 更=%d 成功=%d 兜底=%d 失败=%d",
        overall["log_id"], overall["received"], overall["inserted"],
        overall["updated"], overall["succeeded"], overall["fallback"],
        overall["failed"],
    )

    # 5. 失败时 exit 1，让任务计划发邮件告警
    return 0 if overall["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())