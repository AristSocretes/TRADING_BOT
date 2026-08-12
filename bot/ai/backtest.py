import numpy as np
import pandas as pd

from bot.ai.env import POSITION_LEVELS


def backtest(df, signals, spread=0.0002, slippage=0.0, initial_equity=10000.0):
    equity = initial_equity
    curve = []
    position = 0
    entry_price = 0.0
    trades = []
    for i in range(len(df)):
        price = float(df["close"].iloc[i])
        target = int(signals[i])
        if target != position:
            if position != 0:
                exit_cost = (spread / 2 + slippage) * equity
                gross = (price / entry_price - 1) * position * equity
                pnl = gross - exit_cost
                equity += pnl
                trades.append(pnl)
                position = 0
            if target != 0:
                equity -= (spread / 2 + slippage) * equity
                position = target
                entry_price = price
        curve.append(equity)
    return pd.Series(curve, index=df.index), pd.Series(trades, dtype=float)


def metrics(curve, trades, periods_per_year=10080):
    returns = curve.pct_change().dropna()
    total_return = curve.iloc[-1] / curve.iloc[0] - 1
    std = returns.std()
    sharpe = returns.mean() / std * np.sqrt(periods_per_year) if std > 0 else 0.0
    drawdown = curve / curve.cummax() - 1
    wins = trades[trades > 0]
    losses = trades[trades <= 0]
    gross_wins = wins.sum()
    gross_losses = abs(losses.sum())
    profit_factor = gross_wins / gross_losses if gross_losses != 0 else float("inf")
    return {
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
        "n_trades": int(len(trades)),
        "win_rate": float(len(wins) / len(trades)) if len(trades) else 0.0,
        "profit_factor": float(profit_factor),
    }


def rl_backtest(
    df,
    model,
    window=60,
    spread=0.0004,
    slippage=0.00005,
    sl_frac=0.01,
    trade_penalty=0.0,
    align_bonus=0.0,
    entry_gate=0.0,
    feature_stats=None,
    sup_probs=None,
    cross_asset_dfs=None,
    features_arr=None,
    feature_columns=None,
):
    from bot.ai.env import ForexTradingEnv

    env = ForexTradingEnv(
        df,
        window=window,
        episode_len=len(df),
        spread=spread,
        slippage=slippage,
        sl_frac=sl_frac,
        trade_penalty=trade_penalty,
        align_bonus=align_bonus,
        feature_stats=feature_stats,
        sup_probs=sup_probs,
        cross_asset_dfs=cross_asset_dfs,
        features_arr=features_arr,
        feature_columns=feature_columns,
    )
    obs, _ = env.reset(options={"start_idx": window})
    closes = df["close"].to_numpy(dtype=np.float64)
    curve = []
    trades = []
    prev_pos = 0.0
    snapshot = env.start_equity
    n_steps = len(df) - window - 1
    for i in range(n_steps):
        action, _ = model.predict(obs, deterministic=True)
        target = POSITION_LEVELS[int(action)]
        # Entry gate: no new positions from flat while the 60-bar move is weak
        if entry_gate > 0.0 and prev_pos == 0.0 and target != 0.0:
            ret60 = closes[window + i] / closes[i] - 1
            if abs(ret60) < entry_gate:
                target = 0.0
                action = int(np.where(POSITION_LEVELS == target)[0][0])
        obs, _, terminated, _, _ = env.step(action)
        pos = float(env.position)
        if prev_pos != 0.0 and (pos == 0.0 or np.sign(pos) != np.sign(prev_pos)):
            trades.append(float(env.equity - snapshot))
        if prev_pos == 0.0 and pos != 0.0:
            snapshot = float(env.equity)
        if pos != 0.0 and prev_pos != 0.0 and pos != prev_pos:
            trades.append(float(env.equity - snapshot))
            snapshot = float(env.equity)
        prev_pos = pos
        curve.append(float(env.equity))
        if terminated:
            break
    idx = df.index[window : window + len(curve)]
    return pd.Series(curve, index=idx), pd.Series(trades, dtype=float)


def rl_walk_forward(df, model, n_splits=4, test_size=0.2, **env_kwargs):
    n = len(df)
    n_test = int(n * test_size)
    start_test = n - n_splits * n_test
    reports = []
    curves = []
    for fold in range(n_splits):
        fold_start = start_test + fold * n_test
        fold_end = start_test + (fold + 1) * n_test
        test_df = df.iloc[fold_start:fold_end]
        if len(test_df) < 2:
            continue
        curve, trades = rl_backtest(test_df, model, **env_kwargs)
        reports.append({"fold": fold, **metrics(curve, trades)})
        curves.append(curve)
    return reports, curves


def walk_forward(df, predict_fn, n_splits=4, test_size=0.2, spread=0.0002, slippage=0.0):
    n = len(df)
    n_test = int(n * test_size)
    start_test = n - n_splits * n_test
    reports = []
    curves = []
    for fold in range(n_splits):
        fold_start = start_test + fold * n_test
        fold_end = start_test + (fold + 1) * n_test
        train_df = df.iloc[:fold_start]
        test_df = df.iloc[fold_start:fold_end]
        if len(train_df) < 2000 or len(test_df) < 2:
            continue
        signals = predict_fn(train_df, test_df)
        curve, trades = backtest(test_df, signals, spread=spread, slippage=slippage)
        reports.append({"fold": fold, **metrics(curve, trades)})
        curves.append(curve)
    return reports, curves
