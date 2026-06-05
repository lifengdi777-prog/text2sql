"""对象存储(MinIO / 兼容 S3)封装。

存两类东西:
  - 用户上传的原始 Excel:  ds_{id}/original/{文件名}
  - 各 sheet 清洗后的 parquet: ds_{id}/{sheet}.parquet

boto3 连 MinIO 的关键点:
  - signature_version=s3v4:MinIO 只认 v4 签名。
  - addressing_style=path:用 http://host:9000/bucket/key 形式;
    默认的 virtual-host 形式(bucket.host)在 localhost/IP 下解析不了。
"""
from __future__ import annotations

import io
from functools import lru_cache
from typing import Any

import boto3
import pandas as pd
from botocore.client import Config
from botocore.exceptions import ClientError

from conf.app_config import app_config
from core.log import logger


@lru_cache(maxsize=1)
def _client_and_bucket() -> tuple[Any, str]:
    """进程级单例 S3 client + bucket 名。未配置 s3 直接报错(上传功能必须配)。"""
    cfg = app_config.s3
    if cfg is None:
        raise RuntimeError("未配置 s3(对象存储),请在 app_config.yaml 添加 s3 段")
    client = boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        region_name=cfg.region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return client, cfg.bucket


def ensure_bucket() -> None:
    """启动时调:bucket 不存在就建。已存在则跳过。"""
    client, bucket = _client_and_bucket()
    try:
        client.head_bucket(Bucket=bucket)
        logger.info(f"对象存储 bucket '{bucket}' 已存在")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        # 404 / NoSuchBucket → 建桶;其它错误(权限/网络)直接抛
        if code in ("404", "NoSuchBucket", "NoSuchKey"):
            client.create_bucket(Bucket=bucket)
            logger.info(f"对象存储 bucket '{bucket}' 已创建")
        else:
            raise


# ───────── 字节级读写 ─────────────────────────────────────

def put_bytes(key: str, data: bytes, content_type: str | None = None) -> None:
    """上传原始字节(用于原始 Excel)。"""
    client, bucket = _client_and_bucket()
    extra: dict[str, Any] = {}
    if content_type:
        extra["ContentType"] = content_type
    client.put_object(Bucket=bucket, Key=key, Body=data, **extra)


def get_bytes(key: str) -> bytes:
    """下载对象为字节。"""
    client, bucket = _client_and_bucket()
    resp = client.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


# ───────── DataFrame ↔ parquet ────────────────────────────

def upload_df_parquet(key: str, df: pd.DataFrame) -> None:
    """DataFrame → parquet 字节流 → 上传(不落本地磁盘)。"""
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    put_bytes(key, buf.getvalue(), content_type="application/octet-stream")


def read_df_parquet(key: str) -> pd.DataFrame:
    """从对象存储读 parquet → DataFrame。"""
    raw = get_bytes(key)
    return pd.read_parquet(io.BytesIO(raw))


# ───────── 存在性 / 删除 ──────────────────────────────────

def object_exists(key: str) -> bool:
    """HEAD 探测对象是否存在(给缓存做跨 worker 一致性兜底)。"""
    client, bucket = _client_and_bucket()
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def list_prefix(prefix: str) -> list[str]:
    """列出某前缀下所有对象的 key(智能助手导出时按 ds_{id}/original/ 找原件)。"""
    client, bucket = _client_and_bucket()
    paginator = client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def delete_prefix(prefix: str) -> None:
    """删某 dataset 前缀下的所有对象(删数据集时用)。

    注意 prefix 必须带结尾斜杠(如 'ds_6/'),否则 'ds_6' 会误删 'ds_60/...'。
    """
    if not prefix.endswith("/"):
        prefix = prefix + "/"
    client, bucket = _client_and_bucket()
    paginator = client.get_paginator("list_objects_v2")
    to_delete: list[dict[str, str]] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            to_delete.append({"Key": obj["Key"]})
            # delete_objects 单次上限 1000
            if len(to_delete) == 1000:
                client.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
                to_delete = []
    if to_delete:
        client.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
