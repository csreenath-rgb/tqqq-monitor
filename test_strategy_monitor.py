"""
Offline tests for strategy_monitor / alerts / dashboard.
No internet, no real credentials. Proves the decision logic before deployment.
"""
import os
import sys
import json
import datetime as dt
import numpy as np
import pandas as pd

# import modules under test
import strategy_monitor as mon
import dashboard
import tqqq_backtest as bt  # to cross-check signal parity

PASS, FAIL = "PASS", "FAIL"
results = []
def check(name, cond, detail=""):
    results.append((PASS if cond else FAIL, name, detail))
    print(f"[{PASS if cond else FAIL}] {name}  {detail}")

# work in a temp dir so we don't clobber anything
import tempfile
os.chdir(tempfile.mkdtemp())


def make_df(prices, rf=0.04, with_tqqq=True):
    idx = pd.bdate_range("2019-01-01", periods=len(prices))
    df = pd.DataFrame(index=idx)
    df["qqq"] = np.asarray(prices, float)
    df["tqqq"] = df["qqq"] * 0.3 if with_tqqq else np.nan  # dummy, not used for signal
    df["rf"] = rf
    return df


# ---------------------------------------------------------------------------
# 1. SIGNAL PARITY: monitor vs backtest must match bar-for-bar, EVERY band
# ---------------------------------------------------------------------------
np.random.seed(1)
p = 100 * np.cumprod(1 + np.random.normal(0.0004, 0.012, 800))
ps = pd.Series(p)
for label, (bb, sb) in [("raw", (0.0, 0.0)), ("5/3", (0.05, 0.03)), ("8/5", (0.08, 0.05))]:
    s_mon = mon.signal_sma_filter(ps, 200, buy_buffer=bb, sell_buffer=sb)
    s_bt = bt.signal_sma_filter(ps, window=200, buy_buffer=bb, sell_buffer=sb)
    check(f"STRAT1 signal parity (monitor==backtest) band {label}",
          (s_mon.values == s_bt.values).all(), "bar-for-bar identical")
    s_mon2 = mon.signal_sma_plus_momentum(ps, 200, 252, buy_buffer=bb, sell_buffer=sb)
    s_bt2 = bt.signal_sma_plus_momentum(ps, window=200, mom_window=252,
                                        buy_buffer=bb, sell_buffer=sb)
    check(f"STRAT2 signal parity (monitor==backtest) band {label}",
          (s_mon2.values == s_bt2.values).all(), "bar-for-bar identical")

# preset parser correctness
presets = bt.parse_buffer_string("raw,5/3,8/5,10/7")
labels = [l for l, _ in presets]
vals = [v for _, v in presets]
check("buffer preset parser: labels", labels == ["raw", "5/3", "8/5", "10/7%"],
      f"{labels}")
check("buffer preset parser: raw=0/0", vals[0] == (0.0, 0.0))
check("buffer preset parser: 5/3 -> 0.05/0.03",
      abs(vals[1][0] - 0.05) < 1e-9 and abs(vals[1][1] - 0.03) < 1e-9)
check("buffer preset parser: custom 10/7 -> 0.10/0.07",
      abs(vals[3][0] - 0.10) < 1e-9 and abs(vals[3][1] - 0.07) < 1e-9)

# ---------------------------------------------------------------------------
# 1b. WHIPSAW REDUCTION: on a choppy series oscillating around the MA,
#     a wider band must produce <= the transitions of the raw cross.
# ---------------------------------------------------------------------------
np.random.seed(11)
base = np.linspace(100, 100, 600)                    # flat mean
chop = base + np.random.normal(0, 2.5, 600).cumsum() * 0.1  # wander around it
chop = pd.Series(np.r_[np.linspace(60, 100, 250), chop])    # warmup then chop
def n_flips(sig):
    return int((sig.diff().abs() > 0).sum())
raw = bt.signal_sma_filter(chop, 200, 0.0, 0.0)
b53 = bt.signal_sma_filter(chop, 200, 0.05, 0.03)
b85 = bt.signal_sma_filter(chop, 200, 0.08, 0.05)
check("wider band reduces whipsaw flips (raw >= 5/3 >= 8/5)",
      n_flips(raw) >= n_flips(b53) >= n_flips(b85),
      f"flips raw={n_flips(raw)}, 5/3={n_flips(b53)}, 8/5={n_flips(b85)}")


# ---------------------------------------------------------------------------
# 2. TRANSITION DETECTION: a fresh cross flips the latest bar
# ---------------------------------------------------------------------------
# Build prices: flat below MA for a long time, then jump above on the last bar.
prices = list(np.full(260, 100.0))
# make a clean uptrend so SMA < price only at the very end
prices = list(np.linspace(100, 90, 260))   # downtrend -> below MA, signal 0
prices += [200.0]                           # huge jump on last bar -> above MA, signal 1
df = make_df(prices)
states, levels = mon.evaluate(df)
check("STRAT1 detects RISK-ON transition on latest bar",
      states["STRAT1"]["transition"] and states["STRAT1"]["direction"] == "RISK-ON",
      f"signal={states['STRAT1']['signal']}, dir={states['STRAT1']['direction']}")
check("position label correct on RISK-ON", states["STRAT1"]["position"] == "TQQQ")

# reverse: long uptrend then a collapse on the last bar -> RISK-OFF
prices = list(np.linspace(90, 200, 320)) + [50.0]
df = make_df(prices)
states, levels = mon.evaluate(df)
check("STRAT1 detects RISK-OFF transition on latest bar",
      states["STRAT1"]["transition"] and states["STRAT1"]["direction"] == "RISK-OFF",
      f"dir={states['STRAT1']['direction']}, pos={states['STRAT1']['position']}")


# ---------------------------------------------------------------------------
# 3. NO transition on a steady trend (no false alerts)
# ---------------------------------------------------------------------------
prices = list(np.linspace(80, 200, 400))  # steady uptrend, long above MA
df = make_df(prices)
states, levels = mon.evaluate(df)
check("no transition during steady uptrend (no false alert)",
      not states["STRAT1"]["transition"], f"transition={states['STRAT1']['transition']}")
check("position_since is stable & in the past",
      states["STRAT1"]["since"] < levels["date"], f"since={states['STRAT1']['since']}")


# ---------------------------------------------------------------------------
# 4. ALERT DECISION LOGIC
# ---------------------------------------------------------------------------
# transition present, not previously alerted today -> alert fires
prices = list(np.linspace(90, 200, 320)) + [50.0]
df = make_df(prices)
states, levels = mon.evaluate(df)
subj, body = mon.decide_alerts(states, levels, prev_state={}, force=False, heartbeat_dow=None)
check("alert fires on transition", subj is not None and "RISK-OFF" in subj, f"subj={subj}")

# dedup guard: same data_date already processed -> suppress
prev = {"last_run_date": levels["date"]}
subj2, _ = mon.decide_alerts(states, levels, prev_state=prev, force=False, heartbeat_dow=None)
check("dedup guard suppresses repeat alert same data date", subj2 is None, f"subj={subj2}")

# no transition + no heartbeat -> nothing
prices = list(np.linspace(80, 200, 400))
df = make_df(prices)
states, levels = mon.evaluate(df)
subj3, _ = mon.decide_alerts(states, levels, prev_state={}, force=False, heartbeat_dow=None)
check("silent when no action and no heartbeat", subj3 is None)

# heartbeat day -> heartbeat message (use today's weekday so it triggers)
today_dow = dt.date.today().weekday()
subj4, body4 = mon.decide_alerts(states, levels, prev_state={}, force=False, heartbeat_dow=today_dow)
check("weekly heartbeat fires on its weekday", subj4 is not None and "status" in subj4.lower(),
      f"subj={subj4}")

# force flag always produces a message
subj5, _ = mon.decide_alerts(states, levels, prev_state={}, force=True, heartbeat_dow=None)
check("force flag always sends (test path)", subj5 is not None)


# ---------------------------------------------------------------------------
# 4b. EVALUATE honors a configured band and reports it in levels
# ---------------------------------------------------------------------------
prices = list(np.linspace(80, 200, 400))
df = make_df(prices)
st_raw, lv_raw = mon.evaluate(df, buy_buffer=0.0, sell_buffer=0.0)
st_band, lv_band = mon.evaluate(df, buy_buffer=0.08, sell_buffer=0.05)
check("evaluate reports raw band label", lv_raw["band"] == "raw 200d cross",
      f"got {lv_raw['band']}")
check("evaluate reports custom band label", "8%" in lv_band["band"] and "5%" in lv_band["band"],
      f"got {lv_band['band']}")
check("evaluate stores buffer fractions", abs(lv_band["buy_buffer"] - 0.08) < 1e-9)

# Near the MA on the way up, the wider band should be slower to flip risk-on:
# construct a price that just barely crossed the raw MA on the last bar.
np.random.seed(5)
ramp = list(np.linspace(70, 100, 260))
ramp += [ramp[-1] * 1.001]  # a hair above MA -> raw on, band still off
df2 = make_df(ramp)
sr, _ = mon.evaluate(df2, 0.0, 0.0)
sb_, _ = mon.evaluate(df2, 0.08, 0.05)
check("wider band requires bigger move to go risk-on",
      sr["STRAT1"]["signal"] >= sb_["STRAT1"]["signal"],
      f"raw={sr['STRAT1']['signal']} band={sb_['STRAT1']['signal']}")
prices = list(np.linspace(90, 200, 320)) + [50.0]
df = make_df(prices)
states, levels = mon.evaluate(df)
msg = mon.format_alert("STRAT1", states["STRAT1"], levels)
check("alert message names the action (SELL)", "SELL TQQQ" in msg, "")
check("alert message includes 200-day level", "200-day average" in msg)
check("alert message includes data date", levels["date"] in msg)


# ---------------------------------------------------------------------------
# 6. STATE + HISTORY persistence round-trip
# ---------------------------------------------------------------------------
mon.save_state(states, levels)
loaded = mon.load_state()
check("state.json round-trips last_run_date", loaded.get("last_run_date") == levels["date"])
check("state.json records positions", "STRAT1" in loaded.get("positions", {}))

mon.append_history(states, levels, alerted=True)
mon.append_history(states, levels, alerted=False)
hist = pd.read_csv(mon.HISTORY_FILE)
check("history.csv appends rows", len(hist) == 2, f"rows={len(hist)}")
check("history.csv has expected columns",
      set(["data_date", "qqq_close", "STRAT1_pos", "alerted"]).issubset(hist.columns))


# ---------------------------------------------------------------------------
# 7. ALERTS routing: unconfigured channels skip gracefully (no creds in env)
# ---------------------------------------------------------------------------
import alerts
for k in ["SMTP_HOST","SMTP_PORT","SMTP_USER","SMTP_PASS","EMAIL_FROM","EMAIL_TO",
          "TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID"]:
    os.environ.pop(k, None)
res = alerts.send_all("test", "body")
check("email skips cleanly when unconfigured", res["email"] is False)
check("telegram skips cleanly when unconfigured", res["telegram"] is False)
check("send_all returns a dict of channel results", set(res.keys()) == {"email","telegram"})


# ---------------------------------------------------------------------------
# 8. DASHBOARD renders valid-looking HTML with the data in it
# ---------------------------------------------------------------------------
out = dashboard.render(states, levels, "index.html")
html = open(out).read()
check("dashboard writes index.html", os.path.exists("index.html"))
check("dashboard contains both strategies", "STRAT1" in html and "STRAT2" in html)
check("dashboard shows current QQQ close", f"{levels['qqq_close']:,.2f}" in html)
check("dashboard contains not-advice disclaimer", "not investment" in html.lower())
check("dashboard is non-trivial size", len(html) > 1500, f"{len(html)} bytes")


# ---------------------------------------------------------------------------
n_fail = sum(1 for r in results if r[0] == FAIL)
print("\n" + "=" * 60)
print(f"{len(results)} checks, {n_fail} failures")
print("=" * 60)
sys.exit(1 if n_fail else 0)
