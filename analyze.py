# -*- coding: utf-8 -*-
"""
S1 実測値の集計スクリプト。STATUS.md に書く数字をここで再現可能にする。
x402 Bazaar と MCP Registry のホスト名を突き合わせ、
「MCP登録サーバーのうち、x402決済アドレスを既に持っている（=Bazaarにも載っている）割合」を出す。
"""
import json
import os
import statistics
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
OUT_DIR = os.path.join(HERE, "out")


def hostname(url):
    try:
        return urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""


def main():
    with open(os.path.join(DATA_DIR, "x402_bazaar_resources.json")) as f:
        x402 = json.load(f)
    x402_items = x402["items"]
    x402_hosts = {hostname(it.get("resource", "")) for it in x402_items}
    x402_hosts.discard("")
    payto_all = set()
    for it in x402_items:
        for a in it.get("accepts", []):
            if a.get("payTo"):
                payto_all.add(a["payTo"])

    print("=== x402 Bazaar ===")
    print("total listings (resources):", len(x402_items), "(API-reported total:", x402.get("reported_total_at_fetch_start"), ")")
    print("unique seller hostnames:", len(x402_hosts))
    print("unique payTo addresses:", len(payto_all))
    print(f"avg listings per seller host: {len(x402_items)/len(x402_hosts):.2f}")

    mcp_path = os.path.join(DATA_DIR, "mcp_servers_latest.json")
    if not os.path.exists(mcp_path):
        print("\n(MCP registry data not yet collected — run collector.py mcp first)")
        return

    with open(mcp_path) as f:
        mcp = json.load(f)
    mcp_servers = mcp["servers"]
    mcp_hosts = set()
    host_to_server = {}
    for s in mcp_servers:
        srv = s.get("server", s)
        for remote in srv.get("remotes", []) or []:
            h = hostname(remote.get("url", ""))
            if h:
                mcp_hosts.add(h)
                host_to_server[h] = srv.get("name")

    overlap = mcp_hosts & x402_hosts
    print("\n=== MCP Registry ===")
    print("total records collected (all versions):", mcp.get("actual_records_collected_all_versions"))
    print("total servers (latest version only):", mcp.get("actual_servers_latest_version"))
    print("unique remote hostnames across MCP servers:", len(mcp_hosts))

    print("\n=== Overlap (MCP servers whose host also appears in x402 Bazaar) ===")
    print("overlap hosts:", len(overlap))
    if mcp_hosts:
        print(f"share of MCP hostnames also in x402 Bazaar: {len(overlap)/len(mcp_hosts)*100:.2f}%")
    if overlap:
        print("examples:", list(overlap)[:10])

    # 名前ベースの粗いテキスト一致（x402への言及）
    x402_mention = 0
    for s in mcp_servers:
        srv = s.get("server", s)
        text = f"{srv.get('name','')} {srv.get('description','')}".lower()
        if "x402" in text:
            x402_mention += 1
    print("\nMCP servers whose name/description mentions 'x402':", x402_mention,
          f"({x402_mention/len(mcp_servers)*100:.3f}% of {len(mcp_servers)})")

    summary = {
        "x402_bazaar_total_listings": len(x402_items),
        "x402_bazaar_unique_seller_hosts": len(x402_hosts),
        "x402_bazaar_unique_payto_addresses": len(payto_all),
        "mcp_total_records_all_versions": mcp.get("actual_records_collected_all_versions"),
        "mcp_total_servers_latest": mcp.get("actual_servers_latest_version"),
        "mcp_unique_remote_hosts": len(mcp_hosts),
        "overlap_hosts_mcp_and_x402": len(overlap),
        "overlap_pct_of_mcp_hosts": round(len(overlap) / len(mcp_hosts) * 100, 2) if mcp_hosts else None,
        "mcp_servers_mentioning_x402_in_text": x402_mention,
    }
    with open(os.path.join(OUT_DIR, "cross_ecosystem_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("\nsaved ->", os.path.join(OUT_DIR, "cross_ecosystem_summary.json"))


if __name__ == "__main__":
    main()
