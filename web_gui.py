# ============================================
# TESS — Flight Control System Web Server
# Run: python3 web_gui.py
# ============================================

import csv
import io

from flask import Flask, jsonify, render_template, request, Response

import fcs_api
from chat_listener import generate_conversation_reply


def create_app(get_next_planned=None):
    """Build the FCS Flask app. scanner.py passes get_next_planned for live scan text."""
    app = Flask(__name__, static_folder="static", template_folder="templates")

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def get_status():
        planned = get_next_planned() if get_next_planned else None
        return jsonify(fcs_api.build_status_payload(next_planned=planned))

    @app.route("/api/trades")
    def get_trades():
        limit = min(int(request.args.get("limit", 50)), 500)
        offset = max(int(request.args.get("offset", 0)), 0)
        status = request.args.get("status", "all")
        pair = request.args.get("pair", "all")
        search = request.args.get("q", "").strip()

        all_trades = fcs_api.load_all_trades()
        filtered = fcs_api.filter_trades(all_trades, status=status, pair=pair, search=search)
        filtered.reverse()
        page = filtered[offset : offset + limit]
        stats = fcs_api.trade_stats(all_trades)
        pairs = sorted({t["pair"] for t in all_trades if t["pair"]})

        return jsonify({
            "trades": page,
            "total": len(filtered),
            "offset": offset,
            "limit": limit,
            "stats": stats,
            "pairs": pairs,
        })

    @app.route("/api/trades/export")
    def export_trades():
        all_trades = fcs_api.load_all_trades()
        closed = [t for t in reversed(all_trades) if t["status"] == "closed"]

        output = io.StringIO()
        fields = ["time", "pair", "direction", "mode", "entry", "sl", "tp", "close", "pnl", "reason"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for t in closed:
            writer.writerow(t)

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=tess_trade_archive.csv"},
        )

    @app.route("/api/config")
    def get_config():
        cfg = fcs_api.load_dynamic_config()
        balance = fcs_api.load_balance()
        return jsonify({
            "config": cfg,
            "balance": balance,
            "risk_per_trade_pct": 2.0,
            "risk_per_trade_usd": round(min(max(balance * 0.02, 2.0), 6.0), 2),
            "daily_loss_limit": 12.0,
            "soft_loss_limit": 6.0,
            "reward_to_risk": 3.5,
            "max_open_positions": cfg.get("max_open_positions", 2),
        })

    @app.route("/api/system/files")
    def system_files():
        return jsonify({"files": fcs_api.list_system_files()})

    @app.route("/api/system/file/<path:filename>")
    def system_file(filename):
        data = fcs_api.read_system_file(filename)
        if not data:
            return jsonify({"error": "File not found or not allowed"}), 404
        return jsonify(data)

    @app.route("/api/system/logs")
    def system_logs():
        lines = int(request.args.get("lines", 100))
        log_lines = fcs_api.tail_thinking_log(min(lines, 500))
        feed = fcs_api.parse_scan_feed(log_lines)
        return jsonify({"lines": log_lines, "feed": feed})

    @app.route("/api/market/news")
    def market_news():
        limit = min(int(request.args.get("limit", 15)), 30)
        return jsonify({"news": fcs_api.fetch_market_news(limit)})

    @app.route("/api/market/prices")
    def market_prices():
        cfg = fcs_api.load_dynamic_config()
        pairs = cfg.get("active_pairs", list(fcs_api.COIN_IDS.keys()))
        prices = {}
        for pair in pairs:
            p = fcs_api.get_price(pair)
            if p:
                prices[pair] = round(p, 4)
        return jsonify({"prices": prices, "timestamp": fcs_api.datetime.now().strftime("%H:%M:%S")})

    @app.route("/api/voice_command", methods=["POST"])
    def voice_command():
        data = request.json or {}
        command = data.get("command", "")
        if not command:
            return jsonify({"reply": "I couldn't hear any command, Harshit."})

        print(f"[FCS Voice] Received: '{command}'")
        reply = generate_conversation_reply(command)
        return jsonify({"reply": reply})

    return app


app = create_app()


if __name__ == "__main__":
    print("[Tess FCS] Flight Control launching at http://localhost:5000")
    app.run(port=5000, debug=True, use_reloader=False)
