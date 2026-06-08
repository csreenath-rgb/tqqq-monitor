"""
TQQQ Strategy Backtest  -  self-contained, runs locally (needs internet for data)
=================================================================================

WHAT THIS DOES
--------------
Backtests the two HIGH-CONFIDENCE long-term strategies discussed, against
buy-and-hold benchmarks, on TQQQ (ProShares UltraPro QQQ, 3x daily Nasdaq-100):

  BH_QQQ    : buy & hold QQQ (unleveraged reference)
  BH_TQQQ   : buy & hold TQQQ (the thing you should NOT naively do)
  STRAT1    : hold TQQQ only when QQQ closes above its 200-day moving average,
              else hold cash (T-bills). Optional buffer to cut whipsaws.
  STRAT2    : STRAT1 PLUS a 12-month (252-day) absolute-momentum confirmation.
              Trades even less -> more long-term-tax-qualified gains.
  VOLTGT    : (LOWER CONFIDENCE, gross-only) scale TQQQ exposure to target a
              fixed portfolio volatility. Reported GROSS only - see notes.

Because real TQQQ only exists from 2010-02-11, pre-2010 TQQQ is SIMULATED from
QQQ daily returns with leverage, fund expenses, and borrowing cost. The simulated
segment is clearly flagged and is validated against ACTUAL TQQQ over the overlap.

METRICS: CAGR, annualised volatility, Sharpe, Sortino, Calmar, max drawdown,
time-in-market, trade count, plus PRE-TAX and AFTER-TAX results under US
short-term vs long-term capital-gains treatment (green-card / US-resident case).

HONEST LIMITATIONS (read these):
  * Pre-2010 TQQQ is a reconstruction, not reality. Treat that segment as a
    stress scenario, not a track record. The script labels it everywhere.
  * Tax model is a transparent approximation: lot-based, realises gains on each
    exit, nets losses within a year, applies tax at year-end paid FROM the
    account. It ignores wash-sale rules, multi-year loss carryforwards beyond a
    simple running balance, state tax (add via --state-rate), and assumes a
    fully taxable account. It is directional guidance, not a tax return.
  * Backtests are regime-dependent and curve-fittable. Default parameters are
    deliberately standard (200d / 252d) to limit overfitting.
  * Not investment advice.

USAGE
-----
  pip install yfinance pandas numpy matplotlib
  python tqqq_backtest.py                      # full run, 1999->today
  python tqqq_backtest.py --start 2010-02-11   # real-data-only (no simulation)
  python tqqq_backtest.py --no-plot            # skip charts
  python tqqq_backtest.py --st-rate 0.408 --lt-rate 0.238 --state-rate 0.0

Author: built for an audit-it-yourself workflow. Every number is reproducible.
"""

import argparse
import sys
import numpy as np
import pandas as pd

TRADING_DAYS = 252


# ============================================================================
# DATA
# ============================================================================
def download_data(start="1999-01-01", end=None, verbose=True):
    """Download QQQ, TQQQ, ^VIX, ^IRX (13wk T-bill yield) via yfinance.

    Returns a DataFrame indexed by date with columns:
      qqq, tqqq (may be NaN before 2010-02-11), vix (may be NaN), rf (annual decimal).
    Raises a clear error if the network/library is unavailable.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise SystemExit(
            "yfinance is not installed. Run:  pip install yfinance pandas numpy matplotlib"
        )

    def _close(ticker):
        # auto_adjust=True -> 'Close' is split/dividend adjusted (recent yfinance default)
        df = yf.download(ticker, start=start, end=end, auto_adjust=True,
                         progress=False, threads=False)
        if df is None or len(df) == 0:
            return None
        col = df["Close"]
        # yfinance sometimes returns a 1-col DataFrame; squeeze to Series
        if isinstance(col, pd.DataFrame):
            col = col.iloc[:, 0]
        col.name = ticker
        return col

    if verbose:
        print("Downloading market data (needs internet)...")

    qqq = _close("QQQ")
    if qqq is None:
        raise SystemExit("Could not download QQQ. Check your internet connection / yfinance version.")
    tqqq = _close("TQQQ")
    vix = _close("^VIX")
    irx = _close("^IRX")  # 13-week T-bill yield, in PERCENT (e.g. 5.0 == 5%)

    idx = qqq.index
    df = pd.DataFrame(index=idx)
    df["qqq"] = qqq
    df["tqqq"] = tqqq.reindex(idx) if tqqq is not None else np.nan
    df["vix"] = vix.reindex(idx) if vix is not None else np.nan

    if irx is not None:
        df["rf"] = (irx.reindex(idx) / 100.0).ffill()
    else:
        if verbose:
            print("  WARNING: ^IRX risk-free series unavailable; using constant 2% rf.")
        df["rf"] = 0.02
    df["rf"] = df["rf"].fillna(0.02).clip(lower=0.0)

    df = df.dropna(subset=["qqq"])
    if verbose:
        first_tqqq = df["tqqq"].first_valid_index()
        print(f"  QQQ history : {df.index[0].date()} -> {df.index[-1].date()} ({len(df)} days)")
        print(f"  TQQQ actual : from {None if first_tqqq is None else first_tqqq.date()}")
    return df


# ============================================================================
# TQQQ SIMULATION  (reconstruct 3x daily fund from QQQ)
# ============================================================================
def simulate_tqqq_returns(qqq_close, rf_annual, expense_ratio=0.0086,
                          financing_spread=0.005, leverage=3.0):
    """Daily returns of a synthetic 3x-DAILY leveraged fund built from QQQ.

    sim_daily = leverage * qqq_daily_return
                - expense_ratio/252                       (fund fee)
                - (leverage-1) * (rf + spread) / 252      (cost to borrow 2x)

    Returns a Series of daily returns aligned to qqq_close (first value NaN).
    """
    r = qqq_close.pct_change()
    daily_cost = (expense_ratio + (leverage - 1.0) * (rf_annual + financing_spread)) / TRADING_DAYS
    sim = leverage * r - daily_cost
    return sim


def build_tqqq_series(df, expense_ratio=0.0086, financing_spread=0.005,
                      leverage=3.0, verbose=True):
    """Splice ACTUAL TQQQ with SIMULATED pre-inception TQQQ.

    Adds columns:
      tqqq_full  : continuous price series (simulated before inception, actual after)
      simulated  : bool flag, True where the price is reconstructed
    Also returns a validation dict comparing sim vs actual over the overlap.
    """
    sim_ret = simulate_tqqq_returns(df["qqq"], df["rf"], expense_ratio,
                                    financing_spread, leverage)

    # Build a simulated price path with arbitrary base 1.0
    sim_price = (1.0 + sim_ret.fillna(0.0)).cumprod()

    actual = df["tqqq"]
    inception = actual.first_valid_index()

    validation = {}
    if inception is not None:
        # Scale simulated path so it meets the actual price at inception
        scale = actual.loc[inception] / sim_price.loc[inception]
        sim_price_scaled = sim_price * scale

        full = actual.copy()
        pre = df.index < inception
        full.loc[pre] = sim_price_scaled.loc[pre]
        simulated_flag = pd.Series(pre, index=df.index)

        # ---- validation over the overlap (both exist) ----
        ov = df.index >= inception
        a_ret = actual.loc[ov].pct_change().dropna()
        s_ret = sim_ret.loc[ov].reindex(a_ret.index)
        valid = pd.concat([a_ret, s_ret], axis=1).dropna()
        if len(valid) > 10:
            corr = float(np.corrcoef(valid.iloc[:, 0], valid.iloc[:, 1])[0, 1])
            te = float((valid.iloc[:, 0] - valid.iloc[:, 1]).std() * np.sqrt(TRADING_DAYS))
            ann_a = float((1 + valid.iloc[:, 0]).prod() ** (TRADING_DAYS / len(valid)) - 1)
            ann_s = float((1 + valid.iloc[:, 1]).prod() ** (TRADING_DAYS / len(valid)) - 1)
            validation = {"daily_return_corr": corr,
                          "ann_tracking_error": te,
                          "actual_ann_return_overlap": ann_a,
                          "sim_ann_return_overlap": ann_s,
                          "overlap_days": int(len(valid))}
    else:
        # No actual TQQQ at all -> everything simulated
        full = sim_price
        simulated_flag = pd.Series(True, index=df.index)

    df = df.copy()
    df["tqqq_full"] = full
    df["simulated"] = simulated_flag

    if verbose and validation:
        print("  Simulation validation vs ACTUAL TQQQ (overlap):")
        print(f"    daily-return correlation : {validation['daily_return_corr']:.4f}")
        print(f"    annualised tracking error: {validation['ann_tracking_error']*100:.2f}%")
        print(f"    actual vs sim ann return : {validation['actual_ann_return_overlap']*100:.1f}% "
              f"vs {validation['sim_ann_return_overlap']*100:.1f}%")
    return df, validation


# ============================================================================
# SIGNALS  (no look-ahead: signal at close t governs the position held into t+1)
# ============================================================================
def signal_sma_filter(qqq_close, window=200, buy_buffer=0.0, sell_buffer=0.0):
    """1.0 when QQQ above its SMA (+buffer), 0.0 when below (-buffer), else hold.

    buffers are fractions, e.g. 0.05 / 0.03 for a 5%/3% band.
    """
    sma = qqq_close.rolling(window).mean()
    upper = sma * (1 + buy_buffer)
    lower = sma * (1 - sell_buffer)
    sig = pd.Series(np.nan, index=qqq_close.index)
    sig[qqq_close > upper] = 1.0
    sig[qqq_close < lower] = 0.0
    sig = sig.ffill().fillna(0.0)
    return sig


def signal_sma_plus_momentum(qqq_close, window=200, mom_window=252,
                             buy_buffer=0.0, sell_buffer=0.0):
    """STRAT2: long only if price > SMA(+band) AND 12-month total return > 0."""
    sma_sig = signal_sma_filter(qqq_close, window=window,
                                buy_buffer=buy_buffer, sell_buffer=sell_buffer)
    mom = qqq_close / qqq_close.shift(mom_window) - 1.0
    mom_sig = (mom > 0).astype(float)
    return ((sma_sig > 0) & (mom_sig > 0)).astype(float)


# Named buffer presets (values are PERCENT: buy above SMA+x%, sell below SMA-y%)
BUFFER_PRESETS = {
    "raw": (0.0, 0.0),    # plain 200-day cross
    "5/3": (5.0, 3.0),    # buy +5% above MA, sell -3% below  (whipsaw-dampened)
    "8/5": (8.0, 5.0),    # wider band, fewer trades, later entries/exits
}


def parse_buffer_string(s):
    """'5/3,8/5,raw' -> [('5/3',(0.05,0.03)), ('8/5',(0.08,0.05)), ('raw',(0,0))].
    Accepts named presets or explicit 'buy/sell' percent pairs."""
    out = []
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok in BUFFER_PRESETS:
            b, se = BUFFER_PRESETS[tok]
            out.append((tok, (b / 100.0, se / 100.0)))
        else:
            b, se = tok.split("/")
            out.append((tok + "%", (float(b) / 100.0, float(se) / 100.0)))
    return out


def signal_vol_target(asset_ret, target_vol=0.40, lookback=20, max_w=1.0):
    """VOLTGT: weight = target_vol / realised_vol, capped at max_w."""
    rv = asset_ret.rolling(lookback).std() * np.sqrt(TRADING_DAYS)
    w = (target_vol / rv).clip(upper=max_w).fillna(0.0)
    return w


# ============================================================================
# BACKTEST ENGINE  (dollar walk, with realistic costs + lot-based tax)
# ============================================================================
def run_backtest(asset_ret, cash_ret, target_weight, dates,
                 start_capital=100_000.0, cost_bps=3.0,
                 st_rate=0.408, lt_rate=0.238, tax=True, binary_only=True):
    """Walk the portfolio day by day in dollars.

    asset_ret    : daily return of the risk asset (e.g. TQQQ)
    cash_ret     : daily return of cash when out of the asset
    target_weight: desired weight in the asset, decided at the PRIOR close
                   (this function shifts it internally to avoid look-ahead)
    Returns dict with gross & net equity Series, trade log, and metrics.

    Tax: lot-based, only meaningful when binary_only (weight is 0/1). For
    fractional weights (vol target) pass binary_only=False -> tax skipped.
    """
    w = target_weight.shift(1).fillna(0.0)  # position held during day t set at t-1 close
    w = w.reindex(asset_ret.index).fillna(0.0)

    n = len(asset_ret)
    gross = np.empty(n)
    net = np.empty(n)
    acct_gross = start_capital
    acct_net = start_capital

    # lot tracking for tax (net account)
    in_pos = False
    entry_val = None
    entry_date = None
    realized = {}  # year -> [st_gain, lt_gain]
    trades = []

    prev_w = 0.0
    a = asset_ret.values
    c = cash_ret.reindex(asset_ret.index).fillna(0.0).values
    wv = w.values
    idx = dates

    for t in range(n):
        wt = wv[t]
        # transaction cost on weight change (applied to both curves)
        turn = abs(wt - prev_w)
        cost = turn * cost_bps / 1e4
        day_ret = wt * a[t] + (1 - wt) * c[t] - cost
        if not np.isfinite(day_ret):
            day_ret = 0.0

        acct_gross *= (1 + day_ret)
        acct_net *= (1 + day_ret)

        # ---- tax bookkeeping on the NET account (binary strategies only) ----
        if tax and binary_only:
            # entry: crossing into the asset
            if prev_w < 0.5 <= wt:
                in_pos = True
                entry_val = acct_net
                entry_date = idx[t]
            # exit: crossing out of the asset
            elif prev_w >= 0.5 > wt and in_pos:
                gain = acct_net - entry_val
                hold_days = (idx[t] - entry_date).days
                yr = idx[t].year
                realized.setdefault(yr, [0.0, 0.0])
                if hold_days > 365:
                    realized[yr][1] += gain
                else:
                    realized[yr][0] += gain
                trades.append({"entry": entry_date, "exit": idx[t],
                               "hold_days": hold_days, "gain": gain,
                               "long_term": hold_days > 365})
                in_pos = False

            # year-end: settle tax, pay from the net account
            year_end = (t == n - 1) or (idx[t].year != idx[t + 1].year)
            if year_end:
                yr = idx[t].year
                st, lt = realized.get(yr, [0.0, 0.0])
                # losses offset gains within the year (simple netting)
                taxable_st = max(st, 0.0)
                taxable_lt = max(lt, 0.0)
                # if one bucket is a loss, let it offset the other (simplified)
                if st < 0:
                    taxable_lt = max(taxable_lt + st, 0.0)
                if lt < 0:
                    taxable_st = max(taxable_st + lt, 0.0)
                tax_due = taxable_st * st_rate + taxable_lt * lt_rate
                if tax_due > 0:
                    acct_net -= tax_due

        gross[t] = acct_gross
        net[t] = acct_net
        prev_w = wt

    gross_s = pd.Series(gross, index=idx)
    net_s = pd.Series(net, index=idx)
    strat_ret = pd.Series(np.r_[np.nan, np.diff(gross) / gross[:-1]], index=idx)

    return {"gross_equity": gross_s, "net_equity": net_s,
            "gross_ret": strat_ret, "weight": w, "trades": trades}


# ============================================================================
# METRICS
# ============================================================================
def cagr(equity):
    eq = equity.dropna()
    if len(eq) < 2:
        return np.nan
    days = (eq.index[-1] - eq.index[0]).days
    if days <= 0:
        return np.nan
    return (eq.iloc[-1] / eq.iloc[0]) ** (365.25 / days) - 1


def max_drawdown(equity):
    eq = equity.dropna()
    if len(eq) < 2:
        return np.nan
    roll_max = eq.cummax()
    dd = eq / roll_max - 1.0
    return dd.min()


def ann_vol(daily_ret):
    r = daily_ret.dropna()
    return r.std() * np.sqrt(TRADING_DAYS) if len(r) > 1 else np.nan


def sharpe(daily_ret, rf_daily):
    r = daily_ret.dropna()
    if len(r) < 2 or r.std() == 0:
        return np.nan
    rf = rf_daily.reindex(r.index).fillna(0.0)
    excess = r - rf
    return (excess.mean() / r.std()) * np.sqrt(TRADING_DAYS)


def sortino(daily_ret, rf_daily):
    r = daily_ret.dropna()
    if len(r) < 2:
        return np.nan
    rf = rf_daily.reindex(r.index).fillna(0.0)
    excess = r - rf
    downside = excess[excess < 0]
    dd = downside.std()
    if dd == 0 or np.isnan(dd):
        return np.nan
    return (excess.mean() / dd) * np.sqrt(TRADING_DAYS)


def calmar(equity):
    c = cagr(equity)
    mdd = max_drawdown(equity)
    if mdd is None or mdd == 0 or np.isnan(mdd):
        return np.nan
    return c / abs(mdd)


def summarize(name, result, rf_daily):
    gross = result["gross_equity"]
    net = result["net_equity"]
    gret = result["gross_ret"]
    w = result["weight"]
    n_trades = len(result["trades"])
    lt_trades = sum(1 for t in result["trades"] if t["long_term"])
    win_trades = sum(1 for t in result["trades"] if t["gain"] > 0)
    return {
        "strategy": name,
        "CAGR_gross": cagr(gross),
        "CAGR_net": cagr(net),
        "vol": ann_vol(gret),
        "Sharpe": sharpe(gret, rf_daily),
        "Sortino": sortino(gret, rf_daily),
        "Calmar": calmar(gross),
        "MaxDD": max_drawdown(gross),
        "time_in_mkt": float((w > 0).mean()),
        "n_trades": n_trades,
        "pct_LT_trades": (lt_trades / n_trades) if n_trades else np.nan,
        "trade_win_rate": (win_trades / n_trades) if n_trades else np.nan,
        "final_gross": gross.iloc[-1],
        "final_net": net.iloc[-1],
    }


# ============================================================================
# DRIVER
# ============================================================================
def run_all(df, args):
    df, validation = build_tqqq_series(
        df, expense_ratio=args.expense, financing_spread=args.fin_spread,
        leverage=args.leverage, verbose=not args.quiet)

    tqqq_ret = df["tqqq_full"].pct_change().fillna(0.0)
    qqq_ret = df["qqq"].pct_change().fillna(0.0)
    cash_ret = df["rf"] / TRADING_DAYS
    rf_daily = df["rf"] / TRADING_DAYS
    dates = df.index

    results = {}

    # Benchmarks: always invested (weight 1), tax deferred to the end (no exits)
    results["BH_QQQ"] = run_backtest(qqq_ret, cash_ret, pd.Series(1.0, index=dates),
                                     dates, args.capital, args.cost,
                                     args.st_rate, args.lt_rate, tax=True)
    results["BH_TQQQ"] = run_backtest(tqqq_ret, cash_ret, pd.Series(1.0, index=dates),
                                      dates, args.capital, args.cost,
                                      args.st_rate, args.lt_rate, tax=True)

    # STRAT1: 200d SMA filter (optional buffer)
    s1 = signal_sma_filter(df["qqq"], window=args.sma,
                           buy_buffer=args.buy_buffer, sell_buffer=args.sell_buffer)
    results["STRAT1_200dMA"] = run_backtest(tqqq_ret, cash_ret, s1, dates,
                                            args.capital, args.cost,
                                            args.st_rate, args.lt_rate, tax=True)

    # STRAT2: 200d SMA + 12m momentum confirmation (band applied to the SMA leg)
    s2 = signal_sma_plus_momentum(df["qqq"], window=args.sma, mom_window=args.mom,
                                  buy_buffer=args.buy_buffer, sell_buffer=args.sell_buffer)
    results["STRAT2_MA+Mom"] = run_backtest(tqqq_ret, cash_ret, s2, dates,
                                            args.capital, args.cost,
                                            args.st_rate, args.lt_rate, tax=True)

    # VOLTGT: gross only (fractional weights -> tax not modelled)
    sv = signal_vol_target(tqqq_ret, target_vol=args.target_vol)
    results["VOLTGT_gross"] = run_backtest(tqqq_ret, cash_ret, sv, dates,
                                           args.capital, args.cost,
                                           tax=False, binary_only=False)

    rows = [summarize(name, res, rf_daily) for name, res in results.items()]
    table = pd.DataFrame(rows).set_index("strategy")
    return df, results, table, validation


def compare_buffers(df, args, preset_list):
    """Run STRAT1 & STRAT2 across several buffer bands; return a comparison table.

    preset_list: output of parse_buffer_string, i.e. [(label,(buy_frac,sell_frac)),...]
    Lets the user 'play around' with bands in a single run.
    """
    df2, _ = build_tqqq_series(df, expense_ratio=args.expense,
                               financing_spread=args.fin_spread,
                               leverage=args.leverage, verbose=False)
    tqqq_ret = df2["tqqq_full"].pct_change().fillna(0.0)
    cash_ret = df2["rf"] / TRADING_DAYS
    rf_daily = df2["rf"] / TRADING_DAYS
    dates = df2.index

    rows = []
    for strat, sig_fn in [("STRAT1", signal_sma_filter),
                          ("STRAT2", signal_sma_plus_momentum)]:
        for label, (bb, sb) in preset_list:
            if strat == "STRAT1":
                sig = sig_fn(df2["qqq"], window=args.sma, buy_buffer=bb, sell_buffer=sb)
            else:
                sig = sig_fn(df2["qqq"], window=args.sma, mom_window=args.mom,
                             buy_buffer=bb, sell_buffer=sb)
            res = run_backtest(tqqq_ret, cash_ret, sig, dates, args.capital,
                               args.cost, args.st_rate, args.lt_rate, tax=True)
            s = summarize(f"{strat} [{label}]", res, rf_daily)
            rows.append(s)
    return pd.DataFrame(rows).set_index("strategy")


def fmt_table(table):
    t = table.copy()
    pct = ["CAGR_gross", "CAGR_net", "vol", "MaxDD", "time_in_mkt",
           "pct_LT_trades", "trade_win_rate"]
    for c in pct:
        if c in t:
            t[c] = (t[c] * 100).map(lambda x: f"{x:,.1f}%" if pd.notna(x) else "  -")
    for c in ["Sharpe", "Sortino", "Calmar"]:
        t[c] = t[c].map(lambda x: f"{x:,.2f}" if pd.notna(x) else "  -")
    for c in ["final_gross", "final_net"]:
        t[c] = t[c].map(lambda x: f"${x:,.0f}" if pd.notna(x) else "  -")
    t["n_trades"] = t["n_trades"].astype(int)
    return t


def main():
    p = argparse.ArgumentParser(description="TQQQ long-term strategy backtest")
    p.add_argument("--start", default="1999-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--capital", type=float, default=100_000.0)
    p.add_argument("--cost", type=float, default=3.0, help="round-trip cost in bps per unit turnover")
    p.add_argument("--sma", type=int, default=200)
    p.add_argument("--mom", type=int, default=252)
    p.add_argument("--buy-buffer", type=float, default=0.0,
                   help="buy when QQQ is this PERCENT above its SMA (e.g. 5 = +5%%; 0 = plain cross)")
    p.add_argument("--sell-buffer", type=float, default=0.0,
                   help="sell when QQQ is this PERCENT below its SMA (e.g. 3 = -3%%)")
    p.add_argument("--compare-buffers", type=str, default=None,
                   help="comma list of bands to compare, e.g. 'raw,5/3,8/5' or '10/7' (percent)")
    p.add_argument("--target-vol", type=float, default=0.40)
    p.add_argument("--leverage", type=float, default=3.0)
    p.add_argument("--expense", type=float, default=0.0086, help="TQQQ annual expense ratio (verify current)")
    p.add_argument("--fin-spread", type=float, default=0.005, help="borrowing spread over rf for the 2x debt")
    p.add_argument("--st-rate", type=float, default=0.408, help="short-term cap-gains rate (US top + NIIT)")
    p.add_argument("--lt-rate", type=float, default=0.238, help="long-term cap-gains rate (US top + NIIT)")
    p.add_argument("--state-rate", type=float, default=0.0)
    p.add_argument("--no-plot", action="store_true")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    args.st_rate += args.state_rate
    args.lt_rate += args.state_rate
    # user-facing buffers are PERCENT; convert to fractions for the engine
    args.buy_buffer /= 100.0
    args.sell_buffer /= 100.0

    df = download_data(args.start, args.end, verbose=not args.quiet)
    df, results, table, validation = run_all(df, args)

    print("\n" + "=" * 78)
    print("RESULTS  (gross = pre-tax; net = after-tax under your US rates)")
    band = "raw 200d cross" if args.buy_buffer == 0 and args.sell_buffer == 0 \
        else f"band +{args.buy_buffer*100:.0f}% / -{args.sell_buffer*100:.0f}%"
    print(f"Window: {df.index[0].date()} -> {df.index[-1].date()}  | "
          f"simulated pre-2010 segment: {int(df['simulated'].sum())} days  | "
          f"STRAT1/2 band: {band}")
    print("=" * 78)
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(fmt_table(table).to_string())
    print("\nNOTE: pre-2010 TQQQ is SIMULATED. Re-run with --start 2010-02-11 for real-data-only.")

    if args.compare_buffers:
        presets = parse_buffer_string(args.compare_buffers)
        cmp = compare_buffers(df.drop(columns=[c for c in ("tqqq_full", "simulated")
                                               if c in df.columns]), args, presets)
        print("\n" + "=" * 78)
        print(f"BUFFER COMPARISON  ({', '.join(l for l, _ in presets)})")
        print("Wider bands -> fewer trades & whipsaws, but later entries/exits.")
        print("=" * 78)
        with pd.option_context("display.max_columns", None, "display.width", 200):
            print(fmt_table(cmp).to_string())
        cmp.to_csv("tqqq_buffer_comparison.csv")
        print("Saved -> tqqq_buffer_comparison.csv")


    if not args.no_plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
            for name, res in results.items():
                ax[0].plot(res["gross_equity"], label=name, lw=1.2)
            ax[0].set_yscale("log"); ax[0].set_title("Gross equity (log scale)")
            ax[0].legend(loc="upper left", fontsize=8); ax[0].grid(alpha=0.3)
            for name in ["STRAT1_200dMA", "STRAT2_MA+Mom", "BH_TQQQ"]:
                eq = results[name]["gross_equity"]
                ax[1].plot(eq / eq.cummax() - 1, label=name, lw=1.0)
            ax[1].set_title("Drawdown"); ax[1].legend(loc="lower left", fontsize=8)
            ax[1].grid(alpha=0.3)
            fig.tight_layout(); fig.savefig("tqqq_backtest.png", dpi=120)
            print("\nSaved chart -> tqqq_backtest.png")
        except Exception as e:
            print(f"\n(Plot skipped: {e})")

    table.to_csv("tqqq_backtest_metrics.csv")
    print("Saved metrics -> tqqq_backtest_metrics.csv")


if __name__ == "__main__":
    main()
