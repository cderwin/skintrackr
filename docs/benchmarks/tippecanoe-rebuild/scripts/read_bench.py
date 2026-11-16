"""Time reading N gzipped track features back from the local SeaweedFS, as the plan's `read_track_features` would.

Uploads features under a `bench/` prefix of the local bucket (never production), lists and reads them with a
thread pool, then deletes the prefix. Prints one JSON line per (N, concurrency).

Usage: read_bench.py DATA_DIR
"""

import gzip
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.config import Config

BUCKET, PREFIX = "skintrackr-storage", "bench/athletes/i0/activities/"
s3 = boto3.client(
    "s3",
    endpoint_url="http://localhost:8333",
    region_name="auto",
    aws_access_key_id="skintrackr-local",
    aws_secret_access_key="skintrackr-local-secret",
    config=Config(max_pool_connections=64, retries={"max_attempts": 8, "mode": "adaptive"}),
)


def keys() -> list[str]:
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=PREFIX)
    return [o["Key"] for p in pages for o in p.get("Contents", []) if o["Key"].endswith("track.geojson.gz")]


def main() -> None:
    lines = (Path(sys.argv[1]) / "tracks-5000.ndjson").read_text().splitlines()
    blobs = [gzip.compress(line.encode()) for line in lines]
    print(json.dumps({"gz_kb_mean": round(sum(map(len, blobs)) / len(blobs) / 1024, 1)}))
    try:
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda kv: s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}{kv[0]}/track.geojson.gz", Body=kv[1]), enumerate(blobs)))
        for n in (100, 1000, 5000):
            for workers in (8, 32):
                start = time.perf_counter()
                wanted = sorted(keys(), key=lambda k: int(k.split("/")[-2]))[:n]
                with ThreadPoolExecutor(workers) as pool:
                    data = list(pool.map(lambda k: gzip.decompress(s3.get_object(Bucket=BUCKET, Key=k)["Body"].read()), wanted))
                elapsed = time.perf_counter() - start
                print(json.dumps({"activities": n, "workers": workers, "wall_s": round(elapsed, 2), "mb": round(sum(map(len, data)) / 2**20, 1)}))
    finally:
        all_keys = keys()
        for i in range(0, len(all_keys), 1000):
            s3.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": k} for k in all_keys[i : i + 1000]], "Quiet": True})
        print(json.dumps({"cleaned_up": len(all_keys), "left": len(keys())}))


if __name__ == "__main__":
    main()
