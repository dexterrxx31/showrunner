"""Segment storage backends.

The store persists segment bytes somewhere the delivery layer (nginx/CDN in
prod, the app's static mount in dev) can find them. The catalog always records
a relative `/segments/{asset}/{file}` URI so manifests stay portable across
backends — the store just decides where the bytes physically live.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app import config


class SegmentStore(Protocol):
    def put(self, asset_id: str, filename: str, data: bytes) -> str:
        """Persist one segment and return its relative playlist URI."""
        ...


def _uri(asset_id: str, filename: str) -> str:
    return f"/segments/{asset_id}/{filename}"


class LocalSegmentStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def put(self, asset_id: str, filename: str, data: bytes) -> str:
        dest = self.root / asset_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / filename).write_bytes(data)
        return _uri(asset_id, filename)


class S3SegmentStore:
    def __init__(self, bucket: str, endpoint: str | None, key: str, secret: str, region: str):
        import boto3  # optional dependency, only imported when S3 is used

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name=region,
        )

    def put(self, asset_id: str, filename: str, data: bytes) -> str:
        self.client.put_object(
            Bucket=self.bucket,
            Key=f"segments/{asset_id}/{filename}",
            Body=data,
            ContentType="video/mp2t",
        )
        return _uri(asset_id, filename)


def get_store() -> SegmentStore:
    if config.STORAGE_BACKEND == "s3":
        return S3SegmentStore(
            bucket=config.S3_BUCKET,
            endpoint=config.S3_ENDPOINT,
            key=config.S3_ACCESS_KEY,
            secret=config.S3_SECRET_KEY,
            region=config.S3_REGION,
        )
    return LocalSegmentStore(config.SEGMENT_DIR)
