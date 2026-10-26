# SURVEY_WHITELIST 插件完整交接文档

> 版本：2.0（每群独立 DB + 每月自动同步）
> 适用项目：`/home/admin/nbbot`
> 生成时间：2026-10-26
> 文档性质：最终版交接文档，供后续开发、运维和 Web 端对接使用

---

## 目录

0. 当前状态与文档说明
1. 项目背景
2. 总体逻辑链
3. 架构与分层
4. 关键设计决策与处理方式
5. 代码骨架
6. 依赖与运行环境
7. 部署与使用说明
8. 上线验证清单
9. 风险与应对
10. 附录 A：Web 后端对接约定
11. 附录 B：速查

---

## 0. 当前状态与文档说明

### 0.1 文档用途

这份文档描述 `survey_whitelist` 插件从 1.x 升级到 2.0 后的最终形态，包含：

- 业务背景与整体逻辑链
- 架构分层和关键设计决策
- 需要特别注意的踩坑与处理方式
- 代码骨架、配置项、数据库结构
- 部署、迁移、日常使用与验证步骤
- 已知风险与 Web 后端对接约定

**机器人端代码和 `run.sh` 在本项目内；Web 问卷后端是另一个项目，本文只描述接口约定。**

### 0.2 当前进度

已完成：

- 代码从 1.x（单库 + `group_id` 列）升级到 2.0（每群独立 `.db` 文件）
- 新增每月 1 日 0 点自动同步所有群的定时任务
- 命令权限扩展为 `SUPERUSER | GROUP_ADMIN | GROUP_OWNER`
- 依赖清单移入插件目录 `requirements.txt`
- `run.sh` 支持递归扫描插件目录下所有 requirements 并合并安装
- 单库到多文件的迁移脚本已准备好
- 服务器上已实际启动过一次，并在那一次启动中暴露并修复了
  **scheduler 导入 bug**（详见 4.2）

待验证（上线前必须完成）：

- [ ] 修复后的版本重启，四条启动日志是否全部出现
- [ ] 定时任务能否真正触发（临时把 cron 改成 `* * * * *` 测一次）
- [ ] 跨群隔离是否在 DB 层生效（手动插测试数据验证）
- [ ] 权限矩阵：超管 / 群主 / 群管理 / 普通群员
- [ ] `run.sh` 的可执行权限与换行符

> 注意：**修复后的代码尚未完成重启后的日志验证**。第 8 章的验证清单必须逐项执行。

### 0.3 服务器实际数据状态

- 两个群的数据目前仍在旧单库中，尚未迁移到新版多文件结构：
  - 群 `1101292217`：4 人
  - 群 `752775661`：303 人
- 同时属于两个群的重叠成员（4 人）：
  `3365220669`、`3384188179`、`3808303293`、`3844243138`
- 超管 QQ：`3384188179`
- 旧单库路径：
  `/home/admin/nbbot/src/plugins/survey_whitelist/DB/survey.db`

迁移前不要先启动新版插件（原因见 4.5）。

### 0.4 路径约定

| 项目 | 路径 |
| --- | --- |
| 项目根目录 | `/home/admin/nbbot` |
| 机器人入口 | `/home/admin/nbbot/bot.py` |
| venv | `/home/admin/nb_venv` |
| 启动脚本 | `/home/admin/nbbot/run.sh` |
| 插件目录 | `/home/admin/nbbot/src/plugins/survey_whitelist/` |
| 数据目录（每群一个 `.db`） | `/home/admin/nbbot/src/plugins/survey_whitelist/DB/` |

插件目录内容：

```
/home/admin/nbbot/src/plugins/survey_whitelist/
├── __init__.py        # 装配层
├── config.py          # 配置层
├── db.py              # 数据层
├── handlers.py        # 业务层
├── scheduler.py       # 定时层
└── requirements.txt   # 插件依赖清单
```

数据目录示例：

```
/home/admin/nbbot/src/plugins/survey_whitelist/DB/
├── 1101292217.db
├── 752775661.db
└── ...（每个群一个文件）
```

> ⚠️ DB 位于插件目录内。**重新部署插件目录前必须备份 DB**，否则会被一起覆盖。

---

## 1. 项目背景

### 1.1 业务场景

运营一个 QQ 群（当前为两个群），每月向群员发放一份匿名满意度调查表（Web 页面）。调查表有两个硬约束：

1. **只有本群成员可以填写**（白名单校验）
2. **每个群员只能填写一次**（防重复提交）

### 1.2 两端分工

| 端 | 负责 | 读/写 |
| --- | --- | --- |
| 机器人端（本插件） | 拉取群成员写入白名单；查询未提交的人用于催填 | 写 `group_members`，读 `survey_responses` |
| Web 端（独立项目） | 提供问卷页面；校验白名单；接收答卷 | 读 `group_members`，写 `survey_responses` |

### 1.3 通信方式

机器人端和 Web 端**共享同一个 SQLite 文件目录**（每个群一个文件），天然同步，不需要跨进程通信、消息队列或 HTTP API。

代价是两端必须：

- 使用完全相同的 DB 目录
- 对目录都有读写权限
- 保持表结构一致

### 1.4 数据流

```
管理员在群里发 /同步名单
        │
        ▼
handlers.handle_sync
        │  bot.get_group_member_list()
        ▼
run_in_executor(upsert_group_members)
        │  INSERT ... ON CONFLICT(qq_number) DO NOTHING
        ▼
DB/<group_id>.db  ── group_members
        │
        ▼
Web 问卷：SELECT 1 FROM group_members WHERE qq_number = ?
        │
        ▼
Web 提交：INSERT INTO survey_responses ... ON CONFLICT(qq_number) DO NOTHING
        ▼
管理员发 /未填名单
        │  LEFT JOIN survey_responses
        ▼
返回本群尚未提交的 QQ 号
```

---

## 2. 总体逻辑链

从业务需求到代码落点，链路如下：

| # | 业务需求 | 设计选择 | 代码落点 |
| --- | --- | --- | --- |
| 1 | 只有本群成员能填 | 机器人拉取群成员写入白名单，Web 查表校验 | `handlers.handle_sync` → `db.upsert_group_members` |
| 2 | 一人只能填一次 | `UNIQUE(qq_number)` + `ON CONFLICT DO NOTHING`，用 `rowcount` 判断重复 | Web 端 SQL；表结构见 5.3 |
| 3 | 管理员催填 | 查询白名单中未出现在答卷表的人 | `handlers.handle_unfilled` → `db.query_unfilled` |
| 4 | 多个群互不影响 | 每个群一个独立 `.db` 文件，物理隔离 | `db._db_file` / `ensure_db` |
| 5 | 每月自动拉一次名单 | APScheduler cron 任务遍历所有群 | `scheduler.sync_all_groups_job` |
| 6 | 一键启动、自动装依赖 | `run.sh` 扫描插件 requirements 并合并安装 | `run.sh` |
| 7 | 定时任务坏了不能影响手动命令 | 定时注册加别名 + 双层异常兜底 | `__init__.py` 末尾 |

### 2.1 一次手动同步的完整链路

1. 群管理员发送 `/同步名单`
2. `on_command` 匹配，权限检查 `SUPERUSER | GROUP_ADMIN | GROUP_OWNER`
3. `handle_sync` 把 `event.group_id` 转成 `str`
4. 调用 `bot.get_group_member_list(group_id=int(group_id))`
5. 成员列表交给 `run_in_executor(upsert_group_members, group_id, members)`（sqlite 是同步阻塞 API，不能直接跑在事件循环里）
6. `upsert_group_members` 先 `ensure_db(group_id)` 幂等建库建表，再逐条
   `INSERT ... ON CONFLICT(qq_number) DO NOTHING`，累加 `rowcount`
7. `matcher.finish` 返回：`同步完成：群内共 X 人，本次新增白名单 Y 人（已存在的自动跳过）。`

### 2.2 一次 Web 提交的完整链路

1. 用户打开带群号的问卷链接：`https://your-domain/survey?group_id=1101292217`
2. Web 后端校验 `group_id` 全为数字（防路径穿越）
3. 打开 `/home/admin/nbbot/src/plugins/survey_whitelist/DB/1101292217.db`
4. `SELECT 1 FROM group_members WHERE qq_number = ?` 校验白名单
5. `INSERT INTO survey_responses (qq_number, survey_id, answers) VALUES (?, ?, ?) ON CONFLICT (qq_number) DO NOTHING`
6. `rowcount == 0` 表示“你在本群已提交过”，否则提交成功

### 2.3 一次定时同步的完整链路

1. `CronTrigger` 在 `0 0 1 * *`（每月 1 日 0 点，`Asia/Shanghai`）触发
2. `sync_all_groups_job` 取第一个已连接 bot 的 `get_group_list()`
3. 逐群 `get_group_member_list()`，群间 `sleep(2)` 降低风控概率
4. 每群写自己的 `.db`，单群失败只记录、不中断其他群
5. 汇总日志“成功 X 群，新增 Y 人，失败 Z 群”
6. 若 `auto_sync_notify_superuser=true`，逐个私聊超管汇报

---

## 3. 架构与分层

### 3.1 依赖方向

严格单向，禁止反向 import：

```
__init__.py  →  handlers.py  →  db.py  →  config.py
scheduler.py →  config.py、db.py
__init__.py  →  scheduler.py（仅调用 register_scheduler）
```

### 3.2 每层职责

| 文件 | 职责 | 禁止 |
| --- | --- | --- |
| `config.py` | 字段声明、默认值、pydantic 校验 | 业务判断、IO |
| `db.py` | 纯 sqlite3 操作，返回纯 Python 值 | import 任何 NoneBot 事件类型 |
| `handlers.py` | 命令处理、参数转换、异常转话术、线程池调用 | 写 SQL、注册响应器 |
| `scheduler.py` | 每月同步逻辑 | 注册命令、处理事件 |
| `__init__.py` | 元数据、启动钩子、命令注册、定时任务注册 | 任何 `await` 业务调用、任何 SQL |

这样分层的目的：

- `db.py` 不依赖事件系统，可以原样给 Web 后端参考或复制（但注意 Web 端不要直接 import，见 10.7）
- 改命令名/权限只动 `__init__.py`
- 改业务流程只动 `handlers.py`
- 改表结构只动 `db.py`

### 3.3 数据归属

| 表 | 机器人 | Web 端 |
| --- | --- | --- |
| `group_members` | 只写 | 只读 |
| `survey_responses` | 只读 | 只写 |

两边写不同的表，加上 WAL 模式，写锁冲突概率很低。

---

## 4. 关键设计决策与处理方式

### 4.1 多群隔离：每群一个独立 DB 文件

**1.x 的做法**：单库 `survey.db`，`group_members` 用复合主键 `(qq_number, group_id)`，所有查询都带 `group_id` 过滤。风险是漏一个过滤条件就串群。

**2.0 的做法**：每群一个 `<group_id>.db`，表结构极简（`qq_number` 单主键），群隔离由“不同文件”完成，SQL 写错也串不了群。

适用边界：

| 场景 | 建议 |
| --- | --- |
| 群数量 2–10 个 | 多文件明显更简单（当前方案） |
| 群数量上百 | 应改回单库 + `group_id` |
| 需要频繁跨群聚合统计 | 多文件要遍历，不方便 |

当前场景（少数群、月度调查、分群看结果）适合多文件方案。

### 4.2 踩坑：scheduler 导入必须用别名（已修复）

**现象**

插件加载失败，`/同步名单` 和 `/未填名单` 全部不可用，异常：

```
AttributeError: module 'src.plugins.survey_whitelist.scheduler'
has no attribute 'add_job'
```

**根因**

`__init__.py` 里先绑定 `scheduler`，随后导入本插件的 `.scheduler` 子模块，Python 会把包的 `scheduler` 属性覆盖成子模块对象，于是传给 `register_scheduler` 的是模块，而不是 apscheduler 的调度器实例。

错误写法：

```python
from nonebot_plugin_apscheduler import scheduler
from .scheduler import register_scheduler

register_scheduler(scheduler)   # 这里的 scheduler 可能已被覆盖成模块
```

**修复**

使用别名，并把 `register_scheduler` 的形参改名，彻底消除歧义：

```python
try:
    from nonebot_plugin_apscheduler import scheduler as aps_scheduler
    from .scheduler import register_scheduler

    register_scheduler(aps_scheduler)
except ImportError:
    logger.warning(
        "[survey_whitelist] 未安装 nonebot-plugin-apscheduler，"
        "自动同步不可用。执行 pip install nonebot-plugin-apscheduler 后重启。"
    )
except Exception:
    logger.exception(
        "[survey_whitelist] 自动同步注册失败，手动命令仍可用"
    )
```

`scheduler.py` 中：

```python
def register_scheduler(sched) -> None:
    ...
    sched.add_job(
        sync_all_groups_job,
        trigger=trigger,
        id="survey_whitelist_monthly_sync",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
```

**设计原则**

手动命令是核心功能，定时任务是增强功能。定时任务注册失败（`except Exception` 兜底）不能拖垮手动命令。

### 4.3 注册 ≠ 启动：四条日志缺一不可

`add_job` 成功只代表任务挂到了调度器实例上，**不代表任务会跑**。必须同时满足：

- 插件加载 → `add_job` 成功
- `nonebot_plugin_apscheduler` 被 NoneBot 加载 → 其 `on_startup` 执行 `scheduler.start()`

判定标准是以下四条日志同时出现：

```
[SUCCESS] nonebot | Succeeded to load plugin "survey_whitelist"
[INFO] survey_whitelist | [survey_whitelist] 数据库目录就绪：/home/admin/nbbot/src/plugins/survey_whitelist/DB
[INFO] survey_whitelist | [survey_whitelist] 自动同步已注册：cron=0 0 1 * * tz=Asia/Shanghai
[INFO] nonebot_plugin_apscheduler | Scheduler Started
```

判定：

- 第 1 条：插件加载成功，无 ERROR / Traceback
- 第 2 条：启动钩子正常，DB 目录已创建
- 第 3 条：`add_job` 成功
- 第 4 条：调度器已启动，任务会真正被触发

**只有“自动同步已注册”而没有“Scheduler Started”属于假阳性**——任务挂上了但永远不会跑。

当前 `bot.py` 已加载 `nonebot_plugin_apscheduler`（此前日志已出现 `Scheduler Started`），不需要额外 `load_plugin`。

### 4.4 时区

- cron 触发时间显式指定 `timezone="Asia/Shanghai"`，不依赖服务器时区
- 但 SQL 里的 `datetime('now','localtime')` 仍取服务器本地时区

如果服务器是 UTC，库里的时间会比北京时间早 8 小时。

核查：

```bash
timedatectl | grep "Time zone"
```

若为 UTC，二选一：

- 方案 A（推荐）：`timedatectl set-timezone Asia/Shanghai`
- 方案 B：SQL 改为 `datetime('now')` 存 UTC，前端展示时转换

改动前先确认 Web 端时间展示逻辑，避免出现“提交时间倒退 8 小时”。

### 4.5 迁移顺序不可颠倒

必须严格按：

```
备份 DB 目录 → 跑迁移脚本 → 启动新版
```

如果先启动新版：`ensure_db` 会在首次访问某群时建出空的分群库；之后跑迁移脚本会因目标文件已存在而 `skip`；旧白名单数据全部丢失。

迁移脚本见 7.2。迁移完成后建议把旧 `survey.db` 改名为 `survey.db.old` 保留。

### 4.6 run.sh 合并解析的风险等级

`run.sh` 递归扫描插件目录下所有 `requirements*.txt`，合并成一条：

```bash
pip install -r A -r B ...
```

- 优点：相同依赖去重，版本约束取交集，冲突能被 pip 直接发现
- 风险：任一插件 requirements 写错，或与其他插件版本约束互斥，会导致 `ResolutionImpossible`，整个 `./run.sh` 失败，机器人起不来

这是把“单插件坏”放大成“全机器人坏”。应对：

- 改动任何插件的 `requirements.txt` 后，先在本地或临时 venv 里试一次 `pip install -r ...` 的解析
- 确认无冲突后再上服务器

### 4.7 run.sh 权限与换行符

部署时遇到过 `./run.sh: Permission denied`。处理：

```bash
chmod +x /home/admin/nbbot/run.sh
file /home/admin/nbbot/run.sh
# 若输出含 CRLF（Windows 上传导致）：
sed -i 's/\r$//' /home/admin/nbbot/run.sh
```

临时绕过（不改权限）：

```bash
bash /home/admin/nbbot/run.sh
```

### 4.8 数据库连接的健壮性

`db._connect()` 的处理方式：

```python
conn = sqlite3.connect(db_file, timeout=config.busy_timeout_ms / 1000)
try:
    conn.execute(f"PRAGMA busy_timeout={int(config.busy_timeout_ms)}")
    if config.wal_enabled:
        conn.execute("PRAGMA journal_mode=WAL")
    if config.foreign_keys_enabled:
        conn.execute("PRAGMA foreign_keys=ON")
except Exception:
    conn.close()
    raise
return conn
```

要点：

- `busy_timeout` 必须先于 WAL。切 WAL 需要短暂排他锁，没有 timeout 会立刻抛 `database is locked`
- PRAGMA 失败时关闭连接再 `raise`，不静默吞
- 每次调用新建连接、用完关闭（短连接），避免 sqlite3 跨线程使用问题
- 所有 sqlite 调用都经 `run_in_executor`，不阻塞事件循环

### 4.9 db.py 的日志回退

```python
import logging

try:
    from nonebot.log import logger
except ImportError:
    logger = logging.getLogger(__name__)
```

NoneBot 环境下不会误触发；脱离 NoneBot 复用时（如 Web 端参考代码）也不会因为缺少 `nonebot` 直接 ImportError。

### 4.10 插件元数据

```python
__plugin_meta__ = PluginMetadata(
    name="survey_whitelist",
    description="月度调查白名单同步与催填查询（每群独立库 + 定时同步）",
    usage=(...),
    type="application",
    config=Config,
    supported_adapters={"~onebot.v11"},
)
```

核查点：

- `supported_adapters` 必须是 `"~onebot.v11"`（带 `~`），不是 `"onebot.v11"`
- `config=Config` 与 `from .config import Config, config` 一致，NoneBot 可以正确识别插件配置模型

### 4.11 handlers 的实际文案

以代码为准，不是旧版“等 N 人”的写法：

| 条件 | 回复 |
| --- | --- |
| 未填人数 ≤ 上限 | `未填名单（N 人）：QQ1、QQ2、...` |
| 未填人数 > 上限 | `未填名单（共 N 人，仅展示前 L 人）：QQ1、...`<br>`…… 其余 X 人未展示。` |
| 全部已填 | `所有人都已提交。` |
| 成员列表为空 | `群成员列表为空，可能接口受限或群号错误。` |

`handlers.py` 的其他要点：

- `group_id = str(event.group_id)`，与 db 层类型对齐
- 外部调用失败 → `logger.exception` + `matcher.finish` 给用户可读提示
- 用 `matcher.finish()` 而不是 `sync_cmd.finish()`，切断对注册对象的依赖
- 空列表单独判断，区别于“接口报错”

---

## 5. 代码骨架

> 完整代码在插件目录内，本章只描述职责与关键参数，便于快速定位。

### 5.1 config.py

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `cmd_sync` | `"同步名单"` | 同步命令触发词 |
| `cmd_unfilled` | `"未填名单"` | 催填命令触发词 |
| `db_dir` | `<插件目录>/DB` | 每群 `.db` 的存放目录，运行时解析为绝对路径 |
| `busy_timeout_ms` | `30000` | SQLite 写锁等待毫秒数 |
| `wal_enabled` | `true` | 是否启用 WAL |
| `foreign_keys_enabled` | `true` | 是否启用外键约束 |
| `unfilled_preview_limit` | `50` | 未填名单最多展示几个 QQ |
| `auto_sync_enabled` | `true` | 是否启用自动同步 |
| `auto_sync_cron` | `"0 0 1 * *"` | 每月 1 日 0 点 |
| `auto_sync_timezone` | `"Asia/Shanghai"` | 显式时区 |
| `auto_sync_notify_superuser` | `true` | 同步完成后是否私聊超管 |

环境变量前缀：`SURVEY_WHITELIST_`（大小写不敏感）。

```ini
SURVEY_WHITELIST_DB_DIR=/home/admin/nbbot/src/plugins/survey_whitelist/DB
SURVEY_WHITELIST_CMD_SYNC=同步名单
SURVEY_WHITELIST_CMD_UNFILLED=未填名单
SURVEY_WHITELIST_BUSY_TIMEOUT_MS=30000
SURVEY_WHITELIST_WAL_ENABLED=true
SURVEY_WHITELIST_FOREIGN_KEYS_ENABLED=true
SURVEY_WHITELIST_UNFILLED_PREVIEW_LIMIT=50
SURVEY_WHITELIST_AUTO_SYNC_ENABLED=true
SURVEY_WHITELIST_AUTO_SYNC_CRON=0 0 1 * *
SURVEY_WHITELIST_AUTO_SYNC_TIMEZONE=Asia/Shanghai
SURVEY_WHITELIST_AUTO_SYNC_NOTIFY_SUPERUSER=true
```

> 旧版的 `SURVEY_WHITELIST_DB_PATH` 已废弃，不再生效。

### 5.2 db.py 关键函数

| 函数 | 行为 |
| --- | --- |
| `_db_file(group_id)` | `group_id` 必须 `isdigit()`，否则抛 `ValueError`（防路径穿越）；返回 `<db_dir>/<group_id>.db` |
| `_connect(group_id)` | 短连接；先 `busy_timeout` 再 WAL；可选 `foreign_keys=ON`；PRAGMA 失败关闭连接并 raise |
| `ensure_db(group_id)` | 幂等建表，首次访问某群时自动创建库与表 |
| `init_db_dir()` | 启动时只创建 DB 目录，不建具体群的库 |
| `upsert_group_members(group_id, members)` | `ON CONFLICT(qq_number) DO NOTHING`，返回本次新增人数 |
| `query_unfilled(group_id)` | 本群白名单 LEFT JOIN 本群答卷，返回未提交的 QQ 列表（按 QQ 号排序） |

### 5.3 每群库的表结构

```sql
CREATE TABLE IF NOT EXISTS group_members (
    qq_number TEXT PRIMARY KEY,
    synced_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS survey_responses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    qq_number    TEXT NOT NULL UNIQUE,
    survey_id    TEXT,
    answers      TEXT NOT NULL,
    submitted_at TEXT DEFAULT (datetime('now', 'localtime'))
);
```

关键点：

- 两张表都**没有 `group_id` 列**，群隔离由不同文件完成
- `UNIQUE(qq_number)` = 同一群一人一份
- `survey_id` 由 Web 端写入，机器人不使用
- `AUTOINCREMENT` 必须显式写 `INTEGER PRIMARY KEY AUTOINCREMENT`

### 5.4 scheduler.py

`sync_all_groups_job()`：

1. `get_bots()` 取第一个已连接 bot；没有则 warning 跳过
2. `bot.get_group_list()` 取所有群；空则跳过
3. 逐群 `get_group_member_list()` → `run_in_executor(upsert_group_members, ...)`
4. 群间 `asyncio.sleep(2)` 降低风控概率
5. 单群失败只记入 `failed`，不影响其他群
6. 汇总日志：`成功 X 群，新增 Y 人，失败 Z 群`
7. `notify_superuser=true` 时逐个私聊超管汇报

`register_scheduler(sched)`：

- `auto_sync_enabled=false` 时只记录“自动同步已关闭”并返回
- `CronTrigger.from_crontab(config.auto_sync_cron, timezone=config.auto_sync_timezone)`
- `add_job` 参数：
  - `id="survey_whitelist_monthly_sync"`
  - `replace_existing=True`
  - `misfire_grace_time=3600`（错过 1 小时内补跑）
  - `coalesce=True`（多次错过只跑一次）
  - `max_instances=1`（不允许并发）

### 5.5 __init__.py 装配内容

- `__plugin_meta__`（见 4.10）
- `@_driver.on_startup` 挂 `init_db_dir`（走 `run_in_executor`）
- `_admin_permission = SUPERUSER | GROUP_ADMIN | GROUP_OWNER`
- `sync_cmd` / `unfilled_cmd` 注册 + `append_handler`
- 文件末尾 `try/except` 注册定时任务（见 4.2）
- `GROUP_ADMIN` / `GROUP_OWNER` 从 `nonebot.adapters.onebot.v11` 导入，`SUPERUSER` 从 `nonebot.permission` 导入

---

## 6. 依赖与运行环境

### 6.1 requirements.txt

插件目录内（无注释）：

```text
nonebot2>=2.2.0
nonebot-adapter-onebot>=2.4.0
nonebot-plugin-apscheduler>=0.5.0
pydantic>=1.10,<3
```

### 6.2 运行环境

- Python：3.10+（推荐 3.11 / 3.12）
- NoneBot2：2.2+
- OneBot v11 适配器
- SQLite：Python 标准库 `sqlite3`，无需单独安装
- 机器人端另需 OneBot v11 实现（NapCat / Lagrange）接入 NoneBot

单独确认 apscheduler 是否装上：

```bash
/home/admin/nb_venv/bin/pip show nonebot-plugin-apscheduler
```

### 6.3 run.sh 的依赖处理

- 默认扫描目录：`PLUGINS_DIR="${PLUGINS_DIR:-$PROJECT_DIR/src/plugins}"`
- 递归匹配：`requirements*.txt` 和名为 `requirements` 的文件
- 找到多个文件时合并成一条 `pip install -r A -r B ...`
- 相同依赖自动去重，版本约束取交集，冲突直接报 `ResolutionImpossible`
- 未找到任何 requirements 时只提示，不中断
- 机器人核心依赖（`fastapi` / `uvicorn` / `httpx` / `websockets` / `aiohttp`）仍单独检查安装

可覆盖扫描目录：

```bash
PLUGINS_DIR=/path/to/plugins ./run.sh
```

---

## 7. 部署与使用说明

### 7.1 部署前检查

- [ ] `bot.py` 会加载 `src/plugins` 下的插件
- [ ] `bot.py` 已加载 `nonebot_plugin_apscheduler`
- [ ] `__init__.py` 使用 `from nonebot_plugin_apscheduler import scheduler as aps_scheduler`
- [ ] `scheduler.py` 的 `register_scheduler(sched)` 形参是 `sched`
- [ ] 插件目录内 `requirements.txt` 存在且无语法错误

### 7.2 备份与数据迁移

**第一步：停止机器人，备份 DB 目录。**

```bash
cp -r /home/admin/nbbot/src/plugins/survey_whitelist/DB \
      /home/admin/nbbot/src/plugins/survey_whitelist/DB.bak.$(date +%s)
```

备份前建议合并 WAL：

```bash
for f in /home/admin/nbbot/src/plugins/survey_whitelist/DB/*.db; do
    sqlite3 "$f" "PRAGMA wal_checkpoint(TRUNCATE);"
done
```

**第二步：确认旧答卷可丢弃。**

```bash
sqlite3 /home/admin/nbbot/src/plugins/survey_whitelist/DB/survey.db \
  "SELECT COUNT(*) FROM survey_responses;"
```

旧答卷没有群归属信息，迁移脚本不迁移答卷数据。

**第三步：执行迁移脚本（一次性）。**

```python
python3 - <<'PY'
import sqlite3
from pathlib import Path

OLD = "/home/admin/nbbot/src/plugins/survey_whitelist/DB/survey.db"
NEW_DIR = Path("/home/admin/nbbot/src/plugins/survey_whitelist/DB")
NEW_DIR.mkdir(parents=True, exist_ok=True)

old = sqlite3.connect(OLD)
groups = [r[0] for r in old.execute(
    "SELECT DISTINCT group_id FROM group_members"
)]

for gid in groups:
    dst = NEW_DIR / f"{gid}.db"
    if dst.exists():
        print(f"skip {gid}: {dst} already exists")
        continue
    conn = sqlite3.connect(dst)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS group_members (
        qq_number TEXT PRIMARY KEY,
        synced_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS survey_responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        qq_number TEXT NOT NULL UNIQUE,
        survey_id TEXT,
        answers TEXT NOT NULL,
        submitted_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    rows = old.execute(
        "SELECT qq_number, synced_at FROM group_members WHERE group_id = ?",
        (gid,)
    ).fetchall()
    conn.executemany(
        "INSERT INTO group_members (qq_number, synced_at) VALUES (?, ?) "
        "ON CONFLICT (qq_number) DO NOTHING",
        rows,
    )
    conn.commit()
    print(f"migrated {gid}: {len(rows)} members -> {dst}")
    conn.close()

old.close()
PY
```

**第四步：验证迁移结果。**

```bash
for f in /home/admin/nbbot/src/plugins/survey_whitelist/DB/*.db; do
    echo -n "$f: "
    sqlite3 "$f" "SELECT COUNT(*) FROM group_members;"
done
```

预期：

- `1101292217.db` → 4
- `752775661.db` → 303

迁移后建议把旧库改名保留：

```bash
mv /home/admin/nbbot/src/plugins/survey_whitelist/DB/survey.db \
   /home/admin/nbbot/src/plugins/survey_whitelist/DB/survey.db.old
```

> 如果迁移脚本提示 `skip`，说明目标群库已存在（通常是新版先启动过）。先确认库内容，必要时删除空库重跑迁移，避免旧数据丢失。

### 7.3 启动

```bash
chmod +x /home/admin/nbbot/run.sh
file /home/admin/nbbot/run.sh     # 有 CRLF 就 sed -i 's/\r$//'
/home/admin/nbbot/run.sh
```

`run.sh` 会：

1. 激活 `/home/admin/nb_venv`
2. 扫描插件目录所有 requirements 并合并安装
3. 检查机器人核心依赖
4. 杀掉旧 bot 进程
5. 按需清理系统缓存
6. `nohup python bot.py` 后台启动
7. 等待 10 秒并检查进程与日志

查看日志：

```bash
tail -f /home/admin/nbbot/bot.log
```

### 7.4 命令使用

| 命令 | 权限 | 作用 | 成功回复示例 |
| --- | --- | --- | --- |
| `/同步名单` | 超管 / 本群群主 / 本群管理员 | 拉取本群成员写入本群白名单 | `同步完成：群内共 4 人，本次新增白名单 0 人（已存在的自动跳过）。` |
| `/未填名单` | 超管 / 本群群主 / 本群管理员 | 查询本群尚未提交答卷的成员 | `未填名单（4 人）：...` |

命令字符可通过 `.env` 修改，不用改代码：

```ini
SURVEY_WHITELIST_CMD_SYNC=同步名单
SURVEY_WHITELIST_CMD_UNFILLED=未填名单
```

权限说明：

- 超管：任意群可执行
- 群主 / 群管理员：只能在本群执行，去别的群发无响应
- 普通群员：无响应（静默拒绝）

### 7.5 日常配置修改

- 改命令词：`SURVEY_WHITELIST_CMD_SYNC` / `SURVEY_WHITELIST_CMD_UNFILLED`，重启生效
- 改自动同步时间：`SURVEY_WHITELIST_AUTO_SYNC_CRON`（五段 cron），重启生效
- 关闭自动同步：`SURVEY_WHITELIST_AUTO_SYNC_ENABLED=false`
- 改展示条数：`SURVEY_WHITELIST_UNFILLED_PREVIEW_LIMIT`
- 改 DB 目录：`SURVEY_WHITELIST_DB_DIR`，注意 Web 端要同步修改

### 7.6 备份与恢复

```bash
# 备份
cp -r /home/admin/nbbot/src/plugins/survey_whitelist/DB \
      /home/admin/nbbot/src/plugins/survey_whitelist/DB.bak.$(date +%s)

# 恢复
cp -r /home/admin/nbbot/src/plugins/survey_whitelist/DB.bak.<时间戳>/* \
      /home/admin/nbbot/src/plugins/survey_whitelist/DB/
```

恢复前先停止机器人，恢复后重启。

### 7.7 新增插件 / 新增依赖

- 新插件在自己的插件目录放 `requirements.txt`
- `run.sh` 会自动扫描并合并安装，不需要改脚本
- 修改任何 requirements 后，先在本地验证 pip 解析，避免上线时 `ResolutionImpossible`

---

## 8. 上线验证清单

> 严格按顺序执行。标记 `[ ]` 的项全部通过才算上线完成。

### Step 1 代码核对（不启动）

- [ ] `__init__.py` 末尾是 `from nonebot_plugin_apscheduler import scheduler as aps_scheduler`
- [ ] `scheduler.py` 的 `register_scheduler(sched)` 形参为 `sched`，函数内是 `sched.add_job(...)`
- [ ] `bot.py` 已加载 `nonebot_plugin_apscheduler`
- [ ] 新的 `__init__.py` / `scheduler.py` 已上传到服务器

### Step 2 备份 + 迁移（必须先做）

- [ ] 备份 DB 目录
- [ ] 确认旧 `survey_responses` 可丢弃
- [ ] 执行迁移脚本
- [ ] 逐库核对人数：`1101292217.db` = 4，`752775661.db` = 303
- [ ] 旧库改名保留（可选）

### Step 3 启动并确认四条日志

- [ ] `[SUCCESS] nonebot | Succeeded to load plugin "survey_whitelist"`
- [ ] `[INFO] survey_whitelist | [survey_whitelist] 数据库目录就绪：/home/admin/nbbot/src/plugins/survey_whitelist/DB`
- [ ] `[INFO] survey_whitelist | [survey_whitelist] 自动同步已注册：cron=0 0 1 * * tz=Asia/Shanghai`
- [ ] `[INFO] nonebot_plugin_apscheduler | Scheduler Started`

四条缺一不可；只有“自动同步已注册”没有“Scheduler Started”是假阳性。

### Step 4 手动命令与权限

- [ ] 群 `1101292217` 用超管发 `/同步名单`，返回 4 人
- [ ] 再发一次，新增仍为 0（幂等）
- [ ] 群 `752775661` 同样发一次，返回 303 人
- [ ] 普通群员发命令：无响应
- [ ] 群管理/群主：本群正常执行，去另一群无响应
- [ ] 超管：两个群都可执行

### Step 5 跨群隔离

以 `3365220669` 为例（该号在两个群都有）：

```bash
sqlite3 /home/admin/nbbot/src/plugins/survey_whitelist/DB/1101292217.db \
  "INSERT INTO survey_responses (qq_number, survey_id, answers)
   VALUES ('3365220669', 'test', '{}');"
```

- [ ] 群 `1101292217` 的未填查询只剩 3 人（`3365220669` 消失）

```bash
sqlite3 /home/admin/nbbot/src/plugins/survey_whitelist/DB/1101292217.db \
  "SELECT qq_number FROM group_members m
   LEFT JOIN survey_responses r ON m.qq_number = r.qq_number
   WHERE r.qq_number IS NULL;"
```

- [ ] 群 `752775661` 的未填查询仍返回 `3365220669`

```bash
sqlite3 /home/admin/nbbot/src/plugins/survey_whitelist/DB/752775661.db \
  "SELECT qq_number FROM group_members m
   LEFT JOIN survey_responses r ON m.qq_number = r.qq_number
   WHERE r.qq_number IS NULL AND m.qq_number = '3365220669';"
```

- [ ] 清理测试数据：

```bash
sqlite3 /home/admin/nbbot/src/plugins/survey_whitelist/DB/1101292217.db \
  "DELETE FROM survey_responses WHERE survey_id = 'test';"
```

### Step 6 定时任务

- [ ] `.env` 临时设置 `SURVEY_WHITELIST_AUTO_SYNC_CRON="* * * * *"`
- [ ] 重启，日志每分钟出现“定时同步完成：成功 2 群，新增 0 人，失败 0 群”
- [ ] 超管 QQ 收到私聊汇总
- [ ] 改回 `SURVEY_WHITELIST_AUTO_SYNC_CRON="0 0 1 * *"` 并重启

### Step 7 更新文档状态

- [ ] 把本文档 0.2 的“待验证”项结果如实更新为已完成，或记录未通过项

---

## 9. 风险与应对

| # | 风险 | 影响 | 应对 |
| --- | --- | --- | --- |
| 1 | DB 在插件目录内，整目录替换部署 | 白名单/答卷被覆盖丢失 | 部署前备份 `DB/`，备份前 `wal_checkpoint(TRUNCATE)` |
| 2 | 机器人与 Web 端用户不同、权限不足 | `Permission denied`，读写失败 | 两端同用户运行（如都用 admin），或给 DB 目录组权限 |
| 3 | OneBot v11 非官方协议 | 风控/封号风险 | 机器人用小号；定时任务群间 `sleep(2)`；避免高频手动同步 |
| 4 | 被移出群 | 该群 `.db` 保留但不再更新 | 需要时手动删除该群 `.db`，不影响其他群 |
| 5 | 未装 `nonebot-plugin-apscheduler` | 自动同步不可用 | 插件仍可加载，手动命令正常；安装后重启即可 |
| 6 | `run.sh` 合并解析失败 | 整个机器人起不来 | 改 requirements 前先本地验证 pip 解析 |
| 7 | `group_id` 未校验就拼文件名 | 路径穿越 | 插件端 `isdigit()`；Web 端也必须 `isdigit()` |
| 8 | 服务器时区为 UTC | 库内时间比北京时间早 8 小时 | `timedatectl set-timezone Asia/Shanghai`，或改 SQL 存 UTC + 前端转换 |
| 9 | 迁移顺序颠倒 | 旧白名单丢失 | 严格“备份 → 迁移 → 启动”，迁移脚本 skip 时先核查目标库 |
| 10 | scheduler 导入被同名模块覆盖 | 插件加载失败（已修复） | 使用 `aps_scheduler` 别名；`register_scheduler(sched)` |

---

## 10. 附录 A：Web 后端对接约定

### 10.1 共享目录

Web 后端必须指向同一个目录：

```text
/home/admin/nbbot/src/plugins/survey_whitelist/DB/
```

按群拼文件：

```python
from pathlib import Path

def db_file(group_id: str) -> Path:
    gid = str(group_id).strip()
    if not gid.isdigit():
        raise ValueError("invalid group_id")
    return Path("/home/admin/nbbot/src/plugins/survey_whitelist/DB") / f"{gid}.db"
```

`group_id` 必须 `isdigit()`，禁止直接拼用户输入（防路径穿越）。

### 10.2 分工

- `group_members`：机器人只写、Web 只读
- `survey_responses`：机器人只读、Web 只写

### 10.3 核心 SQL

白名单校验：

```sql
SELECT 1 FROM group_members WHERE qq_number = ?;
```

提交插入：

```sql
INSERT INTO survey_responses (qq_number, survey_id, answers)
VALUES (?, ?, ?)
ON CONFLICT (qq_number) DO NOTHING;
```

`rowcount == 0` 表示“该群已提交过”。

### 10.4 group_id 来源

每个群发不同的问卷链接：

```text
https://your-domain/survey?group_id=1101292217
```

前端从 URL 取，后端不信任用户自填的群号。

### 10.5 survey_id 建议

每群每月独立，格式 `<group_id>-YYYY-MM`，例如 `1101292217-2026-10`，便于导出时按群、按月区分。

### 10.6 SQLite 参数建议

Web 端也开启 WAL 与 `busy_timeout`，减少与机器人端的写锁竞争：

```python
conn = sqlite3.connect(db_path, timeout=30)
conn.execute("PRAGMA busy_timeout=30000")
conn.execute("PRAGMA journal_mode=WAL")
```

### 10.7 不要直接 import 机器人的 db.py

机器人的 `db.py` 依赖 `config.py`（pydantic + NoneBot 配置系统）和 NoneBot 日志，Web 端引入会拖进整个 NoneBot 依赖树。

正确做法：

- Web 端自己维护一份数据访问代码，只共享 SQLite 文件和表结构
- 两边表结构必须严格一致，任何一边改表结构，另一边同步改

---

## 11. 附录 B：速查

### 11.1 关键路径

```text
项目根：      /home/admin/nbbot
插件：        /home/admin/nbbot/src/plugins/survey_whitelist/
数据：        /home/admin/nbbot/src/plugins/survey_whitelist/DB/
日志：        /home/admin/nbbot/bot.log
启动脚本：    /home/admin/nbbot/run.sh
venv：        /home/admin/nb_venv
```

### 11.2 常用命令

```bash
# 启动
/home/admin/nbbot/run.sh

# 看日志
tail -f /home/admin/nbbot/bot.log

# 备份 DB
cp -r /home/admin/nbbot/src/plugins/survey_whitelist/DB \
      /home/admin/nbbot/src/plugins/survey_whitelist/DB.bak.$(date +%s)

# 查某群人数
sqlite3 /home/admin/nbbot/src/plugins/survey_whitelist/DB/1101292217.db \
  "SELECT COUNT(*) FROM group_members;"

# 查某群未填名单
sqlite3 /home/admin/nbbot/src/plugins/survey_whitelist/DB/1101292217.db \
  "SELECT m.qq_number FROM group_members m
   LEFT JOIN survey_responses r ON m.qq_number = r.qq_number
   WHERE r.qq_number IS NULL;"
```

### 11.3 四条启动日志

```text
[SUCCESS] nonebot | Succeeded to load plugin "survey_whitelist"
[INFO] survey_whitelist | [survey_whitelist] 数据库目录就绪：/home/admin/nbbot/src/plugins/survey_whitelist/DB
[INFO] survey_whitelist | [survey_whitelist] 自动同步已注册：cron=0 0 1 * * tz=Asia/Shanghai
[INFO] nonebot_plugin_apscheduler | Scheduler Started
```

### 11.4 一句话总结

> 每群一个独立 SQLite 文件，机器人负责写白名单、读答卷，Web 负责读白名单、写答卷，靠 `UNIQUE(qq_number)` 保证一人一次；每月 1 日凌晨自动同步所有群，手动命令始终优先可用。
