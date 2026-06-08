"""
strategy_monitor.py  -  daily monitor for the two high-confidence TQQQ strategies
=================================================================================

Run once per trading day (after the US close) by GitHub Actions. It:

  1. Pulls ~3 years of daily QQQ (+ TQQQ, T-bill yield) via yfinance.
  2. Computes TODAY's signal for the two HIGH-CONFIDENCE strategies:
       STRAT1  = hold TQQQ when QQQ closes above its 200-day SMA, else T-bills.
       STRAT2  = STRAT1 AND QQQ's trailing 12-month return > 0.
  3. Detects a FRESH transition (signal on the latest bar differs from the bar
     before it) -> this is the "action item".
  4. Computes trailing GROSS performance metrics for the dashboard.
  5. Sends alerts (email + Telegram) on a transition, plus an optional weekly
     heartbeat so users know the system is alive during long quiet stretches.
  6. Writes state.json (dup-alert guard + audit), appends history.csv, and
     regenerates index.html (the dashboard, served by GitHub Pages).

The signal functions here are INTENTIONALLY identical to tqqq_backtest.py; the
test harness asserts they match bar-for-bar so the two can never silently drift.

No look-ahead: the signal is read off the latest CLOSED bar and tells you what to
hold from the next session onward. The monitor acts AFTER the close, so this is
correct, not peeking.

NOT investment advice. Leveraged ETFs can lose nearly all value; see README.
"""

import os
import json
import sys
import datetime as dt
import numpy as np
import pandas as pd

TRADING_DAYS = 252
STATE_FILE = "state.json"
HISTORY_FILE = "history.csv"
DASHBOARD_FILE = "index.html"

SGOV = "a Treasury-bill fund (e.g. SGOV/BIL)"  # human label for the "cash" leg


# ---------------------------------------------------------------------------
# SIGNALS  (must match tqqq_backtest.py exactly - enforced by tests)
# ---------------------------------------------------------------------------
def signal_sma_filter(qqq_close, window=200, buy_buffer=0.0, sell_buffer=0.0):
    sma = qqq_close.rolling(window).mean()
    upper = sma * (1 + buy_buffer)
    lower = sma * (1 - sell_buffer)
    sig = pd.Series(np.nan, index=qqq_close.index)
    sig[qqq_close > upper] = 1.0
    sig[qqq_close < lower] = 0.0
    return sig.ffill().fillna(0.0)


def signal_sma_plus_momentum(qqq_close, window=200, mom_window=252,
                             buy_buffer=0.0, sell_buffer=0.0):
    sma_sig = signal_sma_filter(qqq_close, window=window,
                                buy_buffer=buy_buffer, sell_buffer=sell_buffer)
    mom = qqq_close / qqq_close.shift(mom_window) - 1.0
    return ((sma_sig > 0) & (mom > 0)).astype(float)


# ---------------------------------------------------------------------------
# DATA
# ---------------------------------------------------------------------------
def fetch(period_years=3.5):
    try:
        import yfinance as yf
    except ImportError:
        raise SystemExit("pip install yfinance pandas numpy")
    start = (dt.date.today() - dt.timedelta(days=int(period_years * 365))).isoformat()

    def _close(t):
        d = yf.download(t, start=start, auto_adjust=True, progress=False, threads=False)
        if d is None or len(d) == 0:
            return None
        c = d["Close"]
        return c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c

    qqq = _close("QQQ")
    if qqq is None:
        raise SystemExit("Could not download QQQ - check connectivity.")
    tqqq = _close("TQQQ")
    irx = _close("^IRX")
    df = pd.DataFrame(index=qqq.index)
    df["qqq"] = qqq
    df["tqqq"] = tqqq.reindex(qqq.index) if tqqq is not None else np.nan
    df["rf"] = (irx.reindex(qqq.index) / 100.0).ffill() if irx is not None else 0.04
    df["rf"] = df["rf"].fillna(0.04).clip(lower=0.0)
    return df.dropna(subset=["qqq"])


# ---------------------------------------------------------------------------
# METRICS  (gross, trailing - for the dashboard)
# ---------------------------------------------------------------------------
def _equity(asset_ret, cash_ret, signal):
    w = signal.shift(1).fillna(0.0)
    r = w * asset_ret + (1 - w) * cash_ret
    return (1 + r.fillna(0.0)).cumprod()


def _cagr(eq):
    eq = eq.dropna()
    if len(eq) < 2:
        return np.nan
    days = (eq.index[-1] - eq.index[0]).days
    return (eq.iloc[-1] / eq.iloc[0]) ** (365.25 / days) - 1 if days > 0 else np.nan


def _mdd(eq):
    eq = eq.dropna()
    return float((eq / eq.cummax() - 1).min()) if len(eq) > 1 else np.nan


def _sharpe(asset_ret, cash_ret, signal, rf_daily):
    w = signal.shift(1).fillna(0.0)
    r = (w * asset_ret + (1 - w) * cash_ret).dropna()
    if len(r) < 2 or r.std() == 0:
        return np.nan
    ex = r - rf_daily.reindex(r.index).fillna(0.0)
    return float(ex.mean() / r.std() * np.sqrt(TRADING_DAYS))


def trailing_metrics(df, signal):
    """Gross trailing metrics over the available window (typically ~3y)."""
    tqqq_ret = df["tqqq"].pct_change() if df["tqqq"].notna().all() \
        else (3.0 * df["qqq"].pct_change() - (0.0086 + 2 * (df["rf"] + 0.005)) / TRADING_DAYS)
    cash_ret = df["rf"] / TRADING_DAYS
    eq = _equity(tqqq_ret, cash_ret, signal)
    return {
        "cagr": _cagr(eq),
        "mdd": _mdd(eq),
        "sharpe": _sharpe(tqqq_ret, cash_ret, signal, df["rf"] / TRADING_DAYS),
        "calmar": (_cagr(eq) / abs(_mdd(eq))) if _mdd(eq) not in (0, None) and not np.isnan(_mdd(eq)) else np.nan,
        "window_days": len(eq),
    }


# ---------------------------------------------------------------------------
# SIGNAL STATE + TRANSITION DETECTION
# ---------------------------------------------------------------------------
def position_since(signal):
    """Date the current signal value was first entered (last flip)."""
    cur = signal.iloc[-1]
    flipped = signal[signal != cur]
    if len(flipped) == 0:
        return signal.index[0]
    last_other = flipped.index[-1]
    after = signal.index[signal.index > last_other]
    return after[0] if len(after) else signal.index[-1]


def evaluate(df, buy_buffer=0.0, sell_buffer=0.0):
    """Return per-strategy state dict and whether each just transitioned.

    buy_buffer / sell_buffer are FRACTIONS (e.g. 0.05 / 0.03). 0/0 = plain cross.
    """
    qqq = df["qqq"]
    sma200 = qqq.rolling(200).mean()
    mom252 = qqq / qqq.shift(252) - 1.0

    s1 = signal_sma_filter(qqq, 200, buy_buffer=buy_buffer, sell_buffer=sell_buffer)
    s2 = signal_sma_plus_momentum(qqq, 200, 252,
                                  buy_buffer=buy_buffer, sell_buffer=sell_buffer)

    out = {}
    for name, sig in [("STRAT1", s1), ("STRAT2", s2)]:
        today = float(sig.iloc[-1])
        yday = float(sig.iloc[-2]) if len(sig) > 1 else today
        out[name] = {
            "signal": today,
            "position": "TQQQ" if today > 0.5 else "T-BILLS",
            "transition": today != yday,
            "direction": ("RISK-ON" if today > yday else "RISK-OFF") if today != yday else None,
            "since": position_since(sig).date().isoformat(),
            "metrics": trailing_metrics(df, sig),
        }
    band = "raw 200d cross" if buy_buffer == 0 and sell_buffer == 0 \
        else f"+{buy_buffer*100:.0f}% / -{sell_buffer*100:.0f}% band"
    levels = {
        "date": qqq.index[-1].date().isoformat(),
        "qqq_close": float(qqq.iloc[-1]),
        "sma200": float(sma200.iloc[-1]),
        "pct_vs_sma": float(qqq.iloc[-1] / sma200.iloc[-1] - 1) if pd.notna(sma200.iloc[-1]) else None,
        "mom_12m": float(mom252.iloc[-1]) if pd.notna(mom252.iloc[-1]) else None,
        "tqqq_close": float(df["tqqq"].iloc[-1]) if pd.notna(df["tqqq"].iloc[-1]) else None,
        "band": band,
        "buy_buffer": buy_buffer,
        "sell_buffer": sell_buffer,
    }
    return out, levels


# ---------------------------------------------------------------------------
# MESSAGE FORMATTING
# ---------------------------------------------------------------------------
def format_alert(name, st, levels):
    if st["direction"] == "RISK-ON":
        action = f"BUY TQQQ  -  move out of {SGOV} into TQQQ."
    else:
        action = f"SELL TQQQ  -  move from TQQQ into {SGOV}."
    pct = levels["pct_vs_sma"]
    pct_s = f"{pct*100:+.1f}%" if pct is not None else "n/a"
    mom = levels["mom_12m"]
    mom_s = f"{mom*100:+.1f}%" if mom is not None else "n/a"
    return (
        f"\u26a0\ufe0f ACTION ITEM  -  {name} flipped {st['direction']}\n"
        f"{action}\n\n"
        f"As of close {levels['date']}:\n"
        f"  QQQ close .......... {levels['qqq_close']:.2f}\n"
        f"  200-day average .... {levels['sma200']:.2f}  ({pct_s} vs MA)\n"
        f"  12-month return .... {mom_s}\n"
        f"  New position ....... {st['position']}\n\n"
        f"Reminder: act on the NEXT session's open/close per your plan. "
        f"This is a mechanical signal, not advice."
    )


def format_heartbeat(states, levels):
    lines = [f"\u2705 Weekly status  -  close {levels['date']}  (no action needed)"]
    for name, st in states.items():
        lines.append(f"  {name}: holding {st['position']} since {st['since']}")
    pct = levels["pct_vs_sma"]
    lines.append(f"  QQQ {levels['qqq_close']:.2f} vs 200d {levels['sma200']:.2f} "
                 f"({pct*100:+.1f}%)" if pct is not None else "")
    return "\n".join(l for l in lines if l)


# ---------------------------------------------------------------------------
# STATE / HISTORY PERSISTENCE
# ---------------------------------------------------------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(states, levels):
    payload = {"last_run_date": levels["date"],
               "signals": {k: v["signal"] for k, v in states.items()},
               "positions": {k: v["position"] for k, v in states.items()},
               "since": {k: v["since"] for k, v in states.items()}}
    with open(STATE_FILE, "w") as f:
        json.dump(payload, f, indent=2)


def append_history(states, levels, alerted):
    row = {"run_date": dt.date.today().isoformat(), "data_date": levels["date"],
           "qqq_close": round(levels["qqq_close"], 2),
           "sma200": round(levels["sma200"], 2),
           "STRAT1_pos": states["STRAT1"]["position"],
           "STRAT2_pos": states["STRAT2"]["position"],
           "alerted": alerted}
    df = pd.DataFrame([row])
    if os.path.exists(HISTORY_FILE):
        df.to_csv(HISTORY_FILE, mode="a", header=False, index=False)
    else:
        df.to_csv(HISTORY_FILE, index=False)


def already_alerted_today(state, levels):
    """Guard: don't re-alert if the job runs twice for the same data date."""
    return state.get("last_run_date") == levels["date"]


# ---------------------------------------------------------------------------
# ORCHESTRATION
# ---------------------------------------------------------------------------
def decide_alerts(states, levels, prev_state, force=False, heartbeat_dow=None):
    """Return (subject, body) to send, or (None, None) if nothing to send."""
    transitions = {n: s for n, s in states.items() if s["transition"]}
    dup = already_alerted_today(prev_state, levels)

    if transitions and not dup:
        parts = [format_alert(n, s, levels) for n, s in transitions.items()]
        subj = "ACTION: " + ", ".join(f"{n} {s['direction']}" for n, s in transitions.items())
        return subj, "\n\n" + ("\n\n" + "-" * 40 + "\n\n").join(parts)

    if force:
        return "TQQQ monitor (forced test)", format_heartbeat(states, levels)

    if heartbeat_dow is not None and dt.date.today().weekday() == heartbeat_dow and not dup:
        return "Weekly TQQQ monitor status", format_heartbeat(states, levels)

    return None, None


def run(send=True, force=False):
    from alerts import send_all  # local import so tests can run without it
    from dashboard import render

    df = fetch()
    # band is configurable via env (PERCENT). Default raw 200d cross.
    bb = float(os.environ.get("BUY_BUFFER", "0") or "0") / 100.0
    sb = float(os.environ.get("SELL_BUFFER", "0") or "0") / 100.0
    states, levels = evaluate(df, buy_buffer=bb, sell_buffer=sb)
    prev = load_state()

    hb = os.environ.get("HEARTBEAT_DOW")
    hb = int(hb) if hb not in (None, "") else None
    subject, body = decide_alerts(states, levels, prev, force=force, heartbeat_dow=hb)

    alerted = False
    if subject and send:
        results = send_all(subject, body)
        alerted = any(results.values())
        print(f"Alert sent: {subject}\nChannels: {results}")
    elif subject:
        print(f"[dry-run] would send:\nSubject: {subject}\n{body}")
    else:
        print(f"No action. {levels['date']}: "
              + ", ".join(f"{n}={s['position']}" for n, s in states.items()))

    render(states, levels, DASHBOARD_FILE)
    append_history(states, levels, alerted)
    save_state(states, levels)
    return states, levels


if __name__ == "__main__":
    force = "--force" in sys.argv
    dry = "--dry-run" in sys.argv
    run(send=not dry, force=force)
