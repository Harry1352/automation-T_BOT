# TESS — Setup & Run Guide (T_BOT /THORFINN SPL ACCSESS ONLY)

This walks a first-time user through everything needed to get Tess running locally, on macOS or Windows, from a clean machine.

---

## 1.0 What You Need Before Starting

- **Python 3.10 or newer** — Tess is built and tested against Python 3.10.
- **Git** — to clone the repository (or you can download it as a ZIP instead).
- **API keys** for the services Tess talks to (all free to obtain, some free-tier, some paid — details in Step 5):
  - NVIDIA NIM (Nemotron) key
  - Groq API key
  - Google AI Studio (Gemini) key
  - A Gmail account + app password (for email alerts/ledger)
  - Delta Exchange India API key + secret (only needed for future live trading — paper mode runs fine without real funds behind these)

No prior Python experience is assumed below — every command is copy-pasteable.

---

## 2.0 Step 1 — Get the Code

**Option A — Git clone:**
```
git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME
```

**Option B — Download ZIP:**
On the GitHub repo page, click **Code → Download ZIP**, extract it, then open a terminal (Mac) or Command Prompt/PowerShell (Windows) and `cd` into the extracted folder.

---

## 3.0 Step 2 — Install Python (if you don't already have it)

**macOS:**
1. Check if you already have it: open Terminal and run `python3 --version`.
2. If missing or older than 3.10, install from https://www.python.org/downloads/macos/ (or via Homebrew: `brew install python@3.10`).

**Windows:**
1. Check if you already have it: open Command Prompt and run `python --version`.
2. If missing, download the installer from https://www.python.org/downloads/windows/.
3. **Important:** during install, check the box **"Add Python to PATH"** before clicking Install — this is the single most common Windows setup mistake.

---

## 4.0 Step 3 — Create a Virtual Environment

A virtual environment keeps Tess's dependencies separate from anything else on your machine.

**macOS / Linux (Terminal):**
```
python3 -m venv tess_env
source tess_env/bin/activate
```

**Windows (Command Prompt):**
```
python -m venv tess_env
tess_env\Scripts\activate
```

**Windows (PowerShell):**
```
python -m venv tess_env
tess_env\Scripts\Activate.ps1
```
*(If PowerShell blocks the script with an execution-policy error, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then retry.)*

You'll know it worked if your terminal prompt now starts with `(tess_env)`.

---

## 5.0 Step 4 — Install Dependencies

With the virtual environment active:
```
pip install -r requirements.txt
```

This installs: `ccxt`, `pandas`, `numpy`, `flask`, `requests`, `python-dotenv`, `openai`.

---

## 6.0 Step 5 — Set Up Your `.env` File

Create a file named exactly `.env` in the project root (same folder as `scanner.py`). This file holds every secret and is **never** committed to GitHub — make sure `.env` is listed in your `.gitignore`.

```env
DELTA_API_KEY=your_delta_exchange_api_key
DELTA_API_SECRET=your_delta_exchange_secret
NVIDIA_NEMOTRON_KEY=your_nvidia_nim_key
GROQ_API_KEY_2=your_groq_key
GOOGLE_STUDIO_API_KEY=your_google_ai_studio_key
TESS_GMAIL=your_bot_gmail_address
TESS_GMAIL_APP_PASSWORD=your_gmail_app_password
YOUR_EMAIL=your_personal_email
```

**Where to get each key:**

| Key | Where |
|---|---|
| `NVIDIA_NEMOTRON_KEY` | https://build.nvidia.com — sign in, find Nemotron, generate an API key |
| `GROQ_API_KEY_2` | https://console.groq.com/keys |
| `GOOGLE_STUDIO_API_KEY` | https://aistudio.google.com/apikey |
| `TESS_GMAIL_APP_PASSWORD` | https://myaccount.google.com/apppasswords — requires 2-Step Verification enabled on that Gmail account first |
| `DELTA_API_KEY` / `DELTA_API_SECRET` | Delta Exchange India account → API Management (only required once live trading is enabled — safe to leave blank for paper mode) |

---

## 7.0 Step 6 — Run Tess

**macOS (recommended — prevents your Mac from sleeping mid-scan):**
```
caffeinate python3 scanner.py
```

**macOS (without caffeinate):**
```
python3 scanner.py
```
*(Go to System Settings → Battery/Energy → and disable sleep while plugged in, or the bot will pause when your Mac sleeps.)*

**Windows:**
```
python scanner.py
```
Windows will also sleep by default. To prevent it while Tess is running: **Settings → System → Power & Battery → Screen and sleep**, and set "When plugged in, put my device to sleep" to **Never**.

---

## 8.0 Step 7 — Open the Dashboard

Once you see `[Tess Cockpit] Live dashboard active at: http://localhost:5000` in the terminal, open that URL in your browser. All panels — Cockpit, Trade Archive, Market Intel, System Code, Configuration, Dev Console — live there.

---

## 9.0 Troubleshooting

- **`ModuleNotFoundError` for any package** → your virtual environment isn't active. Re-run the `activate` command from Step 3 (you'll need to do this every time you open a new terminal window).
- **`401 Unauthorized` in the terminal or logs** → one of your API keys in `.env` is missing, wrong, or has extra spaces/quotes around it.
- **`OSError: Address already in use` on port 5000** → another process is already using that port. Either stop it, or change the port in `scanner.py`'s `app.run(port=5000, ...)` line.
- **Nothing happens after "Executing Scan #1"** → check `.tess_diary/tess_thinking.log` for the actual error — it logs far more detail than the terminal shows.

---

## 10.0 Stopping Tess

Press `Ctrl + C` in the terminal where it's running. Open positions are not automatically closed — they remain in `.tess_diary/open_positions.json` and will be picked back up the next time you start `scanner.py`.
