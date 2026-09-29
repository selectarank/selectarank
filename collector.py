"""
S1 収集器: x402 Bazaar (CDP facilitator discovery API) と MCP Registry の
公開APIから全件を GET して ops/S1/data/ に保存する。

すべて公開・認証不要の GET エンドポイント。書き込み系操作は一切行わない。
実行:
    source .venv/bin/activate
    python collector.py x402      # x402 Bazaar のみ
    python collector.py mcp       # MCP Registry のみ
    python collector.py both      # 両方（デフォルト）
    python collector.py mcp --resume   # 中断した収集を cursor から再開
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
os.makedirs(RAW_DIR, exist_ok=True)

X402_BASE = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"
MCP_BASE = "https://registry.modelcontextprotocol.io/v0/servers"

UA = "s1-selectability-research/0.1 (+read-only market sizing, GET only)"


def _get(url, params, timeout=30, max_retries=5):
    last_exc = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": UA})
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last_exc = e
            wait = min(2 ** attempt, 20)
            print(f"  [retry {attempt+1}/{max_retries}] {e} -> sleep {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise last_exc


def collect_x402(limit=1000, sleep_s=0.2):
    """x402 Bazaar discovery API (CDP facilitator) を全件ページングして取得する。
    出典: https://docs.cdp.coinbase.com/x402/bazaar
    実エンドポイント: GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources
    認証: 不要（公開）。フィルタなしで全件を limit/offset でページング。
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    items = []
    offset = 0
    first = _get(X402_BASE, {"limit": limit, "offset": 0})
    total = first["pagination"]["total"]
    items.extend(first["items"])
    print(f"x402 bazaar total (reported by API at offset=0): {total}")
    page_no = 0
    with open(os.path.join(RAW_DIR, f"x402_page_{page_no:04d}.json"), "w") as f:
        json.dump(first, f, ensure_ascii=False)
    offset = limit
    page_no += 1
    while offset < total:
        time.sleep(sleep_s)
        page = _get(X402_BASE, {"limit": limit, "offset": offset})
        got = page.get("items", [])
        if not got:
            print(f"  empty page at offset={offset}, stopping early")
            break
        items.extend(got)
        with open(os.path.join(RAW_DIR, f"x402_page_{page_no:04d}.json"), "w") as f:
            json.dump(page, f, ensure_ascii=False)
        print(f"  offset={offset} +{len(got)} items (cum={len(items)}/{total})")
        offset += limit
        page_no += 1

    out = {
        "source_url": X402_BASE,
        "docs_url": "https://docs.cdp.coinbase.com/x402/bazaar",
        "fetched_at_utc": fetched_at,
        "reported_total_at_fetch_start": total,
        "actual_items_collected": len(items),
        "items": items,
    }
    out_path = os.path.join(DATA_DIR, "x402_bazaar_resources.json")
    with open(out_path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"saved {len(items)} x402 bazaar resources -> {out_path}")
    return out


def collect_mcp(limit=100, sleep_s=0.1, resume=False, checkpoint_every=20):
    """MCP Registry (registry.modelcontextprotocol.io) を全件カーソルページングで取得する。
    出典: https://registry.modelcontextprotocol.io/openapi.yaml
    実エンドポイント: GET https://registry.modelcontextprotocol.io/v0/servers
    認証: 不要（公開）。

    注: `version=latest` フィルタはサーバー側で重く、カーソルが進むほど応答が
    10秒超に劣化することを実測で確認したため使わない。フィルタなしで全バージョン
    （＝サーバーのリビジョン履歴を含む全件）を取得し、各レコードの
    `_meta["io.modelcontextprotocol.registry/official"]["isLatest"]` で
    クライアント側フィルタする。
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    checkpoint_path = os.path.join(RAW_DIR, "mcp_checkpoint.json")
    all_records = []
    cursor = None
    page_no = 0

    if resume and os.path.exists(checkpoint_path):
        with open(checkpoint_path) as f:
            ck = json.load(f)
        cursor = ck.get("cursor")
        page_no = ck.get("page_no", 0)
        for i in range(page_no):
            p = os.path.join(RAW_DIR, f"mcp_page_{i:05d}.json")
            if os.path.exists(p):
                with open(p) as f:
                    all_records.extend(json.load(f).get("servers", []))
        print(f"resume: page_no={page_no}, cursor={cursor}, records so far={len(all_records)}")

    while True:
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        page = _get(MCP_BASE, params, timeout=30)
        got = page.get("servers", [])
        all_records.extend(got)
        with open(os.path.join(RAW_DIR, f"mcp_page_{page_no:05d}.json"), "w") as f:
            json.dump(page, f, ensure_ascii=False)
        cursor = page.get("metadata", {}).get("nextCursor")
        page_no += 1
        if page_no % checkpoint_every == 0 or not cursor:
            with open(checkpoint_path, "w") as f:
                json.dump({"cursor": cursor, "page_no": page_no}, f)
            print(f"  page={page_no} +{len(got)} (cum={len(all_records)}) cursor={cursor}", flush=True)
        if not cursor or not got:
            break
        time.sleep(sleep_s)

    latest_only = [
        r for r in all_records
        if r.get("_meta", {})
            .get("io.modelcontextprotocol.registry/official", {})
            .get("isLatest") is True
    ]

    out = {
        "source_url": MCP_BASE,
        "docs_url": "https://registry.modelcontextprotocol.io/openapi.yaml",
        "fetched_at_utc": fetched_at,
        "actual_records_collected_all_versions": len(all_records),
        "actual_servers_latest_version": len(latest_only),
        "servers": latest_only,
    }
    out_path = os.path.join(DATA_DIR, "mcp_servers_latest.json")
    with open(out_path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"saved {len(all_records)} total records / {len(latest_only)} latest-version servers -> {out_path}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["x402", "mcp", "both"], nargs="?", default="both")
    ap.add_argument("--resume", action="store_true", help="mcp collector: cursor から再開")
    args = ap.parse_args()

    if args.mode in ("x402", "both"):
        collect_x402()
    if args.mode in ("mcp", "both"):
        collect_mcp(resume=args.resume)
