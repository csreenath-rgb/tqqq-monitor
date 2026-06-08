"""
Test harness for tqqq_backtest.py
Runs OFFLINE (no internet): synthetic data + known-answer checks.
Proves the engine math is correct before the script is used on real data.
"""
import numpy as np
import pandas as pd
import tqqq_backtest as bt

PASS, FAIL = "PASS", "FAIL"
results = []

def check(name, cond, detail=""):
    results.append((PASS if cond else FAIL, name, detail))
    print(f"[{PASS if cond else FAIL}] {name}  {detail}")


# ---------------------------------------------------------------------------
# 1. Volatility-drag: 3x daily on +1%/-1% must end DOWN ~0.09%
# ---------------------------------------------------------------------------
dates = pd.bdate_range("2020-01-01", periods=3)
qqq = pd.Series([100.0, 101.0, 99.99], index=dates)  # +1% then -1%
rf = pd.Series(0.0, index=dates)
sim = bt.simulate_tqqq_returns(qqq, rf, expense_ratio=0.0, financing_spread=0.0, leverage=3.0)
sim_price = (1 + sim.fillna(0)).cumprod()
end = sim_price.iloc[-1]
expected = 1.03 * 0.97  # 0.9991
check("3x daily drag (+1%/-1% -> -0.09%)", abs(end - expected) < 1e-6,
      f"got {end:.6f}, expected {expected:.6f}")

# leverage scales daily return exactly
check("daily leverage = 3x", abs(sim.iloc[1] - 3 * 0.01) < 1e-9,
      f"got {sim.iloc[1]:.6f}")

# financing+expense create a daily drag of the right size
sim2 = bt.simulate_tqqq_returns(qqq, pd.Series(0.04, index=dates),
                                expense_ratio=0.0086, financing_spread=0.005, leverage=3.0)
expected_cost = (0.0086 + 2 * (0.04 + 0.005)) / 252
check("financing+expense daily drag", abs((3 * 0.01 - sim2.iloc[1]) - expected_cost) < 1e-9,
      f"drag={3*0.01 - sim2.iloc[1]:.6f}, expected {expected_cost:.6f}")


# ---------------------------------------------------------------------------
# 2. CAGR: double over exactly 1 year -> 100%
# ---------------------------------------------------------------------------
eq = pd.Series([100.0, 200.0],
               index=pd.to_datetime(["2020-01-01", "2021-01-01"]))  # 366 days (leap)
c = bt.cagr(eq)
# 366 days -> (2)^(365.25/366)-1
exp_c = 2 ** (365.25 / 366) - 1
check("CAGR doubling in ~1yr ≈ 100%", abs(c - exp_c) < 1e-6, f"got {c*100:.3f}%")


# ---------------------------------------------------------------------------
# 3. Max drawdown: 120 -> 60 is -50%
# ---------------------------------------------------------------------------
eq = pd.Series([100, 120, 60, 90], index=pd.bdate_range("2020-01-01", periods=4))
mdd = bt.max_drawdown(eq)
check("max drawdown = -50%", abs(mdd - (-0.5)) < 1e-9, f"got {mdd*100:.1f}%")


# ---------------------------------------------------------------------------
# 4. Sharpe: known mean/std
# ---------------------------------------------------------------------------
r = pd.Series([0.01, -0.005, 0.02, 0.0, 0.015],
              index=pd.bdate_range("2020-01-01", periods=5))
rf0 = pd.Series(0.0, index=r.index)
exp_sharpe = (r.mean() / r.std()) * np.sqrt(252)
check("Sharpe matches manual calc", abs(bt.sharpe(r, rf0) - exp_sharpe) < 1e-9,
      f"got {bt.sharpe(r, rf0):.3f}")


# ---------------------------------------------------------------------------
# 5. No look-ahead: a 1-day price spike known only at close t must NOT
#    affect the return earned on day t (position was set at t-1).
# ---------------------------------------------------------------------------
d = pd.bdate_range("2020-01-01", periods=5)
asset = pd.Series([0.0, 0.0, 0.10, 0.0, 0.0], index=d)   # +10% on day index 2
cash = pd.Series(0.0, index=d)
# Signal that "magically" turns on exactly on the spike day (index 2)
sig = pd.Series([0, 0, 1, 0, 0], index=d, dtype=float)
res = bt.run_backtest(asset, cash, sig, d, start_capital=100.0, cost_bps=0.0, tax=False)
# Because weight is shifted, the +10% on day 2 should be MISSED (we were flat into day2).
check("no look-ahead (spike captured only via prior signal)",
      abs(res["gross_equity"].iloc[-1] - 100.0) < 1e-9,
      f"final={res['gross_equity'].iloc[-1]:.4f} (should be 100 -> spike correctly missed)")
# Now set the signal the day BEFORE -> spike SHOULD be captured
sig2 = pd.Series([0, 1, 1, 0, 0], index=d, dtype=float)
res2 = bt.run_backtest(asset, cash, sig2, d, start_capital=100.0, cost_bps=0.0, tax=False)
check("position set prior day captures spike",
      abs(res2["gross_equity"].iloc[-1] - 110.0) < 1e-6,
      f"final={res2['gross_equity'].iloc[-1]:.4f} (should be ~110)")


# ---------------------------------------------------------------------------
# 6. Tax engine: short-term vs long-term classification + correct tax
# ---------------------------------------------------------------------------
# Build a single trade: in for ~100 days with a +20% gain, then out.
n = 400
d = pd.bdate_range("2020-01-01", periods=n)
asset = pd.Series(0.0, index=d)
# put a single +20% pop on day 50, hold, then exit
asset.iloc[50] = 0.20
cash = pd.Series(0.0, index=d)
# Short-term: enter day 10, exit day 80 (~ under 365 days)
sig = pd.Series(0.0, index=d)
sig.iloc[10:80] = 1.0
res = bt.run_backtest(asset, cash, sig, d, start_capital=100_000.0, cost_bps=0.0,
                      st_rate=0.408, lt_rate=0.238, tax=True)
# gross gain = +20% on 100k = 20k. ST tax = 20k*0.408 = 8160. net final = 120000-8160
exp_net = 120_000 - 8_160
check("short-term gain taxed at ST rate",
      abs(res["net_equity"].iloc[-1] - exp_net) < 1.0,
      f"net=${res['net_equity'].iloc[-1]:,.0f}, expected ${exp_net:,.0f}")
check("trade flagged short-term", res["trades"][0]["long_term"] is False,
      f"hold_days={res['trades'][0]['hold_days']}")

# Long-term: enter day 10, exit day 380 (> 365 days)
sig = pd.Series(0.0, index=d)
sig.iloc[10:380] = 1.0
res = bt.run_backtest(asset, cash, sig, d, start_capital=100_000.0, cost_bps=0.0,
                      st_rate=0.408, lt_rate=0.238, tax=True)
exp_net = 120_000 - 20_000 * 0.238
check("long-term gain taxed at LT rate",
      abs(res["net_equity"].iloc[-1] - exp_net) < 1.0,
      f"net=${res['net_equity'].iloc[-1]:,.0f}, expected ${exp_net:,.0f}")
check("trade flagged long-term", res["trades"][0]["long_term"] is True,
      f"hold_days={res['trades'][0]['hold_days']}")


# ---------------------------------------------------------------------------
# 7. SMA signal correctness on a simple ramp
# ---------------------------------------------------------------------------
d = pd.bdate_range("2020-01-01", periods=260)
price = pd.Series(np.r_[np.full(205, 100.0), np.linspace(100, 130, 55)], index=d)
sig = bt.signal_sma_filter(price, window=200)
check("SMA signal flat region = 0 (price == SMA, not above)",
      sig.iloc[201] == 0.0, f"sig[201]={sig.iloc[201]}")
check("SMA signal = 1 once price clearly above SMA",
      sig.iloc[-1] == 1.0, f"sig[-1]={sig.iloc[-1]}")


# ---------------------------------------------------------------------------
# 8. Simulation splice + validation on synthetic data with a fake 'actual' TQQQ
# ---------------------------------------------------------------------------
np.random.seed(42)
N = 1500
d = pd.bdate_range("2008-01-01", periods=N)
rets = np.random.normal(0.0004, 0.012, N)
qqq = pd.Series(100 * np.cumprod(1 + rets), index=d)
rf = pd.Series(0.03, index=d)
# fabricate an "actual" TQQQ for the back half only (mimics 2010 inception)
inception_i = 600
sim_full = bt.simulate_tqqq_returns(qqq, rf, 0.0086, 0.005, 3.0)
fake_actual_price = (1 + sim_full.fillna(0)).cumprod()
fake_actual_price = fake_actual_price * (50.0 / fake_actual_price.iloc[inception_i])
tqqq = pd.Series(np.nan, index=d)
tqqq.iloc[inception_i:] = fake_actual_price.iloc[inception_i:].values

df = pd.DataFrame({"qqq": qqq, "tqqq": tqqq, "vix": np.nan, "rf": rf})
df2, val = bt.build_tqqq_series(df, verbose=False)
check("splice has no NaNs in tqqq_full", df2["tqqq_full"].notna().all())
check("simulated flag true before inception only",
      bool(df2["simulated"].iloc[:inception_i].all()) and
      bool(~df2["simulated"].iloc[inception_i:].any()))
check("splice continuous at inception (no jump)",
      abs(df2["tqqq_full"].iloc[inception_i] / df2["tqqq_full"].iloc[inception_i - 1] - 1) < 0.5,
      "continuity ok")
# since fake actual IS the sim, correlation should be ~1
check("sim-vs-actual validation corr ~1 (synthetic identity)",
      val.get("daily_return_corr", 0) > 0.99,
      f"corr={val.get('daily_return_corr'):.4f}")


# ---------------------------------------------------------------------------
# 9. End-to-end: full run on synthetic data (no internet) must complete & be sane
# ---------------------------------------------------------------------------
class Args:
    expense = 0.0086; fin_spread = 0.005; leverage = 3.0
    sma = 200; mom = 252; buy_buffer = 0.0; sell_buffer = 0.0
    target_vol = 0.40; capital = 100_000.0; cost = 3.0
    st_rate = 0.408; lt_rate = 0.238; quiet = True

# bigger synthetic set with a clear up-trend + a crash, to exercise the filter
np.random.seed(7)
N = 3000
d = pd.bdate_range("2005-01-01", periods=N)
trend = np.r_[np.random.normal(0.0006, 0.011, 1500),       # bull
              np.random.normal(-0.002, 0.025, 300),         # crash
              np.random.normal(0.0007, 0.012, 1200)]        # recovery
qqq = pd.Series(100 * np.cumprod(1 + trend), index=d)
inception_i = 1300
sim_full = bt.simulate_tqqq_returns(qqq, pd.Series(0.03, index=d), 0.0086, 0.005, 3.0)
fa = (1 + sim_full.fillna(0)).cumprod(); fa = fa * (40 / fa.iloc[inception_i])
tqqq = pd.Series(np.nan, index=d); tqqq.iloc[inception_i:] = fa.iloc[inception_i:].values
df = pd.DataFrame({"qqq": qqq, "tqqq": tqqq, "vix": np.nan, "rf": 0.03})

df3, res, table, val = bt.run_all(df, Args())
check("end-to-end run completes", table is not None and len(table) == 5)
check("all CAGRs finite", np.isfinite(table["CAGR_gross"]).all(),
      f"\n{table[['CAGR_gross','CAGR_net','Sharpe','Calmar','MaxDD']]}")
check("net CAGR <= gross CAGR (tax can only reduce)",
      (table["CAGR_net"] <= table["CAGR_gross"] + 1e-9).all())
check("trend filter reduces drawdown vs BH_TQQQ",
      abs(table.loc["STRAT1_200dMA", "MaxDD"]) < abs(table.loc["BH_TQQQ", "MaxDD"]),
      f"STRAT1 MaxDD={table.loc['STRAT1_200dMA','MaxDD']*100:.1f}% vs "
      f"BH_TQQQ={table.loc['BH_TQQQ','MaxDD']*100:.1f}%")
check("STRAT2 trades <= STRAT1 trades (more confirmation = less churn)",
      table.loc["STRAT2_MA+Mom", "n_trades"] <= table.loc["STRAT1_200dMA", "n_trades"],
      f"S2={int(table.loc['STRAT2_MA+Mom','n_trades'])} vs "
      f"S1={int(table.loc['STRAT1_200dMA','n_trades'])}")
check("time-in-market < 100% for filtered strategies",
      table.loc["STRAT1_200dMA", "time_in_mkt"] < 1.0)

print("\n--- sample metrics from synthetic end-to-end run ---")
print(bt.fmt_table(table).to_string())

# ---------------------------------------------------------------------------
# 10. Buffer comparison mode runs and returns a row per (strategy, band)
# ---------------------------------------------------------------------------
presets = bt.parse_buffer_string("raw,5/3,8/5")
cmp = bt.compare_buffers(df[["qqq", "tqqq", "vix", "rf"]].copy(), Args(), presets)
check("compare_buffers returns 2 strategies x 3 bands = 6 rows",
      len(cmp) == 6, f"rows={len(cmp)}")
check("compare_buffers all CAGRs finite", np.isfinite(cmp["CAGR_gross"]).all())
# wider band should not trade MORE than raw for STRAT1 (>= relationship on trades)
raw_tr = cmp.loc["STRAT1 [raw]", "n_trades"]
b85_tr = cmp.loc["STRAT1 [8/5]", "n_trades"]
check("compare_buffers: 8/5 trades <= raw trades (STRAT1)",
      b85_tr <= raw_tr, f"8/5={int(b85_tr)} vs raw={int(raw_tr)}")

# ---------------------------------------------------------------------------
n_fail = sum(1 for r in results if r[0] == FAIL)
print("\n" + "=" * 60)
print(f"{len(results)} checks, {n_fail} failures")
print("=" * 60)
import sys
sys.exit(1 if n_fail else 0)
