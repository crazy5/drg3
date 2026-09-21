# HIS → DRG 同步脚本

部署在 88.3 桥接机（192.168.88.3）。每天凌晨从内网 HIS 拉前一天数据，POST 给 88.2 上的 DRG 系统。

## 网络拓扑

```
HIS (内网, 192.168.101.100)  ──直连──▶  88.3 (桥接, 跑本脚本)
                                          │
                                          │  HTTP POST /api/sync/from-his
                                          ▼
                                     88.2 (DRG, 192.168.88.2:8000)
```

## 安全约束（必读）

三层防护，缺一不可：

| 层 | 谁来做 | 怎么做 |
|---|---|---|
| ① HIS 数据库账号 | HIS DBA / 厂商 | 专用账号 `his_sync_reader`，只授 `db_datareader` 角色。**绝不能用 `sa`** |
| ② 脚本 SQL 审计 | 本脚本内置 `assert_sql_readonly` | 禁止 `INSERT/UPDATE/DELETE/DROP/ALTER/...`，扫到即终止 |
| ③ 连接只读标志 | pyodbc 连接串 + `ApplicationIntent=ReadOnly` | SQL Server 端进一步保证 |

HIS SQL 是只读 `SELECT`，脚本只读取外层数据再转发到 DRG，**DRG 的写入只发生在 DRG 自己的 SQLite（drg.db）里**。

---

## 安装（88.3 上一次性）

### 1. 准备 Python 环境

88.3 已确认能装 Python 3 + pip（建议 3.10+）。

```bash
# 如未装 Python，去 https://www.python.org/downloads/ 下载安装
python --version    # 应 ≥ 3.10

# 创建虚拟环境（推荐，避免污染系统 Python）
cd C:\his_sync      # 你想放的目录
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 安装 SQL Server ODBC 驱动

pyodbc 需要 Microsoft ODBC Driver 17 for SQL Server：

```bash
# 64 位 Windows 安装命令（管理员 PowerShell）
# 先下载 msodbcsql17 的 msi（或 winget）：
winget install --id Microsoft.msodbcsql17 -e
```

验证：
```bash
python -c "import pyodbc; print(pyodbc.drivers())"
# 应包含 'ODBC Driver 17 for SQL Server'
```

### 3. 配置环境变量

把下列 5 个加到 88.3 的 `~/.bashrc`（Git Bash）或系统环境变量（cmd）：

```bash
export HIS_DB_HOST="192.168.101.100"
export HIS_DB_USER="his_sync_reader"     # ★ 用只读账号，不是 sa
export HIS_DB_PASSWORD="Strong@Pass!2026"
export HIS_DB_NAME="bagl_java"
export DRG_API_URL="http://192.168.88.2:8000"
```

cmd / PowerShell 写法见 README 末尾。

### 4. 手动测试一次

```bash
cd C:\his_sync
.\venv\Scripts\activate
python sync.py --date 2026-03-02 --dry-run     # 先 dry-run 看 HIS 返回的字段
python sync.py --date 2026-03-02               # 真实推送一次
```

如果 DRG 端 88.2 上能看到 `/api/sync/logs` 多了一行 `source=88.3(...)` 的记录，就通了。

### 5. 设置 Windows 任务计划（每天凌晨 02:00）

**方式 A：用任务计划程序 GUI**
1. 打开「任务计划程序」→ 「创建任务」
2. 常规：名字 `HIS-DRG-Sync`，「不管用户是否登录都要运行」
3. 触发器：新建，每天，02:00:00
4. 操作：新建，启动程序
   - 程序：`C:\his_sync\venv\Scripts\python.exe`
   - 参数：`C:\his_sync\sync.py`
   - 起始于：`C:\his_sync`
5. 设置：「如果任务失败，按以下频率重新启动」间隔 5 分钟，最多重试 3 次
6. 条件：网络可用时运行

**方式 B：用 schtasks 命令**

```cmd
schtasks /Create /TN "HIS-DRG-Sync" /TR "\"C:\his_sync\venv\Scripts\python.exe\" C:\his_sync\sync.py" /SC DAILY /ST 02:00 /F
```

### 6. 失败告警（建议加）

任务计划程序可在失败时发邮件 / 写事件日志。也可用 PowerShell wrapper 包装：

```powershell
# C:\his_sync\run_with_alert.ps1
$out = & C:\his_sync\venv\Scripts\python.exe C:\his_sync\sync.py 2>&1
if ($LASTEXITCODE -ne 0) {
    # 发邮件 / 企业微信 / 钉钉 webhook
    Send-MailMessage -To "you@hospital.com" -Subject "HIS同步失败" -Body $out
}
```

---

## 日常运维

### 查看日志

```bash
# 今天的日志
cat logs/sync-$(date +%Y%m%d).log

# 跑过的同步记录（在 DRG 端 88.2 看）
curl http://localhost:8000/api/sync/logs?limit=20
```

### 补跑某天数据

```bash
python sync.py --date 2026-03-15           # 单独补一天
# 或脚本里支持循环：
for d in 2026-03-15 2026-03-16 2026-03-17; do python sync.py --date $d; done
```

### DRG 上手动补推

DRG 端 `POST /api/sync/from-his` 也可手工 POST 一段 JSON 触发，写在 `tests/` 下。

---

## 故障排查

| 现象 | 排查 |
|---|---|
| `pyodbc.InterfaceError: ('IM002', ...)` | ODBC 驱动没装，或驱动名拼错（注意 17 vs 18） |
| `Login failed for user '...'` | 账号密码错，或 HIS 没建账号 |
| `SELECT permission denied on object '...'` | 账号没授 `db_datareader` |
| DRG 端 500 | 看 `/api/sync/logs/{log_id}` 的 `error_msg` |
| 任务跑成功但 DRG 没记录 | 检查 `DRG_API_URL` 是否拼写正确；88.2 的 8000 端口防火墙是否对 88.3 开放 |

---

## 环境变量 — cmd.exe / PowerShell 版本

```cmd
:: cmd.exe（永久）
setx HIS_DB_HOST "192.168.101.100" /M
setx HIS_DB_USER "his_sync_reader" /M
setx HIS_DB_PASSWORD "Strong@Pass!2026" /M
setx HIS_DB_NAME "bagl_java" /M
setx DRG_API_URL "http://192.168.88.2:8000" /M
```

```powershell
[Environment]::SetEnvironmentVariable("HIS_DB_HOST","192.168.101.100","User")
[Environment]::SetEnvironmentVariable("HIS_DB_USER","his_sync_reader","User")
[Environment]::SetEnvironmentVariable("HIS_DB_PASSWORD","Strong@Pass!2026","User")
[Environment]::SetEnvironmentVariable("HIS_DB_NAME","bagl_java","User")
[Environment]::SetEnvironmentVariable("DRG_API_URL","http://192.168.88.2:8000","User")
```