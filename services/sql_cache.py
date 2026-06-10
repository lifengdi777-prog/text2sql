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
    """轻量归一化:去首尾空白 + 把连续空白合并成一个。

    让 "2024年  销售额 " 与 "2024年 销售额" 命中同一条缓存;
    但 "前三" 与 "前五" 这种一字之差仍是不同问题(精确匹配,不命中)。
    """
    return " ".join((q or "").split()).strip()


def make_cache_key(question: str, datasource_id: str, database: str | None, meta_version: int) -> str:
    """把四要素拼起来算 sha256 当缓存键(定长 64,正好做主键)。

    用 \\x1f(单元分隔符)隔开各段,避免不同字段内容拼接后产生歧义碰撞。
    """
    raw = f"{normalize_question(question)}\x1f{datasource_id}\x1f{database or ''}\x1f{meta_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
