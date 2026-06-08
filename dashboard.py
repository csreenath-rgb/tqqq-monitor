"""
dashboard.py  -  regenerate index.html (static dashboard served by GitHub Pages).

Pure string templating, no framework. Reads the computed states/levels and the
history.csv log. Shows current position per strategy, key levels, trailing GROSS
metrics, and recent history. Gross is labelled explicitly; after-tax numbers come
from the full backtest script, not the daily monitor.
"""
import os
import datetime as dt
import pandas as pd

HISTORY_FILE = "history.csv"


def _pct(x, signed=False):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "&mdash;"
    return f"{x*100:+.1f}%" if signed else f"{x*100:.1f}%"


def _num(x, d=2):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "&mdash;"
    return f"{x:,.{d}f}"


def _card(name, st):
    on = st["position"] == "TQQQ"
    color = "#1a7f37" if on else "#9a6700"
    badge = "RISK-ON  ·  TQQQ" if on else "RISK-OFF  ·  T-BILLS"
    m = st["metrics"]
    return f"""
    <div class="card">
      <div class="card-h">
        <span class="name">{name}</span>
        <span class="badge" style="background:{color}">{badge}</span>
      </div>
      <div class="since">Holding since <b>{st['since']}</b></div>
      <table class="metrics">
        <tr><td>Trailing CAGR</td><td>{_pct(m['cagr'])}</td></tr>
        <tr><td>Max drawdown</td><td>{_pct(m['mdd'])}</td></tr>
        <tr><td>Sharpe</td><td>{_num(m['sharpe'])}</td></tr>
        <tr><td>Calmar</td><td>{_num(m['calmar'])}</td></tr>
      </table>
      <div class="winnote">gross, trailing ~{m['window_days']} sessions</div>
    </div>"""


def _history_rows(n=15):
    if not os.path.exists(HISTORY_FILE):
        return "<tr><td colspan='5'>No history yet.</td></tr>"
    df = pd.read_csv(HISTORY_FILE).tail(n).iloc[::-1]
    rows = []
    for _, r in df.iterrows():
        flag = "🔔" if str(r.get("alerted")) == "True" else ""
        rows.append(
            f"<tr><td>{r['data_date']}</td><td>{_num(r['qqq_close'])}</td>"
            f"<td>{_num(r['sma200'])}</td><td>{r['STRAT1_pos']}</td>"
            f"<td>{r['STRAT2_pos']} {flag}</td></tr>")
    return "\n".join(rows)


def render(states, levels, out_file="index.html"):
    cards = "\n".join(_card(n, s) for n, s in states.items())
    pct_vs = _pct(levels["pct_vs_sma"], signed=True)
    mom = _pct(levels["mom_12m"], signed=True)
    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TQQQ Strategy Monitor</title>
<style>
  :root {{ font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  body {{ margin:0; background:#f6f8fa; color:#1f2328; }}
  .wrap {{ max-width:880px; margin:0 auto; padding:24px 16px 64px; }}
  h1 {{ font-size:22px; margin:0 0 2px; }}
  .sub {{ color:#656d76; font-size:13px; margin-bottom:20px; }}
  .levels {{ display:flex; gap:18px; flex-wrap:wrap; background:#fff; border:1px solid #d0d7de;
             border-radius:10px; padding:14px 18px; margin-bottom:18px; font-size:14px; }}
  .levels b {{ font-size:18px; display:block; }}
  .cards {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media (max-width:640px) {{ .cards {{ grid-template-columns:1fr; }} }}
  .card {{ background:#fff; border:1px solid #d0d7de; border-radius:10px; padding:16px 18px; }}
  .card-h {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }}
  .name {{ font-weight:700; font-size:16px; }}
  .badge {{ color:#fff; font-size:11px; font-weight:700; padding:4px 9px; border-radius:20px; }}
  .since {{ color:#656d76; font-size:13px; margin-bottom:10px; }}
  table.metrics {{ width:100%; border-collapse:collapse; font-size:14px; }}
  table.metrics td {{ padding:5px 0; border-bottom:1px solid #f0f1f3; }}
  table.metrics td:last-child {{ text-align:right; font-variant-numeric:tabular-nums; font-weight:600; }}
  .winnote {{ color:#8c959f; font-size:11px; margin-top:8px; }}
  h2 {{ font-size:15px; margin:26px 0 8px; }}
  table.hist {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid #d0d7de;
               border-radius:10px; overflow:hidden; font-size:13px; }}
  table.hist th, table.hist td {{ padding:8px 12px; text-align:left; border-bottom:1px solid #f0f1f3; }}
  table.hist th {{ background:#f6f8fa; font-weight:600; }}
  .disc {{ color:#8c959f; font-size:11px; margin-top:24px; line-height:1.5; }}
</style></head>
<body><div class="wrap">
  <h1>TQQQ Strategy Monitor</h1>
  <div class="sub">Data through close <b>{levels['date']}</b> &middot; band: <b>{levels.get('band','raw 200d cross')}</b> &middot; updated {dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC</div>

  <div class="levels">
    <div>QQQ close<b>{_num(levels['qqq_close'])}</b></div>
    <div>200-day avg<b>{_num(levels['sma200'])}</b></div>
    <div>vs 200d avg<b>{pct_vs}</b></div>
    <div>12-mo return<b>{mom}</b></div>
  </div>

  <div class="cards">{cards}</div>

  <h2>Recent history</h2>
  <table class="hist">
    <tr><th>Date</th><th>QQQ</th><th>200d avg</th><th>STRAT1</th><th>STRAT2</th></tr>
    {_history_rows()}
  </table>

  <div class="disc">
    STRAT1 = hold TQQQ when QQQ is above its 200-day average, else T-bills.
    STRAT2 = STRAT1 plus a positive trailing-12-month filter (trades less).
    Metrics shown are <b>gross of tax</b> over the trailing window; after-tax
    figures come from the full backtest. Signals are mechanical and act on the
    next session. This dashboard is informational only and is <b>not investment
    advice</b>; leveraged ETFs can lose nearly all value in a sustained decline.
  </div>
</div></body></html>"""
    with open(out_file, "w") as f:
        f.write(html)
    return out_file
