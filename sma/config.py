DELAYED_DATA_TYPE = 3 # 1=REALTIME, 3=DELAYED
HOST = "127.0.0.1"; PORT = 7497; CLIENT_ID = 0

# UNIVERSE / SCANNER
SCANNER_TOP = 15
MIN_PRICE = 5
MIN_VOL = 500_000
ALLOW_TRADING_OUTSIDE_SESSION = True # flip to True for testing


# SESSIONS (ET)
OPEN_START = (9, 30); OPEN_END = (11, 0)
PWR_START = (15, 0); PWR_END = (16, 0)

# SIGNALS & FILTERS
SMA_FAST = 5; SMA_SLOW = 20
SLOPE_LOOKBACK = 10
VWAP_SLOPE_LOOKBACK = 8
USE_INDEX = True # SPY filter
USE_ATR = True
ATR_MIN_PCT = 0.003 # 0.03%
FALLBACK_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "AMD", "TSLA", "GOOGL", "QQQ", "SPY"]

# Tolerances
VWAP_EPS = 1e-4
SMA_EPS = 1e-4
MIN_SLOPE = 1e-4
MIN_IDX_SLOPE = 5e-4

# RISK
RISK_PCT = 0.02 # stop distance vs entry (2%)
RR = 2.0 # reward:risk target
DAILY_MAX_DD = 0.10 # 10% daily loss stop
ENTRY_QTY = "10"
PRICE_EPS = 0.01 # tick add/sub for marketable limit

# data bars to request
INTRA_5M_DUR = "2 D"
INTRA_1M_DUR = "1800 S"
SPY_15M_DUR = "5 D"