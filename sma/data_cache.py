from collections import defaultdict, deque

class BarCache:
    def __init__(self, maxlen=1000):
        self.bars_5m = defaultdict(lambda: deque(maxlen=maxlen))
        self.bars_1m_today = defaultdict(lambda: deque(maxlen=maxlen))
        self.spy_15m = deque(maxlen=maxlen)
    
    
    def add(self, key, timeframe, bar):
        if timeframe == "5m":
            self.bars_5m[key].append(bar)
        elif timeframe == "1m_today":
            self.bars_1m_today[key].append(bar)
        elif timeframe == "spy_15m":
            self.spy_15m.append(bar)