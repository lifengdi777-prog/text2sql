import sys
from pathlib import Path

from loguru import logger

from conf.app_config import app_config

#定义了日志输出格式，包含时间、日志级别、模块名、函数名、行号和日志消息等信息。
# loguru 日志输出格式
log_format = (
    # 输出时间，绿色显示，例如：2026-05-08 14:25:30.123
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "

    # 输出日志级别，左对齐占 8 位，例如：INFO、ERROR、WARNING
    "<level>{level: <8}</level> | "

    # 输出日志所在位置：模块名:函数名:行号，青色显示
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "

    # 输出具体日志内容，颜色跟随日志级别变化
    "<level>{message}</level>"

    #例子：2026-05-08 14:29:26.440 | INFO     | __main__:main:7 - 这是一条logger消息.
)

logger.remove()
if app_config.logging.console.enable:
    logger.add(sink=sys.stdout, level=app_config.logging.console.level, format=log_format)
if app_config.logging.file.enable:
    path = Path(app_config.logging.file.path)
    path.mkdir(parents=True, exist_ok=True)
    logger.add(
        sink=path / "app.log",
        #日志级别从配置文件中读取，例如：DEBUG、INFO、ERROR
        level=app_config.logging.file.level,
        format=log_format,
        rotation=app_config.logging.file.rotation,
        retention=app_config.logging.file.retention,
        encoding="utf-8"
    )