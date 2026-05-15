import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, async_sessionmaker
from conf.app_config import DBConfig, app_config

class MySQLClient:
    #需要传入一个DBConfig对象来初始化MySQLClient实例
    #传入由 DBConfig 创建出来的对象。
    def __init__(self, db_config: DBConfig):
        self.db_config = db_config
        self.db_uri = f"mysql+asyncmy://{self.db_config.user}:{self.db_config.password}@{self.db_config.host}:{self.db_config.port}/{self.db_config.database}?charset=utf8mb4"
        self.engine: AsyncEngine = create_async_engine(
            self.db_uri,
            # 将输出所有执行SQL的日志（默认是关闭的）
            echo=False,
            # 连接池大小（默认是5个）
            pool_size=5,
            # 允许连接池最大的连接数（默认是10个）
            max_overflow=20,
            # 获得连接超时时间（默认是30s）
            pool_timeout=10,
            # 连接回收时间（默认是-1，代表永不回收）
            pool_recycle=300,
            # 连接前是否预检查（默认为False）
            pool_pre_ping=True
        )
        self._AsyncSessionFactory = async_sessionmaker(
            self.engine,
            autoflush=True,
            expire_on_commit=False
        )

    def session(self):
        return self._AsyncSessionFactory()

    async def close(self):
        await self.engine.dispose()

#传入的app_config.db_dw和app_config.db_meta都是DBConfig对象，分别用于连接数据仓库和元数据库。
dw_mysql_client = MySQLClient(app_config.db_dw)
meta_mysql_client = MySQLClient(app_config.db_meta)

if __name__ == '__main__':
    async def test():
        #打开一个异步数据库会话，并把这个会话对象命名为 session
        async with dw_mysql_client.session() as session: # type: ignore
            result = await session.execute(text("select * from table_product limit 10"))
            rows = result.fetchall()
            print(rows)

    asyncio.run(test())

