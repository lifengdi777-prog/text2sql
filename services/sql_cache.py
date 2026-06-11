"""SQL 缓存的「键」工具。

缓存匹配的对象是 parse_query_intention 改写后的 standalone_query(自包含问题),
不是用户原始问句 —— 多轮追问的指代已被消解,这才是稳定可比对的键。

键 = 归一化问题 + datasource_id + database + meta_version,四者拼成一把唯一钥匙:
  · 绑数据源/库 → 同一句话问不同库不会串(钥匙不同,天然查不到对方缓存);
  · 带 meta_version → 元数据一重建(版本+1),旧键自动全部失效。
"""
from __future__ import annotations

import hashlib


def normalize_question(q: str) -> str:
    """轻量归一化:去首尾空白 + 连续空白合并 + 去首尾标点。

    让 "2024年  销售额 " 与 "2024年 销售额"、"…怎样的" 与 "…怎样的？"
    命中同一条缓存 —— 结尾问号/句号是 LLM 改写与用户手打之间最常见的无意义差异
    (路由器改写习惯补"？",手打往往不带),不归一会产生两条互不相认的重复缓存。
    只动首尾,问题中间的内容一字不改:"前三"与"前五"仍是不同问题(精确匹配,不命中)。
    """
    s = " ".join((q or "").split()).strip()
    # 全角+半角的常见句尾/句首标点;只 strip 首尾,不影响句中
    return s.strip("？?！!。.，,、;；:： ")


def make_cache_key(question: str, datasource_id: str, database: str | None, meta_version: int) -> str:
    """把四要素拼起来算 sha256 当缓存键(定长 64,正好做主键)。

    用 \\x1f(单元分隔符)隔开各段,避免不同字段内容拼接后产生歧义碰撞。
    """
    raw = f"{normalize_question(question)}\x1f{datasource_id}\x1f{database or ''}\x1f{meta_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
