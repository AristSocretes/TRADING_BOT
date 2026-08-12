from bot.ai.signal import SignalGenerator
from bot.data.cache import DataCache
from bot.data.features import add_features

models = [
    "models/ppo_btc_cdl.zip",
    "models/sweep/BTCUSDT_5m.zip",
    "models/prod/BTCUSDT_5m.zip",
]
cache = DataCache()
df = cache.load("BTCUSDT", "5m")
df = df.tail(2000)
features = add_features(df).dropna()
print(f"Features shape: {features.shape}")
for m in models:
    try:
        sg = SignalGenerator(m, entry_gate=0.0)
        print(f"{m}: window={sg.window}, n_feat={sg.n_feat}")
        result = sg.predict(features)
        print(f"{m}: signal={result['signal']} confidence={result['confidence']:.3f} trend={result['trend']:.4f}")
    except Exception as e:
        print(f"{m}: ERROR {e}")
