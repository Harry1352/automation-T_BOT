# ============================================
# TESS — Dynamic Web GUI Server
# Run command: python3 web_gui.py
# ============================================

from flask import Flask, render_template, jsonify, request
import json
import os
import csv
from chat_listener import generate_conversation_reply

app = Flask(__name__)

BALANCE_FILE = "balance.json"
POSITIONS_FILE = "open_positions.json"
LOG_FILE = "trades.csv"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def get_status():
    # 1. Load Balance
    balance = 180.0
    if os.path.exists(BALANCE_FILE):
        try:
            with open(BALANCE_FILE, "r") as f:
                balance = json.load(f).get("paper_balance", 180.0)
        except:
            pass

    # 2. Load Active Positions
    positions = {}
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r") as f:
                positions = json.load(f)
        except:
            pass

    # 3. Load Completed Trades History
    trades = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                trades = list(reader)[-20:] # Get last 20 trades
                trades.reverse() # Show newest first
        except:
            pass

    return jsonify({
        "balance": balance,
        "positions": positions,
        "trades": trades
    })

@app.route("/api/voice_command", methods=["POST"])
def voice_command():
    data = request.json
    command = data.get("command", "")
    if not command:
        return jsonify({"reply": "I couldn't hear any command, Harshit."})
    
    print(f"[Web GUI Voice] Received: '{command}'")
    
    # Process using Tess's conversational engine directly
    reply = generate_conversation_reply(command)
    return jsonify({"reply": reply})

if __name__ == "__main__":
    print("[Tess GUI] Dashboard launching on http://localhost:5000")
    app.run(port=5000, debug=True, use_reloader=False)