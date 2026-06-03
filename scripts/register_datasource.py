"""命令行注册数据源(本地测试用,绕过 API/鉴权,直接写 meta 库)。

默认复用 app_config.db_dw 的 host/port/账号密码(同一台 MySQL),只需指定库名:
  uv run python -m scripts.register_datasource --name 电商库 --database e-commerce

也可显式指定其它服务器:
  uv run python -m scripts.register_datasource --name x --database d \
      --host 1.2.3.4 --port 3306 --user u --password p
注册成功后会打印 datasource_id,拿它去跑:
  uv run python -m scripts.generate_draft <datasource_id>
"""
import argparse
import asyncio
from uuid import uuid4

from sqlalchemy import text

from clients.mysql import MySQLClient, meta_mysql_client
from conf.app_config import DBConfig, app_config
from dtos.datasource import DatasourceCreate
from repositories.datasource import DatasourceRepository


async def _test(cfg: DBConfig) -> None:
    client = MySQLClient(cfg)
    try:
        async with client.session() as s:
            await s.execute(text("SELECT 1"))
    finally:
        await client.close()


async def main():
    dw = app_config.db_dw
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="展示名")
    parser.add_argument("--database", required=True, help="要接入的库名")
    parser.add_argument("--host", default=dw.host)
    parser.add_argument("--port", type=int, default=dw.port)
    parser.add_argument("--user", default=dw.user)
    parser.add_argument("--password", default=dw.password)
    parser.add_argument("--id", default=None, help="不传则自动生成 ds_<uuid>")
    args = parser.parse_args()

    cfg = DBConfig(host=args.host, port=args.port, user=args.user,
                   password=args.password, database=args.database)
    print(f"测试连接 {args.host}:{args.port}/{args.database} ...")
    await _test(cfg)
    print("连接 OK")

    ds_id = args.id or ("ds_" + uuid4().hex[:12])
    try:
        async with meta_mysql_client.session() as session:
            repo = DatasourceRepository(session)
            async with session.begin():
                await repo.add(DatasourceCreate(
                    id=ds_id, name=args.name, host=args.host, port=args.port,
                    username=args.user, password=args.password, default_database=args.database,
                ))
        print(f"[OK] 已注册数据源  id={ds_id}  name={args.name}  db={args.database}")
        print(f"下一步:  uv run python -m scripts.generate_draft {ds_id}")
    finally:
        await meta_mysql_client.close()


if __name__ == "__main__":
    asyncio.run(main())
