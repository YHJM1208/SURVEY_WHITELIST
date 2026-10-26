"""配置层：插件全部可外部修改的参数。

所有配置通过 .env 注入，NoneBot 会自动加上前缀 ``SURVEY_WHITELIST_``
（大小写不敏感），例如 ``SURVEY_WHITELIST_DB_DIR`` 对应 ``db_dir``。

本模块只负责“声明 + 校验”，不做任何业务判断与 IO。
"""

from pathlib import Path

from nonebot import get_plugin_config
from pydantic import BaseModel, Field

#: 插件根目录（本文件所在目录）
_PLUGIN_DIR = Path(__file__).resolve().parent

#: 默认数据库目录：<插件目录>/DB，每群一个 <group_id>.db
_DEFAULT_DB_DIR = _PLUGIN_DIR / "DB"


class Config(BaseModel):
    """survey_whitelist 插件配置（非法值会在启动时直接报错）。"""

    # ── 命令字符：改这里即可换触发词 ──
    cmd_sync: str = Field(default="同步名单", min_length=1)
    cmd_unfilled: str = Field(default="未填名单", min_length=1)

    # ── DB 目录（每群一个 <group_id>.db 文件）──
    # 默认放插件目录下的 DB 文件夹；可用 SURVEY_WHITELIST_DB_DIR 覆盖
    db_dir: str = Field(default=str(_DEFAULT_DB_DIR), min_length=1)
    busy_timeout_ms: int = Field(default=30000, ge=0)
    wal_enabled: bool = True
    foreign_keys_enabled: bool = True

    # ── 展示参数 ──
    unfilled_preview_limit: int = Field(default=50, ge=1)

    # ── 自动同步（定时任务）──
    auto_sync_enabled: bool = True
    auto_sync_cron: str = Field(default="0 0 1 * *", min_length=1)  # 每月 1 日 0 点
    auto_sync_timezone: str = Field(default="Asia/Shanghai", min_length=1)
    auto_sync_notify_superuser: bool = True


#: 模块级单例，全插件共享
config = get_plugin_config(Config)
