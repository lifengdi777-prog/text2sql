import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, async_sessionmaker
from conf.app_config import DBConfig, app_config, DEFAULT_DATASOURCE_ID

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


class ClientRegistry:
    """按 (datasource_id, database) 懒加载并缓存 MySQLClient(连接池)。

    连接信息从 meta 库的 datasource 表取(密码解密);ds_default + 默认库直接复用进程级
    dw_mysql_client —— 同一个连接池,单源行为零变化。多源时按需为每个库各建一个池。
    """
    def __init__(self, meta_client: MySQLClient):
        self._meta_client = meta_client
        self._cache: dict[tuple[str, str], MySQLClient] = {}
        self._lock = asyncio.Lock()

    async def _resolve_config(self, datasource_id: str, database: str | None) -> DBConfig:
        if datasource_id == DEFAULT_DATASOURCE_ID:
            cfg = app_config.db_dw
            if database and database != cfg.database:
                return DBConfig(host=cfg.host, port=cfg.port, user=cfg.user,
                                password=cfg.password, database=database)
            return cfg
        # 延迟 import,避免与 repositories 的任何加载顺序耦合
        from repositories.datasource import DatasourceRepository
        async with self._meta_client.session() as session:
            repo = DatasourceRepository(session)
            ds = await repo.get_by_id(datasource_id)
            if ds is None:
                raise ValueError(f"数据源不存在: {datasource_id}")
            return repo.to_db_config(ds, database)

    async def get_client(
        self, datasource_id: str = DEFAULT_DATASOURCE_ID, database: str | None = None
    ) -> MySQLClient:
        # ds_default 且用默认库 → 复用进程级 dw_mysql_client(同一连接池,零新建)
        if datasource_id == DEFAULT_DATASOURCE_ID and (
            database is None or database == app_config.db_dw.database
        ):
            return dw_mysql_client
        # 指定了 database 时,命中缓存可免去查 meta 库这一跳
        if database is not None:
            cached = self._cache.get((datasource_id, database))
            if cached is not None:
                return cached
        async with self._lock:
            cfg = await self._resolve_config(datasource_id, database)
            key = (datasource_id, cfg.database)
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            client = MySQLClient(cfg)
            self._cache[key] = client
            return client

    async def close_all(self):
        """关闭所有按需建出的连接池(不含复用的 dw/meta 进程级客户端)。"""
        for client in self._cache.values():
            await client.close()
        self._cache.clear()


# 进程级单例:数据源连接信息从 meta 库查询。
client_registry = ClientRegistry(meta_mysql_client)

if __name__ == '__main__':
    async def test():
        #打开一个异步数据库会话，并把这个会话对象命名为 session
        async with dw_mysql_client.session() as session: # type: ignore
            result = await session.execute(text("select * from table_product limit 10"))
            rows = result.fetchall()
            print(rows)

    asyncio.run(test())

