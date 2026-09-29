# -*- coding: utf-8 -*-
"""
ops/S1/out/scored_all.jsonl, ops/S1/out/cross_ecosystem_summary.json, ops/S1/STATUS.md の
実測値を使って index_template.html の __SELECTARANK_DATA__ を埋め、site/index.html を生成する。
あわせて badge/out/*.json を site/badges/ にミラーする（バッジJSONを相対パスで配置するため）。
デプロイはしない（ローカルファイル生成のみ）。英語版サイト（対象読者: x402/MCPの売り手＝世界中の開発者）。
"""
import json
import os
import re
import shutil
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "..", "out")
DATA_DIR = os.path.join(HERE, "..", "data")
BADGE_OUT_DIR = os.path.join(HERE, "..", "badge", "out")
SITE_BADGES_DIR = os.path.join(HERE, "badges")

TOP_N = 30
BOTTOM_N = 15


def slug(host):
    """badge/generate_badges.py の slug() と同一ルール（1ホスト=1バッジのファイル名）。"""
    return re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")


def load_json(*parts):
    with open(os.path.join(*parts)) as f:
        return json.load(f)


def fmt_date_utc(iso_str):
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d")


def mirror_badges():
    """badge/out/*.json を site/badges/ に丸ごとコピーする（相対パス badges/<slug>.json で参照するため）。
    サイト側に古いバッジが残らないよう、対象ディレクトリを作り直す。
    """
    if os.path.isdir(SITE_BADGES_DIR):
        shutil.rmtree(SITE_BADGES_DIR)
    os.makedirs(SITE_BADGES_DIR, exist_ok=True)
    n = 0
    for name in os.listdir(BADGE_OUT_DIR):
        if not name.endswith(".json"):
            continue
        shutil.copyfile(os.path.join(BADGE_OUT_DIR, name), os.path.join(SITE_BADGES_DIR, name))
        n += 1
    return n


def build_stats(x402_meta, mcp_meta, cross):
    x402_total = cross["x402_bazaar_total_listings"]
    unique_hosts = cross["x402_bazaar_unique_seller_hosts"]
    mcp_total = cross["mcp_total_servers_latest"]
    # x402-compatibility among MCP servers: hostname-match is the conservative bound,
    # "mentions x402 in text" is the looser upper bound (see STATUS.md).
    pct_low = cross["overlap_pct_of_mcp_hosts"]
    pct_high = round(cross["mcp_servers_mentioning_x402_in_text"] / mcp_total * 100, 2)

    stats = [
        {"n": f"{x402_total:,}", "label": "x402 Bazaar listings"},
        {"n": f"{unique_hosts:,}", "label": "Unique seller hosts"},
        {"n": f"{mcp_total:,}", "label": "MCP Registry servers"},
        {"n": f"{pct_low:g}–{pct_high:g}%", "label": "MCP servers already x402-ready"},
    ]
    return stats, pct_low, pct_high


def build_facts(x402_meta, mcp_meta, cross, speed_median_ms, x402_docs, mcp_docs):
    x402_total = cross["x402_bazaar_total_listings"]
    unique_hosts = cross["x402_bazaar_unique_seller_hosts"]
    payto = cross["x402_bazaar_unique_payto_addresses"]
    avg_listings = round(x402_total / unique_hosts, 1)
    mcp_total = cross["mcp_total_servers_latest"]
    mcp_all_versions = cross["mcp_total_records_all_versions"]
    overlap_hosts = cross["overlap_hosts_mcp_and_x402"]
    mentions = cross["mcp_servers_mentioning_x402_in_text"]

    facts = [
        (f"<b>x402 Bazaar:</b> {x402_total:,} listings across {unique_hosts:,} unique seller hosts "
         f"(avg {avg_listings} listings/host &mdash; concentrated among a small set of hosts) and "
         f"{payto:,} unique payTo addresses. Source: "
         f"<a href='{x402_docs}' target='_blank' rel='noopener'>{x402_docs}</a>."),
        (f"<b>MCP Registry:</b> {mcp_total:,} servers on their latest version "
         f"({mcp_all_versions:,} records across all versions). Source: "
         f"<a href='{mcp_docs}' target='_blank' rel='noopener'>{mcp_docs}</a>."),
        (f"<b>x402 readiness inside MCP:</b> only {overlap_hosts:,} MCP servers "
         f"({cross['overlap_pct_of_mcp_hosts']:g}% by hostname match against x402 Bazaar) already accept "
         f"x402 payments; {mentions:,} more ({round(mentions / mcp_total * 100, 2):g}%) mention "
         f"&ldquo;x402&rdquo; in their text without a confirmed match. Read the other way: 96%+ of the MCP "
         f"ecosystem has not wired up x402 payments yet."),
        (f"<b>Response time:</b> median {speed_median_ms:,}ms across a live sample of x402 Bazaar hosts "
         f"(GET request, expecting HTTP 402). See <code>ops/S1/out/speed_sample.json</code>."),
    ]
    return facts


def main():
    x402_meta = load_json(DATA_DIR, "x402_bazaar_resources.json")
    mcp_meta = load_json(DATA_DIR, "mcp_servers_latest.json")
    cross = load_json(OUT_DIR, "cross_ecosystem_summary.json")

    # 応答速度中央値: STATUS.md に記載・承認済みの実測値（out/speed_sample.json のサンプリング結果）。
    speed_median_ms = 1118

    collected_dates = [fmt_date_utc(x402_meta["fetched_at_utc"]), fmt_date_utc(mcp_meta["fetched_at_utc"])]
    collected_on = max(collected_dates)

    stats, pct_low, pct_high = build_stats(x402_meta, mcp_meta, cross)
    facts = build_facts(
        x402_meta, mcp_meta, cross, speed_median_ms,
        x402_meta["docs_url"], mcp_meta["docs_url"],
    )

    rows = []
    with open(os.path.join(OUT_DIR, "scored_all.jsonl")) as f:
        for line in f:
            rows.append(json.loads(line))
    rows.sort(key=lambda r: r["score"], reverse=True)

    display_rows = rows[:TOP_N] + rows[-BOTTOM_N:]
    compact_rows = [
        {
            "host": r["host"],
            "slug": slug(r["host"]),
            "score": r["score"],
            "description": (r.get("description") or "")[:150],
        }
        for r in display_rows
    ]

    data = {
        "stats": stats,
        "facts": facts,
        "rows": compact_rows,
        "meta": {
            "collected_on": collected_on,
            "top_n": TOP_N,
            "bottom_n": BOTTOM_N,
            "scored_listings": len(rows),
        },
    }

    with open(os.path.join(HERE, "index_template.html"), encoding="utf-8") as f:
        template = f.read()

    html = template.replace("__SELECTARANK_DATA__", json.dumps(data, ensure_ascii=False))
    out_path = os.path.join(HERE, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    n_badges = mirror_badges()

    print(f"built site -> {out_path} ({len(compact_rows)} rows embedded, {len(rows)} scored listings total)")
    print(f"mirrored {n_badges} badge JSON files -> {SITE_BADGES_DIR}")


if __name__ == "__main__":
    main()
