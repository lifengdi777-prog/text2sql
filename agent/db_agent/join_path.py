"""连接路径补全 & 扇出检测。

两者共用同一份"有向外键边"(data_relationship):
- from_table 是外键所在表(多的一方)、to_table 是被引用表(一的一方);
- 把边当无向图跑 BFS,就能在"已召回的表"之间补出缺失的中间表(多跳 JOIN);
- 看路径上某一跳是不是"一 → 多"(逆 FK 方向),就能判断按该维度聚合事实度量会不会重复计算(扇出)。

图只有"表"这么多个节点、十几条边,BFS 是微秒级的纯内存运算,不调用任何 LLM/embedding,
也绝不把整库 schema 塞给大模型——只补"连接必需"的中间表,且只带其主键/外键列。
"""
from collections import deque

from agent.schemas import WSAgentTableInfoState
from dtos.meta import DataRelationship
from repositories.mysql import MetaDBRepository


def _build_graph(relationships: list[DataRelationship]):
    """由外键边构建：无向邻接表(用于找路径) + 有向边集合(用于判方向/扇出)。

    directed 中的 (a, b) 表示 a 是多的一方、b 是一的一方(a.from_column 引用 b)。
    """
    adjacency: dict[str, set[str]] = {}
    directed: set[tuple[str, str]] = set()
    for rel in relationships:
        a, b = rel.from_table, rel.to_table
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
        directed.add((a, b))
    return adjacency, directed


def _shortest_path(adjacency, start, goal):
    """BFS 求 start→goal 的最短路径(节点列表)。不可达返回 None。"""
    if start == goal:
        return [start]
    visited = {start}
    queue: deque[list[str]] = deque([[start]])
    while queue:
        path = queue.popleft()
        for neighbor in adjacency.get(path[-1], ()):
            if neighbor in visited:
                continue
            new_path = path + [neighbor]
            if neighbor == goal:
                return new_path
            visited.add(neighbor)
            queue.append(new_path)
    return None


def _candidate_paths(adjacency, start, goal, max_extra: int = 1, k: int = 3) -> list[list[str]]:
    """枚举 start→goal 的候选路径(节点列表的列表),短的优先,最多 k 条。

    取最短跳数 L,收所有"跳数 ≤ L+max_extra"的简单路径(节点不重复)。
    - 只有一条连通路径时 → 只返回它(等价 _shortest_path,星型/雪花的常态);
    - 有多条不同表路径时 → 返回若干候选,交给上层把它们都带进表集、由 LLM 按列描述选对那条。
    不可达返回 []。max_extra=0 即退化为"只要最短"。
    """
    base = _shortest_path(adjacency, start, goal)
    if base is None:
        return []
    limit = (len(base) - 1) + max_extra            # 跳数上限 = 最短 + max_extra
    results: list[list[str]] = []
    stack: list[list[str]] = [[start]]
    while stack:
        path = stack.pop()
        node = path[-1]
        if node == goal:
            results.append(path)
            continue
        if len(path) - 1 >= limit:                 # 超过跳数上限,剪枝(保证枚举有界)
            continue
        for neighbor in adjacency.get(node, ()):
            if neighbor not in path:               # 简单路径:不绕回已在路径上的节点
                stack.append(path + [neighbor])
    results.sort(key=len)                          # 短路径优先
    return results[:k]


async def complete_join_path(table_infos: list[WSAgentTableInfoState], meta_repo: MetaDBRepository) -> list[WSAgentTableInfoState]:
    """补全连接路径：把"已选表 → 事实表"路径上缺失的中间表补进来(只带主键/外键列)。

    例：问"各城市的实际产量"，召回到了 factory(city) 与 production_record，但漏了中间的
    workshop；本函数沿 factory→production_record 的最短路径把 workshop 补回，JOIN 才接得上。

    只新增"连接必需"的中间表，不引入无关表，因此交给 LLM 的仍是最小连通表集。
    """
    if not table_infos:
        return table_infos
    relationships = await meta_repo.get_relationships()
    if not relationships:
        return table_infos

    adjacency, _ = _build_graph(relationships)
    present = {table_info.id for table_info in table_infos}
    # 以事实表为枢纽(星型/雪花的中心)；没有事实表时退化为以任一已选表为锚点。
    anchor = next((t.id for t in table_infos if t.role == "fact"), None) or next(iter(present))
    # 候选路径参数按数据源取(管理员在元数据编辑页可调;默认 1/3,稠密库可调大)。
    max_extra, k = await meta_repo.get_join_config()

    needed = set(present)
    for table_id in list(present):
        if table_id == anchor:
            continue
        # 枚举候选路径:单条(星型/雪花常态)直接并入;多条(稠密图/多重关系)把各候选路径的表【全部】并入,
        # 之后 add_extra_context 会把这些表之间的边连同列描述都列给 LLM,由 LLM 按用户问题选对应的那条路。
        for path in _candidate_paths(adjacency, table_id, anchor, max_extra=max_extra, k=k):
            needed.update(path)

    # 把缺失的中间表补进来：先带主键+外键(按 role)列。
    for table_id in needed - present:
        table_info = await meta_repo.get_table_info_by_id(table_id)
        if table_info is None:
            continue
        pfk_columns = await meta_repo.get_table_pfks_by_id(table_id)
        table_infos.append(WSAgentTableInfoState(
            id=table_id,
            name=table_info.name,
            role=table_info.role,
            description=table_info.description,
            columns=pfk_columns,
        ))

    # 关键:补「关系边端点列」。无 FK 约束时这些列的 role 可能是 dimension/measure(不是 foreign_key),
    # 按 role 筛会漏掉 JOIN 列。但只要 data_relationship(可由 ER 图人工维护)里有边,就必须把端点列带上,
    # 与 role 无关。对所有表(已选 + 新补)都补,且只补"两端都在最终表集合里"的边涉及的列。
    final_ids = {t.id for t in table_infos}
    for t in table_infos:
        edge_cols: set[str] = set()
        for rel in relationships:
            if rel.from_table == t.id and rel.to_table in final_ids:
                edge_cols.add(rel.from_column)
            if rel.to_table == t.id and rel.from_table in final_ids:
                edge_cols.add(rel.to_column)
        missing = [n for n in edge_cols if n not in {c.name for c in t.columns}]
        if missing:
            t.columns.extend(await meta_repo.get_columns_by_names(t.id, missing))

    return table_infos


def detect_fanout(table_infos: list[WSAgentTableInfoState], relationships: list[DataRelationship]) -> str | None:
    """扇出检测：若 事实表 → 某维度表 的连接路径含"一 → 多"跳(逆 FK 方向/经桥接表)，
    则按该维度对事实度量做 SUM/COUNT 会重复计算。返回警告文本；无风险返回 None。

    顺着 FK(多 → 一)走是安全的(每行事实只对应一行维度)；逆着 FK(一 → 多)走，
    一行会被复制成多行，聚合就会多算——多对多桥接表必然制造这种逆向跳。
    """
    if not table_infos or not relationships:
        return None
    fact = next((t for t in table_infos if t.role == "fact"), None)
    if fact is None:
        return None

    adjacency, directed = _build_graph(relationships)
    risky: list[str] = []
    for table_info in table_infos:
        # 只关心可作为分组维度的维表；事实表/桥接表本身不作为"按其分组"的对象。
        if table_info.id == fact.id or table_info.role != "dim":
            continue
        path = _shortest_path(adjacency, fact.id, table_info.id)
        if not path:
            continue
        for u, v in zip(path, path[1:]):
            # (v, u) 在 directed 里 → 这一跳是 v(一) → u... 反过来：从 u 走到 v 是 一 → 多。
            if (v, u) in directed:
                risky.append(table_info.name)
                break

    if not risky:
        return None
    names = "、".join(dict.fromkeys(risky))
    return (
        f"【扇出警告】事实表「{fact.name}」的度量经由一对多/多对多关系连接到「{names}」。"
        f"按「{names}」分组对事实度量做 SUM/COUNT 会因一行被复制成多行而重复计算，"
        f"各组结果存在重叠、不可跨组相加。若该度量无法唯一归因到该维度（缺少分摊比例字段），"
        f"应改用 COUNT(DISTINCT ...) 等不受扇出影响的口径，或在结果中明确说明数值含重复。"
    )
