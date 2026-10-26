"""入口：只做装配（元数据、启动钩子、响应器注册、定时任务注册）。

每群独立库模式：命令只在发起的群生效，各群的名单与答卷落在各自的
``<db_dir>/<group_id>.db`` 文件里，互不影响。

判断标准：删掉 handlers.py、db.py、scheduler.py 后，本文件应只剩
import 报错，而不是逻辑缺失。因此这里不写 SQL、不写 await bot.xxx。
"""

import asyncio

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .config import Config, config
from .db import init_db_dir
from .handlers import handle_sync, handle_unfilled

__plugin_meta__ = PluginMetadata(
    name="survey_whitelist",
    description="月度调查白名单同步与催填查询（每群独立库 + 定时同步）",
    usage=(
        f"/{config.cmd_sync} —— 同步本群成员到白名单\n"
        f"/{config.cmd_unfilled} —— 查询本群未提交答卷的成员\n"
        f"（群主 / 群管理员 / 超管可用；每月 1 日 0 点自动同步所有群）"
    ),
    type="application",
    config=Config,
    supported_adapters={"~onebot.v11"},
)


_driver = get_driver()


@_driver.on_startup
async def _startup_init_db_dir() -> None:
    """启动时确保数据库目录存在；走线程池，避免阻塞启动流程。"""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, init_db_dir)


# 各群自己的群主/管理员可在本群触发，超管可在任意群触发
_admin_permission = SUPERUSER | GROUP_ADMIN | GROUP_OWNER

sync_cmd = on_command(
    config.cmd_sync, permission=_admin_permission, priority=10, block=True
)
sync_cmd.append_handler(handle_sync)

unfilled_cmd = on_command(
    config.cmd_unfilled, permission=_admin_permission, priority=10, block=True
)
unfilled_cmd.append_handler(handle_unfilled)


# ── 定时任务注册（依赖缺失或注册失败都不影响手动命令）──
# 注意：导入本插件的 .scheduler 子模块会把包属性 scheduler 覆盖成该模块对象，
# 所以这里用 aps_scheduler 这个别名，避免拿到模块而不是调度器实例。
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
