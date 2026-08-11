import argparse
import base64
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from config import settings  # noqa: E402

FREQ = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}


def load_run(run_dir: Path):
    reports = json.loads((run_dir / "reports.json").read_text())
    curves = []
    for report in reports:
        label = str(report.get("fold", "?"))
        curve_path = run_dir / f"fold{label}_curve.npy"
        curve_path = curve_path if curve_path.exists() else next(
            run_dir.glob(f"fold{label}_*.npy"), None
        )
        values = np.load(curve_path) if curve_path else np.array([])
        curves.append(pd.Series(values, name=f"fold {label}"))
    meta = {}
    meta_path = run_dir / "run_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    return reports, curves, meta


def timestamp_axis(report, n, granularity):
    if n < 1:
        return np.arange(n)
    try:
        start = pd.Timestamp(report["fold_start"])
        end = pd.Timestamp(report["fold_end"])
        idx = pd.date_range(start, end, periods=n, freq=FREQ.get(granularity, "5min"))
    except Exception:
        idx = np.arange(n)
    return idx


def equity_figure(folds, granularity):
    n_folds = len(folds)
    fig, axes = plt.subplots(n_folds, 1, figsize=(11, 3.2 * max(n_folds, 1)), sharex=False)
    if n_folds == 1:
        axes = [axes]
    for ax, (report, curve) in zip(axes, folds):
        idx = timestamp_axis(report, len(curve), granularity)
        start_val = curve.iloc[0] if len(curve) else 1.0
        norm = curve / start_val
        ax.plot(idx, norm, lw=1.0, color="#1f77b4")
        ax.axhline(1.0, color="#888", lw=0.7, ls="--")
        ax.set_title(
            f"Fold {report['fold']}  {report['fold_start'][:10]} -> {report['fold_end'][:10]}"
            f"   Sharpe {report['sharpe']:.2f}   Ret {report['total_return']*100:.1f}%"
            f"   Buy&Hold {report['buy_hold']*100:.1f}%",
            fontsize=9,
        )
        ax.grid(alpha=0.25)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def render_table(reports):
    rows = []
    for r in reports:
        rows.append(
            f"""<tr>
            <td>{r.get('fold')}</td>
            <td>{r['fold_start'][:10]} / {r['fold_end'][:10]}</td>
            <td class="num">{r['total_return']*100:.1f}%</td>
            <td class="num">{r['sharpe']:.2f}</td>
            <td class="num">{r['max_drawdown']*100:.1f}%</td>
            <td class="num">{r['n_trades']}</td>
            <td class="num">{r['win_rate']*100:.1f}%</td>
            <td class="num">{r['profit_factor']:.2f}</td>
            <td class="num">{r['buy_hold']*100:.1f}%</td>
            <td class="num">{r['bull_frac']*100:.0f}%</td>
            </tr>"""
        )
    sharpe = [r["sharpe"] for r in reports]
    ret = [r["total_return"] for r in reports]
    wins = sum(1 for s in sharpe if s > 0)
    rows.append(
        f"""<tr class="mean">
        <td>mean</td><td>—</td>
        <td class="num">{np.mean(ret)*100:.1f}%</td>
        <td class="num">{np.mean(sharpe):.2f}</td>
        <td class="num">{np.mean([r['max_drawdown'] for r in reports])*100:.1f}%</td>
        <td class="num">{int(np.mean([r['n_trades'] for r in reports]))}</td>
        <td class="num">{np.mean([r['win_rate'] for r in reports])*100:.1f}%</td>
        <td class="num">{np.mean([r['profit_factor'] for r in reports]):.2f}</td>
        <td class="num">{np.mean([r['buy_hold'] for r in reports])*100:.1f}%</td>
        <td class="num">—</td>
        </tr>"""
    )
    verdict = go_no_go(reports, wins)
    return "\n".join(rows), verdict, wins


def go_no_go(reports, wins):
    n = len(reports)
    mean_sharpe = float(np.mean([r["sharpe"] for r in reports]))
    beats_bh = sum(1 for r in reports if r["total_return"] > r["buy_hold"])
    if n == 0:
        return "No folds — nothing evaluated.", "neutral"
    if wins == n and mean_sharpe > 0.5:
        return (
            f"GO toward paper trial (Phase 4): all {n} folds positive, "
            f"mean Sharpe {mean_sharpe:.2f}, beats buy&hold in {beats_bh}/{n} folds.",
            "go",
        )
    if wins >= max(n - 1, 1) and mean_sharpe > 0:
        return (
            f"PROCEED WITH CAUTION: {wins}/{n} folds positive, mean Sharpe {mean_sharpe:.2f}. "
            "Paper-trade at minimum size and re-evaluate.",
            "warn",
        )
    return (
        f"NO-GO: only {wins}/{n} folds positive, mean Sharpe {mean_sharpe:.2f}. "
        "Per PLAN.md B.6 this is a valid result — return to Phase 2 (retrain/reward/features) "
        "before any paper run.",
        "nog",
    )


def build_html(run_dir, comparison=None, granularity="5m"):
    reports, curves, meta = load_run(run_dir)
    folds = [(r, c) for r, c in zip(reports, curves)]
    img = equity_figure(folds, granularity)
    rows, verdict, wins = render_table(reports)

    comp_html = ""
    comp_img = ""
    if comparison:
        creports, ccurves, cmeta = load_run(comparison)
        cfolds = [(r, c) for r, c in zip(creports, ccurves)]
        comp_img = equity_figure(cfolds, granularity)
        crows, cverdict, cwins = render_table(creports)
        comp_html = (
            f"<h2>Comparison run: {comparison.name}</h2>"
            f"<p class='tag {cverdict}'>Verdict: {cverdict}</p>"
            f"<table><thead><tr><th>Fold</th><th>Period</th><th>Return</th><th>Sharpe</th>"
            f"<th>MaxDD</th><th>Trades</th><th>Win%</th><th>PF</th><th>Buy&Hold</th><th>Bull%</th>"
            f"</tr></thead><tbody>{crows}</tbody></table>"
            f"<img src='data:image/png;base64,{comp_img}' alt='comparison equity curves' />"
        )

    m = meta or {}
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Walk-Forward Report — {run_dir.name}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem auto;
         max-width: 980px; color: #222; }}
  h1 {{ font-size: 1.4rem; margin-bottom: .2rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
  .sub {{ color: #666; font-size: .85rem; margin-bottom: 1.2rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .82rem; }}
  th, td {{ border: 1px solid #e0e0e0; padding: 4px 8px; text-align: left; }}
  th {{ background: #f4f4f4; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .mean td {{ font-weight: 600; background: #fafafa; }}
  .tag {{ display: inline-block; padding: 4px 10px; border-radius: 4px; font-weight: 600; }}
  .go {{ background: #d4edda; color: #155724; }}
  .warn {{ background: #fff3cd; color: #856404; }}
  .nog {{ background: #f8d7da; color: #721c24; }}
  .neutral {{ background: #e2e3e5; color: #383d41; }}
  img {{ width: 100%; margin-top: 1rem; }}
</style>
</head>
<body>
<h1>Walk-Forward Report — {run_dir.name}</h1>
  <p class="sub">generated {pd.Timestamp.now('UTC').strftime('%Y-%m-%d %H:%M UTC')}
 | symbol {m.get('symbol', 'BTCUSDT')} | window {m.get('window', '?')}
 | timesteps {m.get('timesteps', '?')} | seed {m.get('seed', '?')}
 | entry_gate {m.get('entry_gate', '?')}</p>
<p class="tag {verdict}">Verdict: {verdict}</p>
<table>
<thead><tr><th>Fold</th><th>Period</th><th>Return</th><th>Sharpe</th><th>MaxDD</th>
<th>Trades</th><th>Win%</th><th>PF</th><th>Buy&Hold</th><th>Bull%</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<h2>Equity curves (normalized)</h2>
<img src='data:image/png;base64,{img}' alt='equity curves' />
{comp_html}
</body>
</html>"""
    return html


def main():
    parser = argparse.ArgumentParser(
        description="Render walk-forward results into results/report.html"
    )
    parser.add_argument("--run-dir", default="models/wf_final")
    parser.add_argument(
        "--compare",
        default=None,
        help="Second run dir to render below the primary (optional)",
    )
    parser.add_argument("--granularity", default=settings.GRANULARITY)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not (run_dir / "reports.json").exists():
        print(f"reports.json not found in {run_dir}")
        return
    comparison = Path(args.compare) if args.compare else None
    html = build_html(run_dir, comparison, args.granularity)
    out = settings.RESULTS_DIR / "report.html"
    out.write_text(html)
    print(out)
    reports, _, _ = load_run(run_dir)
    _, verdict, _ = render_table(reports)
    print(verdict)


if __name__ == "__main__":
    main()
