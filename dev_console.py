# ==============================================================================
# TESS — DEV CONSOLE (v1.1 — Gemini backend)
# In-dashboard AI coding assistant. Lets you chat with Gemini 2.5 (Google AI
# Studio) about your own bot's source code directly from the web UI, and
# apply proposed edits to disk without leaving localhost:5000.
#
# SAFETY DESIGN:
# - Only files in FILE_WHITELIST can ever be read or written.
# - The AI never writes to disk directly. It only PROPOSES a full new file.
# - Nothing is written until you click "Apply" in the UI (explicit human step).
# - Every apply creates a timestamped backup before overwriting.
# - This module does NOT restart the bot for you. You restart manually after
#   an apply, so you always see what changed before it goes live.
# ==============================================================================

import os
import json
import difflib
from datetime import datetime

from flask import request, jsonify

# ── Config ────────────────────────────────────────────────────────────────
PROJECT_DIR   = os.path.dirname(os.path.abspath(__file__))
DIARY_DIR     = os.path.join(PROJECT_DIR, ".tess_diary")
BACKUP_DIR    = os.path.join(DIARY_DIR, "dev_console_backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

# Only these files can be viewed or edited through the Dev Console.
# Add filenames here as your project grows. Never whitelist keys.py, .env,
# or anything holding secrets.
FILE_WHITELIST = [
    "scanner.py",
    "strategies.py",       # FIX: this is the actual decision logic -- was missing
    "liquidity_engine.py", # FIX: the real safety-gate file -- was missing
    "config.py",
    "alerts.py",
    "chat_listener.py",
    "fcs_api.py",
    # "brain.py" and "web_gui.py" removed -- legacy/dead files, not part of
    # the live pipeline. Re-add only if you confirm scanner.py actually
    # imports them.
]

MAX_FILE_CHARS_FOR_PROMPT = 40000  # guard against blowing the context window


def _safe_path(filename):
    """Resolve a whitelisted filename to an absolute path, refusing anything else."""
    if filename not in FILE_WHITELIST:
        return None
    path = os.path.join(PROJECT_DIR, filename)
    if not os.path.abspath(path).startswith(PROJECT_DIR):
        return None
    return path


def _read_file(filename):
    path = _safe_path(filename)
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_file_with_backup(filename, new_content):
    path = _safe_path(filename)
    if not path:
        raise ValueError("File not in whitelist.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"{filename}.{timestamp}.bak")

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(existing)

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return backup_path


GEMINI_MODEL = "gemini-2.5-pro"  # swap to "gemini-2.5-flash" for faster/cheaper replies
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def _ask_dev_ai(user_message, filename, file_content, conversation_history):
    """
    Calls Gemini 2.5 (Google AI Studio) with the selected file as context.
    Returns a dict: {"mode": "chat"|"edit", "reply": str, "new_content": str|None}
    """
    from config import GOOGLE_STUDIO_API_KEY

    file_block = ""
    if file_content:
        trimmed = file_content[:MAX_FILE_CHARS_FOR_PROMPT]
        file_block = f"""
CURRENT CONTENTS OF {filename}:
```
{trimmed}
```
"""

    system_prompt = f"""You are Tess Dev Console — an embedded coding assistant living inside \
Harshit's own trading bot dashboard. You help him understand and modify his bot's source code \
directly, without him needing to open an external AI chat page.

RULES:
1. Keep replies SHORT and direct. No filler, no repeating the question, no long preambles.
2. If Harshit is just asking a question or wants an explanation, respond in "chat" mode — no code rewrite needed.
3. If Harshit explicitly asks you to change, fix, add, or upgrade something in the file shown below, \
respond in "edit" mode: return the COMPLETE new file content, preserving everything that wasn't \
supposed to change. Never return a partial snippet or diff as new_content — always the full file, \
or it will corrupt the file when applied.
4. Never invent file contents you weren't shown. If you need a file that isn't in context, say so \
in "chat" mode and ask Harshit to select it from the file dropdown.
5. Never touch risk-management hard caps (leverage limits, position sizing limits, daily loss limits) \
unless Harshit explicitly and unambiguously asks you to change that specific number.

{file_block}

Respond ONLY with raw JSON, nothing else, no markdown fences:
{{
  "mode": "chat" or "edit",
  "reply": "your short response to Harshit, explaining what you did or answering his question",
  "new_content": "the complete new file content if mode is edit, otherwise null"
}}"""

    # Gemini's chat format: system_instruction is separate, contents alternate
    # role "user" / "model" (not "assistant").
    contents = []
    for h in conversation_history[-6:]:
        role = "model" if h["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    import requests
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
        },
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": GOOGLE_STUDIO_API_KEY}

    try:
        r = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        return {
            "mode": parsed.get("mode", "chat"),
            "reply": parsed.get("reply", ""),
            "new_content": parsed.get("new_content"),
        }
    except Exception as e:
        return {
            "mode": "chat",
            "reply": f"Dev Console AI (Gemini) unavailable: {e}",
            "new_content": None,
        }


# ── Route registration ──────────────────────────────────────────────────
DEV_CHAT_HISTORY_FILE = os.path.join(DIARY_DIR, "dev_console_chat_history.json")


def _load_dev_chat_history():
    if not os.path.exists(DEV_CHAT_HISTORY_FILE):
        return []
    try:
        with open(DEV_CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_dev_chat_history(history):
    try:
        with open(DEV_CHAT_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history[-100:], f, indent=2)  # keep last 100 messages
    except Exception as e:
        print(f"[Dev Console] Failed to save chat history: {e}")


_dev_chat_history = _load_dev_chat_history()


def register_dev_console(app):
    """Call this once from scanner.py: register_dev_console(app)"""

    @app.route("/api/devconsole/files")
    def dc_list_files():
        return jsonify({"files": FILE_WHITELIST})

    @app.route("/api/devconsole/history")
    def dc_get_history():
        return jsonify({"history": _dev_chat_history})

    @app.route("/api/devconsole/file/<path:filename>")
    def dc_get_file(filename):
        content = _read_file(filename)
        if content is None:
            return jsonify({"error": "File not found or not whitelisted."}), 404
        return jsonify({"filename": filename, "content": content})

    @app.route("/api/devconsole/chat", methods=["POST"])
    def dc_chat():
        data = request.json or {}
        message = data.get("message", "").strip()
        filename = data.get("filename")

        if not message:
            return jsonify({"error": "Empty message."}), 400

        file_content = _read_file(filename) if filename else None
        result = _ask_dev_ai(message, filename, file_content, _dev_chat_history)

        _dev_chat_history.append({"role": "user", "content": message, "filename": filename})
        _dev_chat_history.append({"role": "assistant", "content": result["reply"]})
        _save_dev_chat_history(_dev_chat_history)

        response = {
            "mode": result["mode"],
            "reply": result["reply"],
            "filename": filename,
        }

        if result["mode"] == "edit" and result.get("new_content"):
            old_content = file_content or ""
            diff = list(difflib.unified_diff(
                old_content.splitlines(keepends=True),
                result["new_content"].splitlines(keepends=True),
                fromfile=f"{filename} (current)",
                tofile=f"{filename} (proposed)",
            ))
            response["proposed_content"] = result["new_content"]
            response["diff"] = "".join(diff)

        return jsonify(response)

    @app.route("/api/devconsole/apply", methods=["POST"])
    def dc_apply():
        data = request.json or {}
        filename = data.get("filename")
        new_content = data.get("new_content")

        if not filename or new_content is None:
            return jsonify({"error": "Missing filename or new_content."}), 400
        if filename not in FILE_WHITELIST:
            return jsonify({"error": "File not whitelisted."}), 403

        try:
            backup_path = _write_file_with_backup(filename, new_content)
            return jsonify({
                "success": True,
                "filename": filename,
                "backup": os.path.basename(backup_path),
                "message": f"{filename} updated. Backup saved. Restart the bot in your terminal to apply changes."
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/devconsole/reset_chat", methods=["POST"])
    def dc_reset_chat():
        _dev_chat_history.clear()
        _save_dev_chat_history(_dev_chat_history)
        return jsonify({"success": True})