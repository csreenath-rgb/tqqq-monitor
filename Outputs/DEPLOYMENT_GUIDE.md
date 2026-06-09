# TQQQ Backtest + Dashboard — Step-by-Step Deployment Guide

This guide takes you from a fresh download to (1) a working backtest you can run
on demand, and (2) a live, self-updating dashboard that alerts your family by
email + Telegram only when an action is needed.

There are **two separate things** being deployed, and it matters that you keep
them straight:

| Thing | What it is | Where it should run | Why |
|---|---|---|---|
| **The backtest** (`tqqq_backtest.py`) | A script you run occasionally to study strategies/bands | Your laptop **or** Claude Cowork | One-off analysis; needs internet only while running |
| **The monitor + dashboard** (`strategy_monitor.py` etc.) | An always-on daily job that checks signals, alerts family, updates the dashboard | **GitHub Actions (the cloud)** | Must run every trading day whether your computer is on or off |

> **Key decision, made for you:** the always-on monitor belongs on **GitHub
> Actions**, *not* Cowork. A Cowork scheduled task only runs while your desktop
> app is open and the machine is awake — wrong tool for a job your family relies
> on daily. Use Cowork (or your laptop) to *run the backtest*; use GitHub for the
> *always-on dashboard*. Both paths are below.

> **Not investment advice.** Leveraged ETFs can lose nearly all their value in a
> sustained decline. These are mechanical signals you choose to act on.

---

## Contents

1. [What you need before starting](#0-prerequisites)
2. [PART A — Run the backtest on your own laptop](#part-a)
3. [PART B — Run the backtest in Claude Cowork](#part-b)
4. [PART C — Deploy the live dashboard + family alerts (GitHub)](#part-c)
5. [PART D — Verify everything works](#part-d)
6. [Troubleshooting](#troubleshooting)
7. [Daily life: what you'll actually see](#daily-life)

---

<a name="0-prerequisites"></a>
## 0. What you need before starting

**For the backtest only (Part A or B):**
- The downloaded `tqqq-monitor.zip`, unzipped.
- Python 3.10 or newer (3.12 recommended). Check with `python --version` or
  `python3 --version`.

**Additionally, for the live dashboard (Part C):**
- A free **GitHub account**.
- An **email account that can send via SMTP with an app password.** A Gmail
  account works; you must turn on 2-Step Verification, then generate a 16-character
  *app password* (this is NOT your normal Gmail password — see Part C, Step 4).
- A **Telegram account** (free) to create a bot and a family group.

You do **not** need a paid plan, a server, or a credit card for any of this.

---

<a name="part-a"></a>
## PART A — Run the backtest on your own laptop

This is the simplest way to get real numbers. ~10 minutes.

### A1. Unzip the project
Unzip `tqqq-monitor.zip`. You'll get a folder `tqqq-monitor/` containing
`tqqq_backtest.py`, `strategy_monitor.py`, and the rest.

### A2. Open a terminal in that folder
- **macOS:** open the Terminal app, type `cd ` (with a trailing space), drag the
  `tqqq-monitor` folder onto the window, press Enter.
- **Windows:** open the folder in File Explorer, click the address bar, type
  `cmd`, press Enter (a Command Prompt opens already pointed at the folder).

### A3. (Recommended) Create an isolated Python environment
This keeps the project's libraries from colliding with anything else on your
machine.
```bash
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate
```
If `python` isn't found, try `python3` instead (macOS/Linux).

### A4. Install the required libraries
```bash
pip install yfinance pandas numpy matplotlib
```

### A5. Prove the engine is correct BEFORE trusting any numbers
These tests need no internet and should report **0 failures**:
```bash
python test_tqqq_backtest.py
python test_strategy_monitor.py
```
Expect `27 checks, 0 failures` and `40 checks, 0 failures`. If either fails,
stop — something is wrong with the install; see Troubleshooting.

### A6. Run the backtest
Full run (1999 → today, with the labelled simulated pre-2010 segment):
```bash
python tqqq_backtest.py
```
Real-data-only (no simulation, TQQQ's actual 2010-onward history):
```bash
python tqqq_backtest.py --start 2010-02-11
```
Compare buffer bands side by side (the whipsaw-control knob):
```bash
python tqqq_backtest.py --compare-buffers "raw,5/3,8/5,10/7"
```
Run a single chosen band end to end (percent units):
```bash
python tqqq_backtest.py --buy-buffer 5 --sell-buffer 3
```
Adjust tax rates to your bracket (defaults assume top US rates):
```bash
python tqqq_backtest.py --st-rate 0.408 --lt-rate 0.238 --state-rate 0.0
```

### A7. Read the output
The script prints a metrics table (CAGR, Sharpe, Sortino, Calmar, max drawdown,
trades, % long-term, gross + after-tax final values) and writes:
- `tqqq_backtest_metrics.csv` — the headline table
- `tqqq_buffer_comparison.csv` — the band comparison (if you used that flag)
- `tqqq_backtest.png` — equity-curve and drawdown charts

Done. That's the analysis half. To get the live dashboard, continue to Part C.

---

<a name="part-b"></a>
## PART B — Run the backtest in Claude Cowork

Cowork can run the exact same script, with one extra thing to get right:
**Cowork's code runs in an isolated virtual machine whose internet access is a
setting you must confirm is ON.** This is the single difference from a regular
chat (which is air-gapped and can't download data).

### B1. Confirm code-execution network access is enabled
1. In Claude (web), go to **Settings → Capabilities → Code execution** (on Team/
   Enterprise this lives under **Organization settings → Capabilities**).
2. Ensure **network/egress access is enabled** for code execution.
3. If your organization uses an **allowlist** (rather than open access), the
   data source must be allowed. `yfinance` downloads from Yahoo Finance, so add
   these domains to the allowlist:
   - `query1.finance.yahoo.com`
   - `query2.finance.yahoo.com`
   - `fc.yahoo.com`
   - `finance.yahoo.com`
4. **Critical gotcha:** network settings apply **when a Cowork session starts.**
   If you change the setting, you must **start a NEW Cowork conversation** — the
   change will not take effect in an already-open one.

> If you're on an individual/Pro plan, egress is usually open and you can likely
> skip the allowlist step — but if a download fails, this is the first thing to check.

### B2. Connect a working folder
In Cowork, connect (or create) a folder for this project — for example
`~/tqqq-monitor`. Cowork can only read/write inside folders you connect.

### B3. Put the project files in that folder
Unzip `tqqq-monitor.zip` into the connected folder so Cowork can see the scripts.

### B4. Ask Cowork to run it
In a **new** Cowork conversation (see B1 gotcha), prompt something like:

> "In the connected `tqqq-monitor` folder, install yfinance/pandas/numpy/
> matplotlib, run `test_tqqq_backtest.py` and `test_strategy_monitor.py` to
> confirm 0 failures, then run `python tqqq_backtest.py --compare-buffers
> 'raw,5/3,8/5'` and show me the results table."

Cowork will execute the commands in its VM, pull live data from Yahoo, and hand
back the metrics plus the CSV/PNG files in your connected folder.

### B5. Security rules for Cowork (important — financial workflow)
- **Only feed it public market data.** Running the backtest is safe; it reads
  nothing sensitive.
- **Never paste your SMTP password or Telegram bot token into a Cowork session**,
  and don't store them in a Cowork-connected folder. Those belong only in GitHub
  Secrets (Part C). Cowork is a much larger attack surface than chat, and a
  hidden prompt injection in any document it reads could attempt to exfiltrate
  secrets. Keep credentials out of its reach entirely.

---

<a name="part-c"></a>
## PART C — Deploy the live dashboard + family alerts (GitHub)

This is the always-on half. Once set up, it runs itself every trading day for
free. ~30–40 minutes the first time.

### C1. Create a PRIVATE GitHub repository
1. Sign in to GitHub → top-right **+** → **New repository**.
2. Name it e.g. `tqqq-monitor`.
3. Set it to **Private** (your family's emails will live in repo secrets — private
   is the safe default).
4. Click **Create repository**.

### C2. Upload the project files
Easiest (no Git knowledge needed):
1. On the empty repo page, click **uploading an existing file**.
2. Drag in everything from inside the unzipped `tqqq-monitor` folder.
3. **Important folder detail:** the workflow file must end up at
   `.github/workflows/monitor.yml`. If the drag-and-drop flattens it, create the
   path manually: click **Add file → Create new file**, type
   `.github/workflows/monitor.yml` as the name (GitHub makes the folders as you
   type the slashes), and paste the contents of `monitor.yml` into it.
4. Click **Commit changes**.

Your repo should now contain `strategy_monitor.py`, `alerts.py`, `dashboard.py`,
`tqqq_backtest.py`, the two test files, `README.md`, and
`.github/workflows/monitor.yml`.

### C3. Create the Telegram bot and family group
1. In Telegram, search for **@BotFather**, open it, send `/newbot`, and follow the
   prompts. It returns a **bot token** like `123456789:ABCdef...` — copy it.
2. Create a Telegram **group**, add your family members, and add your new bot to
   the group as a member.
3. Get the group's **chat id**:
   - Send any message in the group.
   - In a browser, visit
     `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
     (paste your real token in place of `<YOUR_TOKEN>`).
   - Find `"chat":{"id":-100xxxxxxxxxx,...}` in the JSON. That negative number is
     your **chat id**. (Group ids are usually negative — keep the minus sign.)

### C4. Create the email app password (Gmail example)
1. The sending Google account needs **2-Step Verification ON**
   (Google Account → Security → 2-Step Verification).
2. Then go to **Google Account → Security → App passwords**, create one (name it
   "tqqq monitor"), and copy the **16-character password** it shows. This is what
   you'll use as `SMTP_PASS` — never your normal login password.
   - For Gmail, the other settings are: `SMTP_HOST=smtp.gmail.com`,
     `SMTP_PORT=587`.

### C5. Add repository SECRETS (the sensitive values)
In your repo: **Settings → Secrets and variables → Actions → Secrets tab →
New repository secret.** Add each of these (only the channels you want; missing
ones are skipped automatically):

| Secret name | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your sending email address |
| `SMTP_PASS` | the 16-char **app password** from C4 |
| `EMAIL_FROM` | usually same as `SMTP_USER` |
| `EMAIL_TO` | comma-separated family emails: `a@x.com,b@y.com,c@z.com` |
| `TELEGRAM_BOT_TOKEN` | the token from BotFather (C3) |
| `TELEGRAM_CHAT_ID` | the group chat id from C3 (often negative) |

### C6. (Optional) Add repository VARIABLES (non-sensitive settings)
Same page, but the **Variables tab → New repository variable.** These are
plaintext on purpose — they're settings, not secrets:

| Variable name | Meaning | Example |
|---|---|---|
| `BUY_BUFFER` | percent above the 200-day avg required to buy | `5` |
| `SELL_BUFFER` | percent below the 200-day avg required to sell | `3` |
| `HEARTBEAT_DOW` | weekday for a "still alive, no action" message (0=Mon … 6=Sun) | `0` |

Leave `BUY_BUFFER`/`SELL_BUFFER` unset (or `0`) for the plain 200-day cross.
Pick the band you settled on from the Part A/B backtest. Leave `HEARTBEAT_DOW`
unset if you want alerts only and no weekly heartbeat.

### C7. Turn on GitHub Pages (this is what publishes the dashboard)
1. Repo → **Settings → Pages.**
2. Under **Build and deployment → Source**, choose **GitHub Actions.**
3. Save. (Your dashboard URL will be
   `https://<your-username>.github.io/<repo-name>/` after the first successful run.)

### C8. Give the workflow permission to commit and deploy
1. Repo → **Settings → Actions → General.**
2. Scroll to **Workflow permissions**, select **Read and write permissions**,
   save. (This lets the daily job save its state file and history back to the repo.)

---

<a name="part-d"></a>
## PART D — Verify everything works (do this before trusting it)

### D1. Fire a manual test run
1. Repo → **Actions** tab.
2. Click the **TQQQ Strategy Monitor** workflow on the left.
3. Click **Run workflow** (top right). Set **force = true** so it sends a test
   alert regardless of the current signal. Click the green **Run workflow**.
4. Watch the run. Within ~2 minutes it should finish green.

### D2. Confirm the three outcomes
- **Email:** every address in `EMAIL_TO` receives a test message.
- **Telegram:** the family group receives a test message.
- **Dashboard:** visit `https://<your-username>.github.io/<repo>/` — you should
  see current positions, levels, the active band, and a history row.

### D3. Read the run log if anything's missing
In the run, open the **Run monitor** step. Each channel logs a clear line, e.g.
`email: sent to 3 recipient(s)` or `telegram: not configured (skipped)`. That tells
you exactly which secret is missing or wrong. Fix it and re-run.

### D4. Let it run automatically
The schedule (`cron: "30 22 * * 1-5"`) runs after the US close on weekdays.
GitHub's scheduler can lag a few minutes — irrelevant for an end-of-day signal.
No further action needed; it now runs itself.

---

<a name="troubleshooting"></a>
## Troubleshooting

**`python: command not found`** → try `python3` (and `pip3`). On Windows, reinstall
Python from python.org and tick "Add Python to PATH".

**Tests fail / `ModuleNotFoundError`** → the libraries didn't install into the
environment you're running. Re-activate the venv (A3) and re-run `pip install ...`.

**Backtest download fails on your laptop** → check your internet; corporate VPNs
sometimes block Yahoo. Try again off-VPN.

**Backtest download fails in Cowork** → almost always the network-egress setting
(B1). Confirm egress is on, add the Yahoo domains if you're on an allowlist, then
**start a brand-new Cowork conversation** (settings don't apply mid-session).

**GitHub run is red** → open the failed step. Common causes: (a) workflow not at
`.github/workflows/monitor.yml`; (b) "Read and write permissions" not set (C8);
(c) a typo'd secret name.

**No email arrived** → you almost certainly used your normal password instead of an
**app password** (C4), or 2-Step Verification isn't on. The run log's `email:`
line will say `FAILED` with the reason.

**No Telegram message** → the bot must be a **member of the group**, and the
`TELEGRAM_CHAT_ID` must be the **group** id (usually negative), not your personal id.

**Dashboard 404** → Pages source must be **GitHub Actions** (C7), and at least one
workflow run must have completed successfully.

**It went quiet for weeks** → that's normal and correct. These strategies trade a
few times a year. Set `HEARTBEAT_DOW` if the silence makes you nervous.

---

<a name="daily-life"></a>
## Daily life: what you'll actually see

- **Most days:** nothing. The job runs silently, updates the dashboard, and sends
  no message because no action is needed.
- **A few times a year:** a signal flips. Everyone in the group gets an email +
  Telegram message naming the action ("BUY TQQQ" or "SELL TQQQ → move to T-bills"),
  with the QQQ close, the 200-day level, and the 12-month return. You then place
  the trade yourself in your brokerage — the system never touches your money.
- **Anytime:** open the dashboard URL to see current positions and recent history.

To change the band later (e.g. from `5/3` to `8/5`), just edit the `BUY_BUFFER`/
`SELL_BUFFER` repository variables (C6) — no code change needed. Because the
monitor and backtest share identical signal logic, what you tested is what runs.
