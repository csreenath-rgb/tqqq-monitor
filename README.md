# TQQQ Strategy Monitor

A small, free-to-run system that watches the two high-confidence, low-turnover
TQQQ strategies daily and alerts a few family members (email + Telegram) **only
when an action is actually needed** — which, by design, is just a few times a
year. It also publishes a live dashboard via GitHub Pages.

This pairs with `tqqq_backtest.py`. The monitor's signal logic is **identical**
to the backtest's (the test suite enforces this bar-for-bar), so what you
backtested is exactly what gets monitored.

> **Not investment advice.** Leveraged ETFs can lose nearly all their value in a
> sustained decline. These are mechanical signals you choose to act on.

---

## The two strategies

- **STRAT1 — 200-day trend filter:** hold TQQQ when QQQ closes above its 200-day
  moving average; otherwise hold a Treasury-bill fund (SGOV/BIL).
- **STRAT2 — trend + 12-month momentum:** same as STRAT1, but only when QQQ's
  trailing 12-month return is also positive. Trades even less → more gains taxed
  at the lower long-term rate.

### Configurable buffer band (whipsaw control)

Both strategies take an optional **band** so you don't flip on every tiny cross
of the 200-day line. Buy only when QQQ is `buy_buffer`% *above* the average,
sell only when it's `sell_buffer`% *below*. Wider band → fewer trades and fewer
whipsaws, but you enter and exit later. Named presets: `raw` (0/0, plain cross),
`5/3`, `8/5`. You can also pass any custom pair like `10/7`.

**Explore bands in the backtest** (pick the one you like *before* deploying):

```bash
# compare several bands side by side on real data
python tqqq_backtest.py --compare-buffers "raw,5/3,8/5,10/7"

# or run a single chosen band end-to-end (percent units)
python tqqq_backtest.py --buy-buffer 5 --sell-buffer 3
```

**Set the band the live monitor uses** via repository *variables* (not secrets):
Repo → Settings → Secrets and variables → Actions → **Variables** tab:

| Variable | Meaning | Example |
|---|---|---|
| `BUY_BUFFER` | percent above the 200-day avg to buy | `5` |
| `SELL_BUFFER` | percent below the 200-day avg to sell | `3` |

Leave both unset (or `0`) for the plain 200-day cross. The active band is shown
on the dashboard so everyone can see which rule is live. Because the monitor and
backtest share identical signal code (enforced by the parity test across every
band), the band you choose in the backtest behaves the same way live.

---

## What you need (all free)

1. A **GitHub account** and a repository (make it **private** — your family's
   emails will be stored as repository secrets, and a private repo is the safe
   default).
2. An **email sending account** with an **app password** (a Gmail account works;
   you must enable 2-factor auth, then create an *app password* — not your normal
   login password).
3. A **Telegram bot** and a **group chat** your family joins (steps below).

---

## Repository layout

```
your-repo/
├─ strategy_monitor.py
├─ alerts.py
├─ dashboard.py
├─ tqqq_backtest.py            # the full backtest (optional but recommended)
├─ test_strategy_monitor.py
├─ test_tqqq_backtest.py
└─ .github/workflows/monitor.yml   # <-- move monitor.yml here
```

> Put `monitor.yml` at `.github/workflows/monitor.yml` in the repo.

---

## Setup — step by step

### 1. Create the Telegram bot and group
1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts. It
   gives you a **bot token** like `123456:ABC-DEF...`.
2. Create a Telegram **group**, add your family members, and add your new bot to
   the group.
3. Get the group's **chat id**: add **@RawDataBot** to the group briefly (or send
   a message in the group, then visit
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read the
   `"chat":{"id":-100...}` value). Group chat ids are usually **negative**.

### 2. Add GitHub repository secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**.
Add the ones for the channels you want; missing channels are simply skipped.

| Secret | Example / note |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your sending email address |
| `SMTP_PASS` | the **app password** (not your login password) |
| `EMAIL_FROM` | usually same as `SMTP_USER` |
| `EMAIL_TO` | comma-separated family emails: `a@x.com,b@y.com,c@z.com` |
| `TELEGRAM_BOT_TOKEN` | from @BotFather |
| `TELEGRAM_CHAT_ID` | the group chat id (often negative) |

Optional: under the **Variables** tab add `HEARTBEAT_DOW` = `0` to get a "still
alive, no action needed" message every Monday (0=Mon … 6=Sun). Leave it unset for
alerts only. Also under **Variables**: `BUY_BUFFER` / `SELL_BUFFER` (percent) to
set the band the live monitor trades — see "Configurable buffer band" above.

### 3. Enable GitHub Pages (for the dashboard)
Repo → **Settings → Pages → Build and deployment → Source: GitHub Actions.**
After the first run, your dashboard is at
`https://<your-username>.github.io/<repo>/`.

### 4. Test it before trusting it
- Push the code. Go to the **Actions** tab → run **TQQQ Strategy Monitor**
  manually (workflow_dispatch) with **force = true**. You should get a test
  email + Telegram message and a published dashboard within a couple of minutes.
- Run the offline tests locally too:
  ```bash
  pip install yfinance pandas numpy
  python test_strategy_monitor.py   # 27 checks, expect 0 failures
  python test_tqqq_backtest.py      # 24 checks, expect 0 failures
  ```

### 5. Let it run
The schedule (`cron: "30 22 * * 1-5"`) runs after the US close on weekdays.
GitHub's scheduler can lag a few minutes — irrelevant for an end-of-day signal.

---

## How alerts behave

- **Action needed (signal flips):** every recipient gets a message naming the
  action ("BUY TQQQ" / "SELL TQQQ, move to T-bills"), the QQQ close, the 200-day
  level, and the 12-month return.
- **Ran twice for the same trading day:** a dedup guard prevents a repeat alert.
- **Nothing changed:** silent (plus the optional Monday heartbeat). Long quiet
  stretches are normal and expected for these strategies.

---

## Honest limitations

- The dashboard's trailing metrics are **gross of tax**; after-tax figures come
  from `tqqq_backtest.py`.
- The monitor reads daily *closing* signals. If you want to act intraday or use a
  buffer band to reduce whipsaw, change `signal_sma_filter` in **both**
  `strategy_monitor.py` and `tqqq_backtest.py` (the parity test will tell you if
  they drift).
- Email/Telegram delivery depends on those third-party services; the job logs a
  clear success/failure per channel each run (visible in the Actions log).
- This automates *signalling*, not *trading*. It never touches a brokerage. You
  place the trades.
