# DRG 3.0 分组器

> 基于国家医保局《按病组（DRG）付费 3.0 版分组方案配置信息》的分组器。
> 导入病案 → 输出 DRG 分组 + 完整证据链（可申诉场景可解释）。

## 特性

- 🚀 **0.05ms/条** 单病案分组（启动期构建内存索引 + frozenset O(1) 判定）
- 🌲 **完整证据链** 每条命中规则的子表达式、`in` 关联的具体编码、CC 排除判定全程留痕
- 🧬 **规则即数据** 6 张业务表 + 文本表达式，逻辑/数据/引擎三层分离
- 📊 **三层漏斗** MDC → ADRG → DRG，优先级显式化（排序字段）
- 🛡️ **兜底可控** 任一层失败 → `0000` + `fallback` 标记，Dashboard 兜底率监控
- 📥 **批量导入** xlsx / csv，前端实时进度 + 结果下载

## 架构

```
┌─────────────────────────────────────────────────────────┐
│  Frontend (Vue3 + Element Plus + Pinia)                   │
│  SingleCase │ BatchImport │ Dashboard │ RuleBrowser       │
└────────────────────────┬────────────────────────────────┘
                         │ REST / SSE
┌────────────────────────▼────────────────────────────────┐
│  FastAPI                                                   │
│  /api/rules/import  /api/cases  /api/grouping/batch      │
└────────────────────────┬────────────────────────────────┘
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
  ┌─────────┐      ┌──────────┐      ┌──────────┐
  │ SQLite  │      │  Rule    │      │   DSL    │
  │ drg.db  │◄─────┤  Index   │◄─────┤  Parser  │
  │ 6 表+   │      │ 内存     │      │ 递归下降 │
  │ cases+  │      │ frozenset│      │          │
  │ results │      └─────┬────┘      └──────────┘
  └─────────┘            │
                         ▼
                  ┌──────────────┐
                  │  Grouper     │
                  │ 三层漏斗+    │
                  │ CC 分级判定  │
                  └──────────────┘
```

## 启动

### 后端

```bash
cd backend
pip install -r requirements.txt
python scripts/run_cli.py import --xlsx ../data/drg_rules_3.0.xlsx   # 首次导入规则
python scripts/run_cli.py import --xlsx ../data/drg_rules_3.0.xlsx   # 也可在前端上传
uvicorn app.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev   # 默认 http://localhost:5173
```

前端通过 `vite.config.ts` 的 proxy 将 `/api` 转发到 `localhost:8000`。

## 核心模块

| 模块 | 职责 |
|---|---|
| `app/engine/parser/` | DSL 词法 + 语法分析，手写 lexer + 递归下降 parser |
| `app/engine/runtime/evaluator.py` | AST 解释执行，证据链挂载 |
| `app/engine/index.py` | 启动期内存索引构建（frozenset） |
| `app/engine/grouper/pipeline.py` | 三层漏斗编排 |
| `app/engine/grouper/cc.py` | CC / MCC 分级判定（含排除表） |
| `app/importers/xlsx_importer.py` | xlsx 规则 → SQLite |
| `app/api/` | FastAPI 路由层（薄） |

## DSL 速览

```
ZYZD in DI_B00
ZYSS in OP_AA1
QTZD in MCC                     # 命中即并发症（高严重度）
QTZD in CC                      # 命中即并发症
{ZYSS, QTSS} in OP2_AC1        # 任一变量命中集合
ZYSS not in OP_ALL
NL >= 70
XB = 1
1                               # 恒真
((NL=0) and (XSRTL>=29)) or ((NL=0) and (XSRTL<29))    # 新生儿跨字段 OR
```

详细 DSL 见 `docs/dsl.md`（待写）。

## 目录结构

```
drg3/
├── backend/                FastAPI 服务
│   ├── app/
│   │   ├── api/            路由层
│   │   ├── core/           配置
│   │   ├── db/             SQLAlchemy 模型 + session
│   │   ├── engine/         ★ 分组引擎核心
│   │   │   ├── parser/     lexer / ast / parser
│   │   │   ├── runtime/    evaluator / evidence
│   │   │   ├── grouper/    pipeline / cc
│   │   │   └── index.py    内存索引
│   │   ├── importers/      xlsx → DB
│   │   ├── schemas/        Pydantic DTO
│   │   └── tasks/          批量分组
│   ├── tests/              49 单测
│   └── scripts/run_cli.py  命令行工具
├── frontend/               Vue3 + Element Plus
│   ├── src/views/          SingleCase / BatchImport / Dashboard / RuleBrowser / ResultDetail
│   ├── src/components/     EvidenceTree / RuleSyntax / SetInspector
│   └── src/api/            axios 封装
├── data/
│   └── drg_rules_3.0.xlsx  国家医保局规则源文件
└── docs/
    ├── README.md           本文件
    └── template.md         批量导入模板说明
```

## 测试

```bash
cd backend && pytest -v
```

- `test_parser.py` 22 用例覆盖所有 DSL 形态
- `test_evaluator.py` 12 用例验证解释器 + 证据
- `test_cc.py` 7 用例验证 CC 分级（含跨系统排除）
- `test_pipeline.py` 6 用例含真实 xlsx 集成
- `test_importer.py` 2 用例验证导入幂等性

**49 用例全部通过**。

## 性能

| 场景 | 实测 |
|---|---|
| 单条病案分组 | ~0.05ms（目标 < 5ms，100 倍超额） |
| 1000 条批量 | ~49ms |
| 启动期索引构建 | ~3s（含 10 万 + 集合成员 + 873 DRG 规则解析） |

## 设计哲学

参考 [《从 3.0 规则文件到 DRG 分组器》](../从3.0规则文件到DRG分组器，一些启发和思考.md)：

1. **逻辑 / 数据 / 引擎三层分离** 6 张业务表只承载数据，规则字符串只承载逻辑，Python 代码只承载解释执行
2. **规则即数据** 把判定逻辑写成文本表达式，非程序员也能读、能审、能改
3. **优先级显式化** 「排序」字段把控制流搬进数据
4. **证据链是 first-class 输出** 每条规则的命中情况、每个 `in` 关联的具体诊断/手术、所属集合都要可被复述

## 已知限制

- DRG 规则中偶尔出现 `length(...)>=N` 这类函数调用表达式（IC2 等极少数场景），当前 parser 不支持 → 该规则 `parsed_ast` 存为 `{"error": ...}`，对应病案走兜底 `0000`。生产环境出现频率 < 0.1%。
- 多值分隔符支持 `,` `;`，不支持制表符。
- 批量任务执行在主进程 `BackgroundTasks` 内，并发量 > 100 QPS 时建议切到独立 worker。

## License

内部使用。