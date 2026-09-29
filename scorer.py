# -*- coding: utf-8 -*-
"""
S1 採点器: x402 Bazaar 掲載サービスの「エージェントに選ばれやすさ」スコアを付ける。

根拠（research_notes/財布を持つエージェント収益化調査/02_discovery_and_selection.md、
reports/財布を持つエージェント収益化調査.md より）:
  - 最初に提示された候補が 60〜100% 選ばれ、速さは品質より 10〜30倍重い
    (Magentic Marketplace, arXiv:2510.25779)
  - 説明文の書き方だけで利用率が最大10倍変わる (arXiv:2605.23916)
  - 用途に合わせた説明文の書き直しで選択率が +3.7〜14.9pt (ACES, arXiv:2508.02630)
  - 価格弾力性はおよそ -1.6〜-2.2（安いほど選ばれる）(ACES, arXiv:2508.02630)
  - 「Overall Pick」等の推薦ラベルで基準10%の選択率が20〜43%に (ACES, arXiv:2508.02630)
  - x402 Bazaar 自体のランキングも「直近30日の呼び出し数・ユニーク支払者数・
    メタデータ充実度」の実績ベース (docs.cdp.coinbase.com/x402/bazaar)

2段構成:
  (a) 決定的な規則ベースの特徴量 → 合成スコア 0-100（全件に適用）
  (b) LLM による模擬選択（同一カテゴリの候補を並べて複数回・順序を入れ替えて選ばせる）
      OPENROUTER_API_KEY か ANTHROPIC_API_KEY が環境変数にあれば実行、無ければスキップ。
      キーの中身は一切読まず、存在確認のみ行う。

出力:
  ops/S1/out/scored_all.jsonl        全件のスコアと特徴量（1行1件）
  ops/S1/out/top50.md                上位50件の例
  ops/S1/out/bottom50.md             下位50件の例
  ops/S1/out/llm_simulated_selection.json  (b) の結果。キー無ければ空でスキップ理由を記録
  ops/S1/out/speed_sample.json       応答速度の実測サンプル（ホスト単位）
"""
import json
import math
import os
import random
import re
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
OUT_DIR = os.path.join(HERE, "out")
os.makedirs(OUT_DIR, exist_ok=True)

UA = "s1-selectability-research/0.1 (+read-only market sizing, GET only)"

# ---------------------------------------------------------------------------
# (a) 決定的な規則ベースの特徴量
# ---------------------------------------------------------------------------

STABLE_6DEC_HINTS = ("usd coin", "usdc", "global dollar", "usdt", "tether")


def _hostname(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _price_usd(item):
    """accepts[] の中から最安値を推定 USD 建てで返す（USDC 系=6桁小数を仮定）。
    非ステーブル資産は換算根拠がないため None を返し、価格スコアでは中立点を使う。
    """
    best = None
    stable = False
    for a in item.get("accepts", []):
        name = (a.get("extra", {}) or {}).get("name", "") or ""
        symbol = (a.get("currency") or "") + (a.get("asset") or "")
        is_stable = any(h in name.lower() for h in STABLE_6DEC_HINTS) or "usdc" in symbol.lower()
        try:
            amt = int(a.get("amount", a.get("maxAmountRequired", "0")))
        except (TypeError, ValueError):
            continue
        if is_stable:
            usd = amt / 1_000_000.0
            stable = True
        else:
            continue  # 換算根拠がない資産はスキップ（過大評価を避ける）
        if best is None or usd < best:
            best = usd
    return best, stable


def _freshness_days(item, now):
    lu = item.get("lastUpdated")
    if not lu:
        return None
    try:
        dt = datetime.fromisoformat(lu.replace("Z", "+00:00"))
        return (now - dt).total_seconds() / 86400.0
    except Exception:
        return None


def _desc_quality(desc):
    """説明文の質を 0-1 で粗く採点する。
    根拠なき厳密さは避け、観測可能な代理指標のみ使う:
      - 長さが極端でない（20〜600字の範囲を最良とする）
      - 数値・単位・価格条件など具体性を示す記号を含む
      - 箇条書き/区切り記号があり要点が構造化されている
    """
    if not desc:
        return 0.0
    length = len(desc)
    # 長さスコア: 20字未満や600字超は減点。80〜300字あたりを山にする。
    if length < 10:
        len_score = 0.05
    else:
        ideal = 180
        len_score = math.exp(-((length - ideal) ** 2) / (2 * 160 ** 2))
    specificity = 0.0
    if re.search(r"\d", desc):
        specificity += 0.4
    if re.search(r"[\$￥€£]|usd|usdc|per |秒|filter|via ", desc, re.IGNORECASE):
        specificity += 0.3
    if re.search(r"[,、/\-–—]| or |include|default", desc, re.IGNORECASE):
        specificity += 0.3
    specificity = min(specificity, 1.0)
    return round(0.6 * len_score + 0.4 * specificity, 4)


def rule_features(item, now, speed_by_host, price_percentiles):
    host = _hostname(item.get("resource", ""))
    desc = item.get("description") or ""
    quality = item.get("quality") or {}
    calls30 = quality.get("l30DaysTotalCalls") or 0
    payers30 = quality.get("l30DaysUniquePayers") or 0
    price_usd, price_is_stable = _price_usd(item)
    freshness_days = _freshness_days(item, now)
    bazaar_ext = (item.get("extensions") or {}).get("bazaar") or {}
    info = bazaar_ext.get("info") or {}
    output = info.get("output") or {}
    input_ = info.get("input") or {}
    has_example = bool(output.get("example"))
    has_schema = bool(bazaar_ext.get("schema"))
    n_query_params = len(input_.get("queryParams") or {})
    networks = {a.get("network") for a in item.get("accepts", [])}
    speed_ms = speed_by_host.get(host, {}).get("latency_ms")
    speed_ok = speed_by_host.get(host, {}).get("ok")

    return {
        "host": host,
        "resource": item.get("resource"),
        "desc_len": len(desc),
        "desc_quality": _desc_quality(desc),
        "calls_30d": calls30,
        "unique_payers_30d": payers30,
        "price_usd": price_usd,
        "price_is_stable_estimate": price_is_stable,
        "freshness_days": freshness_days,
        "has_example": has_example,
        "has_schema": has_schema,
        "n_query_params": n_query_params,
        "n_networks": len(networks),
        "speed_ms": speed_ms,
        "speed_ok": speed_ok,
    }


def _minmax_norm(value, lo, hi):
    if value is None:
        return 0.5  # 情報なしは中立点
    if hi <= lo:
        return 0.5
    v = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, v))


def composite_score(feat, price_percentiles, speed_percentiles):
    """0-100 の合成スコア。重みは調査で計測された効果量の相対的な大きさに
    ラフに合わせている（速さ・説明文 >> 価格 > 実績・鮮度 > メタデータ充実度）。
    これは検証済みの予測モデルではなく、根拠を明示した優先順位付けの一次近似。
    """
    # 説明文の質 (0-1)
    desc_score = feat["desc_quality"]

    # 実績 (log スケールで正規化)
    calls_log = math.log1p(feat["calls_30d"])
    payers_log = math.log1p(feat["unique_payers_30d"])
    traction_score = 0.6 * _minmax_norm(calls_log, 0, math.log1p(50000)) + 0.4 * _minmax_norm(
        payers_log, 0, math.log1p(20000)
    )

    # 価格競争力: 同一データセット内での価格の低さの百分位（安いほど高スコア）
    if feat["price_usd"] is not None and price_percentiles:
        pct = price_percentiles(feat["price_usd"])  # 0=最安 .. 1=最高
        price_score = 1.0 - pct
    else:
        price_score = 0.5

    # 速さ: 実測サンプルがあれば使う。速いほど高スコア（応答2秒以内を満点近辺に）
    if feat["speed_ms"] is not None and feat["speed_ok"]:
        speed_score = _minmax_norm(-feat["speed_ms"], -8000, -300)
    else:
        speed_score = 0.5

    # 鮮度: 30日以内なら満点、365日超で下限
    if feat["freshness_days"] is not None:
        freshness_score = _minmax_norm(-feat["freshness_days"], -365, -1)
    else:
        freshness_score = 0.5

    # メタデータ充実度
    meta_score = (
        0.4 * (1.0 if feat["has_example"] else 0.0)
        + 0.3 * (1.0 if feat["has_schema"] else 0.0)
        + 0.15 * min(feat["n_query_params"] / 3.0, 1.0)
        + 0.15 * min(feat["n_networks"] / 3.0, 1.0)
    )

    weights = {
        "speed": 0.25,
        "desc": 0.25,
        "price": 0.20,
        "traction": 0.15,
        "freshness": 0.05,
        "meta": 0.10,
    }
    raw = (
        weights["speed"] * speed_score
        + weights["desc"] * desc_score
        + weights["price"] * price_score
        + weights["traction"] * traction_score
        + weights["freshness"] * freshness_score
        + weights["meta"] * meta_score
    )
    return round(raw * 100, 2), {
        "speed_score": round(speed_score, 3),
        "desc_score": round(desc_score, 3),
        "price_score": round(price_score, 3),
        "traction_score": round(traction_score, 3),
        "freshness_score": round(freshness_score, 3),
        "meta_score": round(meta_score, 3),
        "weights": weights,
    }


def make_price_percentile_fn(all_prices):
    prices = sorted(p for p in all_prices if p is not None)
    n = len(prices)
    if n == 0:
        return None

    def pct(v):
        import bisect

        i = bisect.bisect_left(prices, v)
        return i / n

    return pct


# ---------------------------------------------------------------------------
# 応答速度の実測サンプリング（ホスト単位で1回。全19091件を叩かない）
# ---------------------------------------------------------------------------

def sample_speed(items, max_hosts=None, max_workers=16, timeout=6, seed=42):
    by_host = {}
    for it in items:
        h = _hostname(it.get("resource", ""))
        if h and h not in by_host:
            by_host[h] = it.get("resource")
    hosts = list(by_host.items())
    random.Random(seed).shuffle(hosts)
    if max_hosts:
        hosts = hosts[:max_hosts]

    result = {}

    def probe(host_url):
        host, url = host_url
        try:
            t0 = time.time()
            r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
            t1 = time.time()
            return host, {"latency_ms": round((t1 - t0) * 1000, 1), "status": r.status_code, "ok": True}
        except Exception as e:  # noqa: BLE001
            return host, {"latency_ms": None, "status": None, "ok": False, "error": str(e)[:120]}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(probe, hu) for hu in hosts]
        done = 0
        for fut in as_completed(futs):
            host, res = fut.result()
            result[host] = res
            done += 1
            if done % 200 == 0:
                print(f"  speed sample: {done}/{len(hosts)}", flush=True)

    return result


# ---------------------------------------------------------------------------
# (b) LLM による模擬選択（キーが無ければスキップ）
# ---------------------------------------------------------------------------

def _keys_present():
    return {
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


def _call_llm(provider, system, user):
    if provider == "openrouter":
        key = os.environ["OPENROUTER_API_KEY"]
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "anthropic/claude-3.5-haiku",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 300,
                "temperature": 0.7,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    elif provider == "anthropic":
        key = os.environ["ANTHROPIC_API_KEY"]
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-3-5-haiku-20241022",
                "max_tokens": 300,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    raise ValueError(provider)


def build_categories(scored_items, max_categories=5, group_size=5):
    """簡易カテゴリ化: description の先頭キーワードでグルーピングし、
    価格帯・実績が近い候補群からランダムに group_size 件を選ぶ。
    """
    buckets = defaultdict(list)
    for it in scored_items:
        desc = (it.get("description") or "").lower()
        key = "other"
        for kw in [
            "weather", "price", "balance", "search", "news", "translat", "chain",
            "wallet", "token", "image", "email", "sms", "pdf", "ocr", "summar",
        ]:
            if kw in desc:
                key = kw
                break
        buckets[key].append(it)

    cats = []
    rng = random.Random(7)
    for key, arr in buckets.items():
        if len(arr) < group_size:
            continue
        cats.append((key, rng.sample(arr, group_size)))
        if len(cats) >= max_categories:
            break
    return cats


def run_llm_simulation(scored_items, n_trials=3):
    keys = _keys_present()
    if not keys["openrouter"] and not keys["anthropic"]:
        return {
            "skipped": True,
            "reason": "OPENROUTER_API_KEY / ANTHROPIC_API_KEY のいずれも環境変数に無いためスキップ",
            "keys_checked": list(keys.keys()),
        }
    provider = "openrouter" if keys["openrouter"] else "anthropic"
    cats = build_categories(scored_items)
    results = []
    for cat_name, candidates in cats:
        for trial in range(n_trials):
            order = list(range(len(candidates)))
            random.Random(trial * 13 + 1).shuffle(order)
            shuffled = [candidates[i] for i in order]
            listing = "\n".join(
                f"[{chr(65+i)}] {c.get('description','')[:200]} "
                f"(price=${( _price_usd(c)[0] or 0):.4f}, host={_hostname(c.get('resource',''))})"
                for i, c in enumerate(shuffled)
            )
            system = (
                "あなたは自律エージェントで、タスク遂行のため有料APIツールを1つだけ選ぶ。"
                "候補のうち最も呼び出すべきものをラベル1文字で答え、理由を1文で述べよ。"
            )
            user = f"カテゴリ: {cat_name}\n候補:\n{listing}\n\nどれを呼び出す？"
            try:
                answer = _call_llm(provider, system, user)
            except Exception as e:  # noqa: BLE001
                answer = f"ERROR: {e}"
            results.append(
                {
                    "category": cat_name,
                    "trial": trial,
                    "order_labels": [chr(65 + i) for i in range(len(shuffled))],
                    "order_hosts": [_hostname(c.get("resource", "")) for c in shuffled],
                    "raw_answer": answer,
                }
            )
    return {"skipped": False, "provider": provider, "results": results}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    with open(os.path.join(DATA_DIR, "x402_bazaar_resources.json")) as f:
        d = json.load(f)
    items = d["items"]
    print(f"loaded {len(items)} x402 bazaar resources")

    speed_sample_path = os.path.join(OUT_DIR, "speed_sample.json")
    if os.path.exists(speed_sample_path) and "--reuse-speed" in sys.argv:
        with open(speed_sample_path) as f:
            speed_by_host = json.load(f)
        print(f"reusing speed sample: {len(speed_by_host)} hosts")
    else:
        max_hosts = None
        for a in sys.argv:
            if a.startswith("--max-hosts="):
                max_hosts = int(a.split("=", 1)[1])
        print("sampling response speed per unique host (GET only, one probe per host)...")
        speed_by_host = sample_speed(items, max_hosts=max_hosts)
        with open(speed_sample_path, "w") as f:
            json.dump(speed_by_host, f, ensure_ascii=False, indent=1)
        print(f"saved speed sample -> {speed_sample_path} ({len(speed_by_host)} hosts)")

    now = datetime.now(timezone.utc)
    all_prices = []
    feats = []
    for it in items:
        feat = rule_features(it, now, speed_by_host, None)
        feats.append(feat)
        if feat["price_usd"] is not None:
            all_prices.append(feat["price_usd"])
    price_pct_fn = make_price_percentile_fn(all_prices)

    scored = []
    for it, feat in zip(items, feats):
        score, breakdown = composite_score(feat, price_pct_fn, None)
        row = {
            "resource": it.get("resource"),
            "host": feat["host"],
            "description": it.get("description"),
            "score": score,
            "features": feat,
            "score_breakdown": breakdown,
        }
        scored.append(row)

    scored.sort(key=lambda r: r["score"], reverse=True)

    scored_path = os.path.join(OUT_DIR, "scored_all.jsonl")
    with open(scored_path, "w") as f:
        for row in scored:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"saved {len(scored)} scored rows -> {scored_path}")

    def render_examples(rows, title):
        lines = [f"# {title}", ""]
        for i, r in enumerate(rows, 1):
            lines.append(
                f"{i}. **{r['score']}点** `{r['host']}` — {(r['description'] or '')[:140]}"
            )
            bd = r["score_breakdown"]
            lines.append(
                f"   - speed={bd['speed_score']} desc={bd['desc_score']} "
                f"price={bd['price_score']} traction={bd['traction_score']} "
                f"freshness={bd['freshness_score']} meta={bd['meta_score']}"
            )
        return "\n".join(lines)

    top50 = scored[:50]
    bottom50 = scored[-50:]
    with open(os.path.join(OUT_DIR, "top50.md"), "w") as f:
        f.write(render_examples(top50, "上位50件（選ばれやすさスコア）"))
    with open(os.path.join(OUT_DIR, "bottom50.md"), "w") as f:
        f.write(render_examples(bottom50, "下位50件（選ばれやすさスコア）"))
    print("saved top50.md / bottom50.md")

    # 統計サマリ
    scores_only = [r["score"] for r in scored]
    summary = {
        "n": len(scored),
        "mean": round(statistics.mean(scores_only), 2),
        "median": round(statistics.median(scores_only), 2),
        "stdev": round(statistics.pstdev(scores_only), 2),
        "min": min(scores_only),
        "max": max(scores_only),
        "unique_hosts": len({r["host"] for r in scored}),
    }
    with open(os.path.join(OUT_DIR, "score_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("score summary:", summary)

    # (b) LLM 模擬選択
    print("running (b) LLM simulated selection (skips if no API key present)...")
    llm_out = run_llm_simulation(scored)
    with open(os.path.join(OUT_DIR, "llm_simulated_selection.json"), "w") as f:
        json.dump(llm_out, f, ensure_ascii=False, indent=2)
    print("llm simulation:", "skipped" if llm_out.get("skipped") else f"{len(llm_out['results'])} trials")


if __name__ == "__main__":
    main()
