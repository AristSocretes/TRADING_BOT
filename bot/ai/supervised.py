from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import accuracy_score

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

from bot.data.features import normalized_frame


def make_supervised_target(df, horizon=1, threshold=0.002):
    """Create binary target: 1=up, 0=down over horizon bars (excludes flat)."""
    log_ret = np.log(df["close"]).diff(horizon).shift(-horizon)
    target = np.zeros(len(df), dtype=int)
    target[log_ret > threshold] = 1
    target[log_ret < -threshold] = 0
    return target


def prepare_supervised_data(df, window=30, horizon=4, threshold=0.002,
                            feature_stats=None, cross_asset_dfs=None):
    """Prepare features and targets for supervised learning."""
    feats = normalized_frame(df, stats=feature_stats, cross_asset_dfs=cross_asset_dfs)
    target = make_supervised_target(df, horizon=horizon, threshold=threshold)
    
    X, y = [], []
    for i in range(window, len(df) - horizon):
        if target[i] in (0, 1):  # only up/down
            X.append(feats.iloc[i - window:i].values.reshape(-1))
            y.append(target[i])
    return np.array(X), np.array(y)


def train_supervised_model(train_df, val_df, window=30, horizon=4, threshold=0.002,
                           feature_stats=None, cross_asset_dfs=None):
    """Train XGBoost classifier for next-bar direction (binary: up/down)."""
    if not HAS_XGB:
        raise ImportError("xgboost not installed. Run: pip install xgboost")
    
    X_train, y_train = prepare_supervised_data(
        train_df, window, horizon, threshold, feature_stats, cross_asset_dfs)
    X_val, y_val = prepare_supervised_data(
        val_df, window, horizon, threshold, feature_stats, cross_asset_dfs)
    
    if len(X_train) < 100 or len(X_val) < 20:
        raise ValueError("Insufficient supervised samples")
    
    # Check class balance
    train_pos = (y_train == 1).mean()
    val_pos = (y_val == 1).mean()
    print(f"  class balance train: {train_pos:.3f} up, val: {val_pos:.3f} up")
    
    clf = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="binary:logistic",
        eval_metric="logloss",
        n_jobs=-1,
        random_state=42,
        early_stopping_rounds=30,
    )
    
    clf.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )
    
    val_pred = (clf.predict_proba(X_val)[:, 1] > 0.5).astype(int)
    acc = accuracy_score(y_val, val_pred)
    return clf, acc


def save_supervised_model(model, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load_supervised_model(path):
    return joblib.load(path)


def supervised_probs(model, df, window=30, feature_stats=None, cross_asset_dfs=None):
    """Get predicted probability of UP for each bar."""
    feats = normalized_frame(df, stats=feature_stats, cross_asset_dfs=cross_asset_dfs)
    probs = np.zeros((len(df), 2), dtype=np.float32)
    for i in range(window, len(df)):
        x = feats.iloc[i - window:i].values.reshape(1, -1)
        p_up = model.predict_proba(x)[0, 1]
        probs[i] = [1 - p_up, p_up]  # [p_down, p_up]
    return probs  # [p_down, p_up]


def evaluate_supervised(model, df, window=30, horizon=4, threshold=0.001, feature_stats=None):
    X, y = prepare_supervised_data(df, window, horizon, threshold, feature_stats)
    if len(X) == 0:
        return 0.0
    pred = model.predict(X)
    return accuracy_score(y + 1, pred)