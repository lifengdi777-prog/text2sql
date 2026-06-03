"""不经 HTTP/鉴权,直接跑一条问数(可指定数据源),打印步骤 + SQL + 结果。

跑法:
  uv run python -m scripts.query_once "各区域的总销售额" ds_d2251976bdfa
  uv run python -m scripts.query_once "各城市的实际产量"            # 不传则 ds_default
用于本地验证多源是否打通,以及不同 datasource 是否互相隔离。
"""
import asyncio
import sys

from api.agent_router import query_graph


def _short(v, n=600):
    s = str(v)
    return s if len(s) <= n else s[:n] + " …(截断)"


async def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "各区域的总销售额"
    datasource_id = sys.argv[2] if len(sys.argv) > 2 else "ds_default"
    print(f"问题: {query}\n数据源: {datasource_id}\n" + "-" * 60)

    async for chunk in query_graph(query, datasource_id=datasource_id):
        step = getattr(chunk, "step", None)
        status = getattr(chunk, "status", None)
        sql = getattr(chunk, "sql", None)
        data = getattr(chunk, "data", None)
        if step:
            print(f"[{status}] {step}")
        if sql:
            print(f"    SQL: {_short(sql)}")
        # 执行结果/解读等放在 data 里,挑能读的打印
        if data not in (None, "", [], {}):
            print(f"    data: {_short(data)}")


if __name__ == "__main__":
    asyncio.run(main())
