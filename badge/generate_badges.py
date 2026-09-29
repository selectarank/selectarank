# -*- coding: utf-8 -*-
"""
ops/S1/out/scored_all.jsonl からホスト単位の最高スコアを集計し、
shields.io の endpoint バッジ形式 JSON を ops/S1/badge/out/ にローカル生成する。
公開ホスティングは行わない（デプロイ未実施。SPEC.md 参照）。
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSONL = os.path.join(HERE, "..", "out", "scored_all.jsonl")
BADGE_OUT = os.path.join(HERE, "out")
os.makedirs(BADGE_OUT, exist_ok=True)


def color_for(score):
    if score >= 80:
        return "brightgreen"
    if score >= 65:
        return "green"
    if score >= 50:
        return "yellow"
    if score >= 35:
        return "orange"
    return "red"


def slug(host):
    return re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")


def main():
    best = {}
    with open(OUT_JSONL) as f:
        for line in f:
            row = json.loads(line)
            host = row["host"]
            if host not in best or row["score"] > best[host]["score"]:
                best[host] = row

    for host, row in best.items():
        s = slug(host)
        badge = {
            "schemaVersion": 1,
            "label": "SelectaRank",
            "message": f"{row['score']:.0f}/100",
            "color": color_for(row["score"]),
        }
        with open(os.path.join(BADGE_OUT, f"{s}.json"), "w") as f:
            json.dump(badge, f, ensure_ascii=False)

    print(f"generated {len(best)} badge JSON files -> {BADGE_OUT}")
    # サンプル表示
    for host, row in list(sorted(best.items(), key=lambda kv: -kv[1]["score"]))[:3]:
        print(f"  sample: {host} -> {row['score']}")


if __name__ == "__main__":
    main()
