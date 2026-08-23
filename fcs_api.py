# Tess Flight Control System — Shared API Data Layer
import csv
import json
import os
import re
from datetime import datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DIARY_DIR = os.path.join(ROOT, ".tess_diary")  # FIX: scanner.py writes all live state here now

BALANCE_FILE = os.path.join(DIARY_DIR, "balance.json")
POSITIONS_FILE = os.path.join(DIARY_DIR, "open_positions.json")
LOG_FILE = os.path.join(DIARY_DIR, "trades.csv")
PERF_FILE = os.path.join(DIARY_DIR, "performance.json")
CONFIG_FILE = os.path.join(DIARY_DIR, "dynamic_config.json")
THINKING_LOG = os.path.join(DIARY_DIR, "tess_thinking.log")

COIN_IDS = {
    "ETH/USDT": "ethereum",
    "SOL/USDT": "solana",
    "BTC/USDT": "bitcoin",
    "XAUT/USDT": "pax-gold",
}
KRAKEN_IDS = {
    "ETH/USDT": "XETHZUSD",
    "SOL/USDT": "SOLUSD",
    "BTC/USDT": "XXBTZUSD",
    "XAUT/USDT": "PAXGUSD",
}

SYSTEM_FILES = [
    "scanner.py",
    "web_gui.py",
    "fcs_api.py",
    "chat_listener.py",
    "brain.py",
    "tess.py",
    "alerts.py",
    "backtest.py",
    "config.py",
    "dynamic_config.json",
]

DEFAULT_PAIR_RULES = {
    "ETH/USDT": {"min_confidence": 82, "max_capital_pct": 0.14, "max_leverage": 40},
    "SOL/USDT": {"min_confidence": 82, "max_capital_pct": 0.14, "max_leverage": 40},
    "BTC/USDT": {"min_confidence": 85, "max_capital_pct": 0.10, "max_leverage": 25},
    "XAUT/USDT": {"min_confidence": 85, "max_capital_pct": 0.10, "max_leverage": 20},
}

_price_cache = {}
_price_cache_ts = {}


def _safe_float(val, default=0.0):
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def load_balance(default=180.0):
    if not os.path.exists(BALANCE_FILE):
        return default
    try:
        with open(BALANCE_FILE, "r") as f:
            return float(json.load(f).get("paper_balance", default))
    except Exception:
        return default


def load_positions():
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def load_performance():
    if not os.path.exists(PERF_FILE):
        return {}
    try:
        with open(PERF_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def load_dynamic_config():
    cfg = {
        "active_pairs": ["ETH/USDT", "SOL/USDT", "BTC/USDT", "XAUT/USDT"],
        "pair_rules": DEFAULT_PAIR_RULES,
        "max_open_positions": 2,
        "weekly_target_usd": 2500,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                disk = json.load(f)
            cfg.update(disk)
        except Exception:
            pass
    if "pair_rules" not in cfg:
        cfg["pair_rules"] = DEFAULT_PAIR_RULES
    return cfg


def get_price(pair):
    now = datetime.now()
    cached_at = _price_cache_ts.get(pair)
    if cached_at and (now - cached_at).total_seconds() < 20 and pair in _price_cache:
        return _price_cache[pair]

    price = None
    coin_id = COIN_IDS.get(pair)
    if coin_id:
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd"},
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                if coin_id in data:
                    price = float(data[coin_id]["usd"])
        except Exception:
            pass

    if price is None:
        kraken_id = KRAKEN_IDS.get(pair)
        if kraken_id:
            try:
                r = requests.get(
                    f"https://api.kraken.com/0/public/Ticker?pair={kraken_id}",
                    timeout=8,
                )
                result = list(r.json()["result"].values())[0]
                price = float(result["c"][0])
            except Exception:
                pass

    if price is not None:
        _price_cache[pair] = price
        _price_cache_ts[pair] = now
    return price


def _format_duration(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    return f"{hours}h {mins}m"


def enrich_position(pair, pos):
    entry = _safe_float(pos.get("entry"))
    sl = _safe_float(pos.get("sl"))
    tp = _safe_float(pos.get("tp"))
    size = _safe_float(pos.get("size"))
    direction = pos.get("direction", "long")
    capital = _safe_float(pos.get("capital"))
    leverage = int(_safe_float(pos.get("leverage"), 1))

    mark = get_price(pair) or entry
    if direction == "long":
        unrealized = (mark - entry) * size
        sl_dist_pct = ((entry - sl) / entry * 100) if entry else 0
        tp_dist_pct = ((tp - entry) / entry * 100) if entry else 0
        to_sl_pct = ((mark - sl) / mark * 100) if mark else 0
        to_tp_pct = ((tp - mark) / mark * 100) if mark else 0
    else:
        unrealized = (entry - mark) * size
        sl_dist_pct = ((sl - entry) / entry * 100) if entry else 0
        tp_dist_pct = ((entry - tp) / entry * 100) if entry else 0
        to_sl_pct = ((sl - mark) / mark * 100) if mark else 0
        to_tp_pct = ((mark - tp) / mark * 100) if mark else 0

    risk_at_sl = abs(entry - sl) * size
    notional = size * mark
    rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0

    elapsed_sec = 0
    entry_time_raw = pos.get("entry_time")
    if entry_time_raw:
        try:
            entry_dt = datetime.fromisoformat(entry_time_raw)
            elapsed_sec = (datetime.now() - entry_dt).total_seconds()
        except Exception:
            pass

    return {
        **pos,
        "pair": pair,
        "mark_price": round(mark, 4),
        "unrealized_pnl": round(unrealized, 2),
        "risk_at_sl": round(risk_at_sl, 2),
        "notional_usd": round(notional, 2),
        "rr_ratio": round(rr, 2),
        "sl_dist_pct": round(sl_dist_pct, 3),
        "tp_dist_pct": round(tp_dist_pct, 3),
        "to_sl_pct": round(to_sl_pct, 3),
        "to_tp_pct": round(to_tp_pct, 3),
        "elapsed": _format_duration(elapsed_sec),
        "elapsed_sec": int(elapsed_sec),
        "capital": round(capital, 2),
        "leverage": leverage,
        "breakeven_set": bool(pos.get("breakeven_set")),
        "trailing_set": bool(pos.get("trailing_set")),
    }


def load_all_trades():
    if not os.path.exists(LOG_FILE):
        return []
    trades = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                entry = _safe_float(row.get("entry"))
                close = _safe_float(row.get("close"))
                sl = _safe_float(row.get("sl"))
                tp = _safe_float(row.get("tp"))
                pnl = _safe_float(row.get("pnl"))
                status = (row.get("status") or "").strip().lower()
                if not status:
                    status = "closed" if close and row.get("pnl") not in (None, "") else "open"

                risk = abs(entry - sl) if sl else 0
                r_multiple = round(pnl / (risk * _safe_float(row.get("size"), 1)), 2) if risk and pnl else None

                trades.append({
                    "time": row.get("time", ""),
                    "pair": row.get("pair", ""),
                    "direction": row.get("direction", ""),
                    "mode": row.get("mode", ""),
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "close": close,
                    "pnl": pnl,
                    "status": status,
                    "reason": (row.get("reason") or "").strip(),
                    "r_multiple": r_multiple,
                })
    except Exception:
        pass
    return trades


def filter_trades(trades, status=None, pair=None, search=None):
    out = trades
    if status and status != "all":
        out = [t for t in out if t["status"] == status]
    if pair and pair != "all":
        out = [t for t in out if t["pair"] == pair]
    if search:
        q = search.lower()
        out = [
            t for t in out
            if q in t["pair"].lower()
            or q in t["reason"].lower()
            or q in t["direction"].lower()
            or q in t.get("mode", "").lower()
        ]
    return out


def trade_stats(trades):
    closed = [t for t in trades if t["status"] == "closed"]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] < 0]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = sum(abs(t["pnl"]) for t in losses)
    return {
        "total": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(gross_profit - gross_loss, 2),
        "avg_win": round(gross_profit / len(wins), 2) if wins else 0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0,
        "best": round(max((t["pnl"] for t in closed), default=0), 2),
        "worst": round(min((t["pnl"] for t in closed), default=0), 2),
    }


def tail_thinking_log(lines=80):
    if not os.path.exists(THINKING_LOG):
        return []
    try:
        with open(THINKING_LOG, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
        return [ln.rstrip("\n") for ln in all_lines[-lines:]]
    except Exception:
        return []


def parse_scan_feed(log_lines):
    feed = []
    patterns = [
        (re.compile(r"AI Choice for (.+?): (LONG|SHORT|SKIP)", re.I), "decision"),
        (re.compile(r"Position Established: (LONG|SHORT) (.+?) at (\d+)x", re.I), "entry"),
        (re.compile(r"Closed (.+?): ([+-][\d.]+) USD\. Reason: (.+?)\.", re.I), "exit"),
        (re.compile(r"Executing Scan #(\d+)", re.I), "scan"),
        (re.compile(r"System config updated dynamically", re.I), "config"),
        (re.compile(r"Locked trade at breakeven for (.+)", re.I), "breakeven"),
        (re.compile(r"Day transition detected", re.I), "system"),
        (re.compile(r"Tess System Online", re.I), "system"),
    ]
    for line in reversed(log_lines):
        ts_match = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)", line)
        if not ts_match:
            continue
        ts, body = ts_match.groups()
        matched = False
        for pat, kind in patterns:
            m = pat.search(body)
            if m:
                feed.append({"time": ts, "type": kind, "text": body.strip(), "meta": m.groups()})
                matched = True
                break
        if not matched and ("Reasoning:" in body or "Target:" in body or "Skip" in body):
            feed.append({"time": ts, "type": "decision", "text": body.strip(), "meta": []})
        if len(feed) >= 50:
            break
    return feed


def _fetch_rss_news(limit=12):
    items = []
    try:
        r = requests.get(
            "https://cointelegraph.com/rss",
            timeout=10,
            headers={"User-Agent": "TessFCS/1.0"},
        )
        if r.status_code != 200:
            return items
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.content)
        for item in root.findall(".//item")[:limit]:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub = item.findtext("pubDate", "")[:22]
            if title:
                items.append({
                    "title": title,
                    "source": "CoinTelegraph",
                    "url": link,
                    "time": pub,
                    "categories": "Market",
                })
    except Exception:
        pass
    return items


def _news_from_thinking_log(limit=8):
    items = []
    for line in reversed(tail_thinking_log(200)):
        ts_match = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)", line)
        if not ts_match:
            continue
        ts, body = ts_match.groups()
        if "AI Choice for" in body or "Position Established" in body or "Closed " in body:
            items.append({
                "title": body[:140] + ("..." if len(body) > 140 else ""),
                "source": "Tess Engine",
                "url": "",
                "time": ts,
                "categories": "Live Feed",
            })
        if len(items) >= limit:
            break
    return items


def fetch_market_news(limit=12):
    items = []
    try:
        r = requests.get(
            "https://min-api.cryptocompare.com/data/v2/news/?lang=EN",
            timeout=8,
            headers={"User-Agent": "TessFCS/1.0"},
        )
        if r.status_code == 200:
            for article in r.json().get("Data", [])[:limit]:
                items.append({
                    "title": article.get("title", ""),
                    "source": article.get("source_info", {}).get("name", "CryptoCompare"),
                    "url": article.get("url", ""),
                    "time": datetime.fromtimestamp(article.get("published_on", 0)).strftime("%Y-%m-%d %H:%M"),
                    "categories": article.get("categories", ""),
                })
    except Exception:
        pass

    if len(items) < 3:
        items.extend(_fetch_rss_news(limit - len(items)))

    if len(items) < 3:
        items.extend(_news_from_thinking_log(limit - len(items)))

    return items[:limit]


def list_system_files():
    files = []
    for name in SYSTEM_FILES:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            files.append({
                "name": name,
                "size_kb": round(os.path.getsize(path) / 1024, 1),
                "modified": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M"),
            })
    return files


def read_system_file(name):
    if name not in SYSTEM_FILES:
        return None
    path = os.path.join(ROOT, name)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    return {
        "name": name,
        "content": content,
        "lines": content.count("\n") + 1,
    }


def build_status_payload(next_planned=None):
    balance = load_balance()
    raw_positions = load_positions()
    positions = {pair: enrich_position(pair, pos) for pair, pos in raw_positions.items()}
    all_trades = load_all_trades()
    stats = trade_stats(all_trades)
    perf = load_performance()
    config = load_dynamic_config()

    recent_closed = [t for t in reversed(all_trades) if t["status"] == "closed"][:20]

    prices = {}
    for pair in config.get("active_pairs", list(COIN_IDS.keys())):
        p = get_price(pair)
        if p:
            prices[pair] = round(p, 4)

    return {
        "balance": round(balance, 2),
        "balance_inr": round(balance * 85),
        "positions": positions,
        "trades": recent_closed,
        "total_profit_td": stats["gross_profit"],
        "total_loss_td": stats["gross_loss"],
        "net_pnl": stats["net_pnl"],
        "performance": perf,
        "config": {
            "active_pairs": config.get("active_pairs", []),
            "max_open_positions": config.get("max_open_positions", 2),
            "weekly_target_usd": config.get("weekly_target_usd"),
        },
        "stats": stats,
        "prices": prices,
        "next_planned": next_planned or "Scanning markets...",
        "open_count": len(positions),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
