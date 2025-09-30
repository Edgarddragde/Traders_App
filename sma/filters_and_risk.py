"""
filters_and_risk.py

Drop-in module for your IBKR project.

What it gives you:
- Indicator helpers: SMA(5/20), ATR(14), session VWAP (1-min or 5-min), slope
- Toggleable filter stack: VWAP alignment + momentum slope + index regime + ATR expansion
- Session guard: open window (09:30–11:00 ET) and power hour (15:00–16:00 ET)
- RiskManager: 2:1 reward:risk targets via bracket, 10% daily loss circuit breaker

How to use (inside your TradingApp):
    from filters_and_risk import (
        sma, atr, compute_vwap_from_bars, vwap_slope,
        slope, within_session, FilterConfig, FilterStack, RiskManager
    )

    # 1) Initialize once after account summary is ready
    self.risk = RiskManager(rr=2.0, daily_max_dd=0.10)

    # 2) Build filter config (you can tune thresholds)
    self.filter_cfg = FilterConfig(
        use_vwap=True,
        use_slope=True,
        use_index=True,          # SPY/QQQ regime filter (provide market slope)
        use_atr=True,
        atr_min_pct=0.005,       # 0.5% of price
        slope_lookback=10,
        vwap_lookback=5,
    )

    # 3) Each cycle/symbol, compute features from bar data then call
    ok, reasons = FilterStack(self.filter_cfg).check(
        side="LONG",            # or "SHORT"
        price=last_close,
        sma_fast=sma5,
        sma_slow=sma20,
        vwap=curr_vwap,
        vwap_trend=vwap_trend,
        mom_slope=mom_slope,
        index_slope=index_slope, # SPY/QQQ 15m SMA(20) slope
        atr=atr14,
    )
    if ok:
        entry = pick_entry()  # your existing epsilon+ snapshot logic
        stop, target = self.risk.rr_bracket(side="LONG", entry=entry, risk_pct=0.02)
        # place bracket...

Notes:
- Bars format expected here: list of tuples (date, open, high, low, close, volume) oldest->newest.
- Time windows assume US Eastern. Adjust if you store tz-aware times differently.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional
from datetime import datetime, time, timezone
import math
from zoneinfo import ZoneInfo


Bar = Tuple[object, float, float, float, float, float]  # (date, o, h, l, c, v)
NY = ZoneInfo("America/New_York")


# ----------------------
# Indicator primitives
# ----------------------

def sma(vals: List[float], n: int) -> Optional[float]:
    if n <= 0 or len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema(vals: List[float], n: int) -> Optional[float]:
    if n <= 0 or len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def slope(vals: List[float], n: int) -> float:
    n = min(n, len(vals))
    if n < 3:
        return 0.0
    xs = list(range(n))
    ys = vals[-n:]
    xbar = (n - 1) / 2
    ybar = sum(ys) / n
    num = sum((i - xbar) * (y - ybar) for i, y in enumerate(ys))
    den = sum((i - xbar) ** 2 for i in range(n))
    return num / den if den else 0.0


def true_range(prev_close: float, h: float, l: float) -> float:
    return max(h - l, abs(h - prev_close), abs(l - prev_close))


def atr_from_bars(bars: List[Bar], n: int = 14) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        _, _, h, l, c, _ = bars[i]
        _, _, _, _, pc, _ = bars[i - 1]
        trs.append(true_range(pc, h, l))
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n

# alias for clarity with config naming
atr = atr_from_bars


def compute_vwap_from_bars(bars: List[Bar]) -> List[Optional[float]]:
    """Session VWAP series from intraday bars.
    bars must be a single session (e.g., today's 1-min or 5-min) oldest->newest.
    """
    cum_pv = 0.0
    cum_vol = 0.0
    out: List[Optional[float]] = []
    for _, o, h, l, c, v in bars:
        tp = (h + l + c) / 3.0
        vol = float(v) if v is not None else 0.0
        cum_pv += tp * (vol or 0.0)
        cum_vol += (vol or 0.0)
        out.append((cum_pv / cum_vol) if cum_vol else None)
    return out


def vwap_slope(vwap_series: List[Optional[float]], k: int = 5) -> float:
    series = [x for x in vwap_series if x is not None]
    if len(series) < k:
        return 0.0
    return series[-1] - series[-k]


# ----------------------
# Session gating
# ----------------------


def within_session(now_dt: datetime | None = None) -> bool:
    """Open window (09:30-11:00 ET) or power hour (15:00-16:00 ET)."""
    now_dt = now_dt or datetime.now(tz=NY)
    # naive shift: convert UTC to ET using fixed offset; prefer pytz/zoneinfo in prod
    et_minutes = now_dt.hour * 60 + now_dt.minute
    def m(h, m):
        return h * 60 + m
    open_start, open_end = m(9, 30), m(11, 0)
    pwr_start, pwr_end = m(15, 0), m(16, 0)
    return (open_start <= et_minutes <= open_end) or (pwr_start <= et_minutes <= pwr_end)


# ----------------------
# Filters
# ----------------------

@dataclass
class FilterConfig:
    use_vwap: bool = True
    use_slope: bool = True
    use_index: bool = True
    use_atr: bool = True
    atr_min_pct: float = 0.005  # 0.5% of price
    slope_lookback: int = 10
    vwap_lookback: int = 5
    
    vwap_eps: float = 1e-4 # treat equal price/vwap as aligned
    sma_eps: float = 1e-4 # treat small fast-slow diffs as equal
    min_slope: float = 1e-4 # minimum momentum slope magnitude
    min_idx_slope: float = 5e-4 # minimum index slope to call it trending


class FilterStack:
    def __init__(self, cfg: FilterConfig):
        self.cfg = cfg

    def check(self, side: str, price: float, 
    sma_fast: Optional[float], sma_slow: Optional[float], 
    vwap: Optional[float] = None, vwap_trend: Optional[float] = None, 
    mom_slope: Optional[float] = None, index_slope: Optional[float] = None, atr: Optional[float] = None,
    ) -> Tuple[bool, List[str]]:
        """Return (ok, reasons). reasons include passes and blocks for logging."""
        reasons: List[str] = []
        side = side.upper()

        # Base signal: SMA cross consistency w/ tolerance
        if sma_fast is None or sma_slow is None:
            return False, ["no SMA data"]
        diff = sma_fast - sma_slow
        base_ok = (diff > self.cfg.sma_eps) if side == "LONG" else (diff < -self.cfg.sma_eps)
        if not base_ok:
            return False, [f"SMA base mismatch: fast={sma_fast:.4f} slow={sma_slow:.4f} side={side}"]
        reasons.append("SMA base ok")

        # VWAP alignment w/ EPS and non-negative/positive trend tolerance
        if self.cfg.use_vwap and vwap is not None:
            if side == "LONG" and not (price >= (vwap - self.cfg.vwap_eps) and (vwap_trend or 0) >= -self.cfg.vwap_eps):
                return False, [f"VWAP block: price={price:.4f} vwap={vwap:.4f} trend={vwap_trend:.5f}"]
            if side == "SHORT" and not ((price < (vwap + self.cfg.vwap_eps)) and (vwap_trend or 0) <= self.cfg.vwap_eps):
                return False, [f"VWAP block: price={price:.4f} vwap={vwap:.4f} trend={vwap_trend:.5f}"]
            reasons.append("VWAP ok")

        # Momentum slope (close-based) w/ minimum magnitude
        if self.cfg.use_slope and mom_slope is not None:
            if side == "LONG" and mom_slope < self.cfg.min_slope:
                return False, [f"Slope block: slope={mom_slope:.5f} < {self.cfg.min_slope:0.5f}"]
            if side == "SHORT" and mom_slope > -self.cfg.min_slope:
                return False, [f"Slope block: slope={mom_slope:.5f} > -{self.cfg.min_slope:0.5f}"]
            reasons.append("Slope ok")

        # Index regime filter (don't block if near zero) 
        if self.cfg.use_index and index_slope is not None:
            if abs(index_slope) < self.cfg.min_idx_slope:
                reasons.append("Index neutral")
            else:
                if side == "LONG" and index_slope <= 0:
                    return False, [f"Index block: slope={index_slope:.5f}"]
                if side == "SHORT" and index_slope >= 0:
                    return False, [f"Index block: slope={index_slope:.5f}"]
                reasons.append("Index ok")

        # ATR expansion filter
        if self.cfg.use_atr and atr is not None and price:
            if (atr / price) < self.cfg.atr_min_pct:
                return False, [f"ATR block: atr/price={(atr/price):.3%} < {self.cfg.atr_min_pct:.2%}"]
            reasons.append("ATR ok")

        return True, reasons


# ----------------------
# Risk manager
# ----------------------

@dataclass
class RiskManager:
    rr: float = 2.0                 # reward:risk target (e.g., 2.0 => TP = entry ± rr*risk)
    daily_max_dd: float = 0.10      # 10% daily loss cap
    start_equity: Optional[float] = None
    _risk_day = None  # add to __init__

    def set_start_equity(self, net_liq: float):
        if self.start_equity is None and net_liq:
            self.start_equity = float(net_liq)

    def hit_daily_stop(self, curr_net_liq: float) -> bool:
        if not self.start_equity or not curr_net_liq:
            return False
        dd = (self.start_equity - curr_net_liq) / self.start_equity
        return dd >= self.daily_max_dd

    def rr_bracket(self, side: str, entry: float, risk_pct: float) -> Tuple[float, float]:
        """Return (stop, target) based on entry and percent risk to stop, with rr target."""
        side = side.upper()
        if side == "LONG":
            stop = round(entry * (1 - risk_pct), 2)
            target = round(entry * (1 + risk_pct * self.rr), 2)
        else:
            stop = round(entry * (1 + risk_pct), 2)
            target = round(entry * (1 - risk_pct * self.rr), 2)
        return stop, target

# ----------------------
# Utility extractors for bars
# ----------------------

def closes_from_bars(bars: List[Bar]) -> List[float]:
    return [b[4] for b in bars]


def highs_from_bars(bars: List[Bar]) -> List[float]:
    return [b[2] for b in bars]


def lows_from_bars(bars: List[Bar]) -> List[float]:
    return [b[3] for b in bars]


def volumes_from_bars(bars: List[Bar]) -> List[float]:
    return [b[5] for b in bars]
