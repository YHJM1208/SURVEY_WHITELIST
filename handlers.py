"""业务编排层：命令的具体处理逻辑。

职责边界：
- 参数提取：把 OneBot 的 int 类型 group_id 转成 str，与 db 层类型对齐
- 调 db：同步阻塞的 sqlite3 调用一律丢进线程池，避免卡住事件循环
- 异常转话术：logger.exception 记堆栈，matcher.finish 给用户可读提示
- 结果格式化：截断长列表、区分“总人数”和“新增人数”

本模块不写 SQL、不注册响应器，因此可以被任意 Matcher 复用。
"""

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher

from .config import config
from .db import query_unfilled, upsert_group_members

__all__ = ["handle_sync", "handle_unfilled"]

T = TypeVar("T")


async def _run_in_executor(func: Callable[..., T], *args: Any) -> T:
    """把同步阻塞的数据库函数丢进默认线程池执行。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)


async def handle_sync(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    """拉取本群成员列表，写入 group_members 白名单。"""
    group_id = str(event.group_id)

    try:
        members = await bot.get_group_member_list(group_id=int(group_id))
    except Exception as exc:
        logger.exception("get_group_member_list 失败")
        await matcher.finish(f"拉取群成员失败：{exc}")

    if not members:
        await matcher.finish("群成员列表为空，可能接口受限或群号错误。")

    try:
        inserted = await _run_in_executor(upsert_group_members, group_id, members)
    except Exception as exc:
        logger.exception("写入白名单失败")
        await matcher.finish(f"写入白名单失败：{exc}")

    await matcher.finish(
        f"同步完成：群内共 {len(members)} 人，"
        f"本次新增白名单 {inserted} 人（已存在的自动跳过）。"
    )


async def handle_unfilled(event: GroupMessageEvent, matcher: Matcher) -> None:
    """查询本群白名单中尚未提交答卷的成员。"""
    group_id = str(event.group_id)

    try:
        unfilled = await _run_in_executor(query_unfilled, group_id)
    except Exception as exc:
        logger.exception("查询未填名单失败")
        await matcher.finish(f"查询未填名单失败：{exc}")

    if not unfilled:
        await matcher.finish("所有人都已提交。")

    total = len(unfilled)
    limit = config.unfilled_preview_limit

    if total <= limit:
        await matcher.finish(f"未填名单（{total} 人）：{'、'.join(unfilled)}")

    preview = "、".join(unfilled[:limit])
    await matcher.finish(
        f"未填名单（共 {total} 人，仅展示前 {limit} 人）：{preview}\n"
        f"…… 其余 {total - limit} 人未展示。"
    )
