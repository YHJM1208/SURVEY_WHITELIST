"""定时任务层：每月自动同步机器人所在所有群的成员名单。

由 ``__init__.py`` 在插件装配阶段调用 :func:`register_scheduler` 注册；
若未安装 nonebot-plugin-apscheduler，本模块不会被导入，手动命令仍可用。
"""

import asyncio

from apscheduler.triggers.cron import CronTrigger
from nonebot import get_bots, get_driver
from nonebot.log import logger

from .config import config
from .db import upsert_group_members

__all__ = ["sync_all_groups_job", "register_scheduler"]


async def sync_all_groups_job() -> None:
    """遍历所有群拉取成员列表，写入各群独立的白名单库。"""
    bots = get_bots()
    if not bots:
        logger.warning("[survey_whitelist] 定时同步跳过：当前没有已连接的 bot")
        return

    bot = next(iter(bots.values()))

    try:
        groups = await bot.get_group_list()
    except Exception:
        logger.exception("[survey_whitelist] 定时同步失败：get_group_list 调用异常")
        return

    if not groups:
        logger.warning("[survey_whitelist] 定时同步：群列表为空")
        return

    loop = asyncio.get_running_loop()
    total_groups = 0
    total_new = 0
    failed: list[str] = []

    for group in groups:
        gid = str(group["group_id"])
        gname = group.get("group_name", "")
        try:
            members = await bot.get_group_member_list(group_id=int(gid))
            inserted = await loop.run_in_executor(
                None, upsert_group_members, gid, members
            )
            total_groups += 1
            total_new += inserted
            logger.info(
                f"[survey_whitelist] 定时同步 {gname}({gid})："
                f"{len(members)} 人，新增 {inserted}"
            )
        except Exception as exc:
            failed.append(gid)
            logger.exception(f"[survey_whitelist] 定时同步 {gname}({gid}) 失败：{exc}")
        await asyncio.sleep(2)  # 群间暂停，降低风控概率

    summary = (
        f"[survey_whitelist] 定时同步完成："
        f"成功 {total_groups} 群，新增 {total_new} 人，"
        f"失败 {len(failed)} 群"
    )
    if failed:
        summary += f"（失败群号：{', '.join(failed)}）"
    logger.info(summary)

    if config.auto_sync_notify_superuser:
        for superuser in get_driver().config.superusers:
            try:
                await bot.send_private_msg(user_id=int(superuser), message=summary)
            except Exception:
                logger.exception(f"[survey_whitelist] 私聊超管 {superuser} 失败")


def register_scheduler(sched) -> None:
    """把月度同步任务挂到 nonebot-plugin-apscheduler 的调度器上。"""
    if not config.auto_sync_enabled:
        logger.info("[survey_whitelist] 自动同步已关闭（auto_sync_enabled=false）")
        return

    trigger = CronTrigger.from_crontab(
        config.auto_sync_cron,
        timezone=config.auto_sync_timezone,
    )
    sched.add_job(
        sync_all_groups_job,
        trigger=trigger,
        id="survey_whitelist_monthly_sync",
        replace_existing=True,
        misfire_grace_time=3600,  # 错过 1 小时内补跑
        coalesce=True,  # 多次错过只跑一次
        max_instances=1,  # 不允许并发
    )
    logger.info(
        f"[survey_whitelist] 自动同步已注册："
        f"cron={config.auto_sync_cron} tz={config.auto_sync_timezone}"
    )
