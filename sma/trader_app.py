# app_trader.py
import time, threading
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict, deque
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.utils import Decimal
from ibapi.scanner import ScannerSubscription
from ibapi.tag_value import TagValue

from filters_and_risk import (
    sma, atr, compute_vwap_from_bars, vwap_slope, slope,
    within_session, FilterConfig, FilterStack, RiskManager,
)

from execution_modes import (
    place_resting_limit_bracket,
    place_stoplimit_bracket,
    place_pricecondition_limit_bracket,
)
from data_cache import BarCache
import config as C
NY = ZoneInfo("America/New_York")

def mk_stock(symbol: str) -> Contract:
    ct = Contract(); ct.symbol=symbol; ct.secType="STK"; ct.currency="USD"; ct.exchange="SMART"
    return ct

def mk_bracket(parent_id, action, qty, entry, stop, target):
    parent = Order(); parent.orderId = parent_id; parent.action = action; parent.totalQuantity = Decimal(qty)
    parent.orderType="LMT"; parent.lmtPrice=float(entry); parent.transmit=False
    stopO = Order(); stopO.action=("SELL" if action=="BUY" else "BUY"); stopO.orderType="STP"
    stopO.auxPrice=float(stop); stopO.totalQuantity=Decimal(qty); stopO.parentId=parent_id; stopO.transmit=False
    takeO = Order(); takeO.action=stopO.action; takeO.orderType="LMT"; takeO.lmtPrice=float(target)
    takeO.totalQuantity=Decimal(qty); takeO.parentId=parent_id; takeO.transmit=True
    return parent, stopO, takeO

class TradingApp(EClient, EWrapper):
    def __init__(self, host, port_id, clientId):
        EClient.__init__(self, self)
        self.connect(host, port_id, clientId)
        self.cache = BarCache()
        self._ids = None
        self._scanner_req_id = 9001
        self._contract_by_symbol = {}
        self._scanner_rows = []
        self._acct = {}
        self._acct_ready = False

        # risk & filters
        self.risk = RiskManager(rr=C.RR, daily_max_dd=C.DAILY_MAX_DD)
        self.filters = FilterStack(FilterConfig(
            use_vwap=True, use_slope=True, use_index=C.USE_INDEX,
            use_atr=C.USE_ATR, atr_min_pct=C.ATR_MIN_PCT,
            slope_lookback=C.SLOPE_LOOKBACK, vwap_lookback=C.VWAP_SLOPE_LOOKBACK,
            vwap_eps=C.VWAP_EPS, sma_eps=C.SMA_EPS, min_slope=C.MIN_SLOPE, min_idx_slope=C.MIN_IDX_SLOPE,
            ))
        self._risk_day = None
        self._risk_lock = threading.Lock()


        # snapshot state
        self._snap_next_id = 20000
        self._snap_req = {}      # reqId -> {symbol, side}
        self._nbbo = {}          # symbol -> {bid, ask}

        self._reconnect_scheduled = False
        self._done = threading.Event()
    
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2108, 2158, 162):
            return
        print(f"[ERR] reqId={reqId} code={errorCode} msg={errorString}")
        
        
        if errorCode in (1100, 1101, 1102, 1300, 1301): # common connectivity
            if not self._reconnect_scheduled:
                self._reconnect_scheduled = True
                threading.Thread(target=self._reconnect_with_backoff, daemon=True).start()
        
        symbol = self._symbol_by_req.get(reqId)
        if symbol is not None:
            if isinstance(reqId, int) and symbol.startswith("SPY|spy15m"):
                print("[REGIME] retry SPY daily as fallback")
                spy = "SPY"
                reqId = 555001
                self._symbol_by_req[reqId] = "SPY|spydaily"
                self.reqHistoricalData(reqId, self._contract_by_symbol[spy], "", "10 D", "1 day", "TRADES", 1, 1, False, [])
        
        
    # ---- lifecycle
    def nextValidId(self, orderId: int):
        self._ids = type("Ids", (), {"curr": orderId, "next": lambda s: setattr(s, "curr", s.curr+1) or s.curr})()
        print(f"[OK] connected, nextValidId={orderId}")
        self.reqMarketDataType(C.DELAYED_DATA_TYPE)
        self.reqCurrentTime()
        self.reqAccountSummary(700, "All", "NetLiquidation,AvailableFunds,BuyingPower,Cushion")

        # Optional: pre-request SPY 15m for index filter
        self._request_index_bars()

        # Start scanner
        if C.ALLOW_TRADING_OUTSIDE_SESSION or within_session():
            self.run_scanner()
        else:
            print("[SCAN] WAITING for session window before starting scanner")

    def accountSummary(self, reqId, account, tag, value, currency):
        try: self._acct[tag] = float(value)
        except: self._acct[tag] = value
    def accountSummaryEnd(self, reqId): self._acct_ready = True

    def _reconnect_with_backoff(self, max_wait=120):
        """Try to reconnect with exponential backoff up to max_wait seconds."""
        wait = 2
        while not self.isConnected() and wait <= max_wait:
            try:
                print(f"[RECON] attempting reconnect (waited {wait}s)…")
                # Close just in case, then reconnect with the same params
                try: self.disconnect()
                except: pass
                self.connect(C.HOST, C.PORT, C.CLIENT_ID)
                if self.isConnected():
                    print("[RECON] reconnected")
                    # Re-init essentials that are set on connection
                    self.reqMarketDataType(C.DELAYED_DATA_TYPE)
                    self.reqCurrentTime()
                    self.reqAccountSummary(700, "All", "NetLiquidation,AvailableFunds,BuyingPower,Cushion")
                    # Kick regime bars again (safe to repeat)
                    self._request_index_bars()
                    # Optionally restart scanner only if we’re in session
                    if C.ALLOW_TRADING_OUTSIDE_SESSION or within_session():
                        self.run_scanner()
                    break
            except Exception as e:
                print(f"[RECON] failed: {e}")
            time.sleep(wait)
            wait = min(wait * 2, max_wait)
        self._reconnect_scheduled = False

    # ---- scanner
    def run_scanner(self):
        sub = ScannerSubscription()
        sub.instrument="STK"; sub.locationCode="STK.US"; sub.scanCode="HOT_BY_VOLUME"
        sub.stockTypeFilter="STOCK"
        sub.abovePrice = C.MIN_PRICE; sub.aboveVolume = C.MIN_VOL
        sub.numberOfRows=25
        print("[SCAN] requesting…"); self.reqScannerSubscription(self._scanner_req_id, sub, [], [])

    def scannerData(self, reqId, rank, contractDetails, distance, benchmark, projection, legsStr):
        sym = contractDetails.contract.symbol
        self._scanner_rows.append((rank, sym))
        self._contract_by_symbol.setdefault(sym, mk_stock(sym))

    def scannerDataEnd(self, reqId):
        self.cancelScannerSubscription(reqId)
        self._scanner_rows.sort()
        symbols = [s for _, s in self._scanner_rows[:C.SCANNER_TOP]]
        
        if not symbols:
            print("[SCAN] empty; using FALLBACK_SYMBOLS")
            symbols = C.FALLBACK_SYMBOLS
        
        print("[SCAN] top:", symbols)

        # request 5m (signal/ATR) and 1m today (VWAP) for each symbol
        for s in symbols:
            self._req_hist(s, barSize="5 mins", duration=C.INTRA_5M_DUR, whatToShow="TRADES", useRTH=0, tag=f"{s}|5m")
            self._req_hist(s, barSize="1 min",  duration=C.INTRA_1M_DUR, whatToShow="TRADES", useRTH=0, tag=f"{s}|1m_today")
            time.sleep(0.2)

    # ---- historical requests
    def _req_hist(self, symbol, barSize, duration, whatToShow, useRTH, tag):
        rid = hash((symbol, barSize, duration, tag)) % 2_147_483_000
        self._symbol_by_req = getattr(self, "_symbol_by_req", {})
        self._symbol_by_req[rid] = tag
        self.reqHistoricalData(rid, self._contract_by_symbol[symbol], "", duration, barSize, whatToShow, useRTH, 1, False, [])
        print(f"[REQ] {tag} dur={duration} bar={barSize} useRTH={useRTH}")

    def historicalData(self, reqId, bar):
        tag = self._symbol_by_req.get(reqId, "")
        if not tag: return
        symbol, kind = tag.split("|", 1)
        tup = (bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume)
        if kind == "5m":
            self.cache.add(symbol, "5m", tup)
        elif kind == "1m_today":
            self.cache.add(symbol, "1m_today", tup)
        elif kind == "spy15m":
            self.cache.add("SPY", "spy_15m", tup)

    def historicalDataEnd(self, reqId, start, end):
        tag = self._symbol_by_req.get(reqId, "")
        if not tag: return
        symbol, kind = tag.split("|", 1)
        
        if kind == "spy15m":
            print(f"[REGIME] SPY 15m bars loaded: {len(self.cache.spy_15m)}")
            return
        if kind == "spydaily":
            daily_bars = list(self.cache.spy_15m)
            return
        # once we have both 5m and 1m_today for a symbol, evaluate
        if kind in ("5m","1m_today"):
            if self.cache.bars_5m[symbol] and self.cache.bars_1m_today[symbol]:
                self._evaluate_symbol(symbol)

    # ---- index (SPY) bars for regime
    def _request_index_bars(self):
        spy = "SPY"; self._contract_by_symbol.setdefault(spy, mk_stock(spy))
        rid = 555000
        self._symbol_by_req = getattr(self, "_symbol_by_req", {})
        self._symbol_by_req[rid] = "SPY|spy15m"
        self.reqHistoricalData(rid, self._contract_by_symbol[spy], "", C.SPY_15M_DUR, "15 mins", "TRADES", 0, 1, False, [])
        print("[REQ] SPY 15m regime bars")


    def _maybe_reset_risk(self):
        """Rest RiskManager.start_equity at the first evaluation of each ET session day"""
        today = datetime.now(tz=NY).date()
        if self._risk_day == today:
            return
        with self._risk_lock:
            # double-check in lock
            if self._risk_day == today:
                return
            # Only reset when we’re within the session windows so "daily" means trading day
            if within_session():  # uses your existing session gate
                netliq = self._acct.get("NetLiquidation", None)
                if netliq:
                    self.risk.start_equity = float(netliq)
                    self._risk_day = today
                    print(f"[RISK] start_equity reset to {self.risk.start_equity} for {today}")

    # ---- evaluation + execution
    def _evaluate_symbol(self, symbol):
        if not self._acct_ready:
            print("[GUARD] account not ready"); return

        # roll daily start_equity at first eval of the session day
        self._maybe_reset_risk()

        # 10% daily stop
        if self.risk.start_equity is None:
            self.risk.set_start_equity(self._acct.get("NetLiquidation", 0.0))
        if self.risk.hit_daily_stop(self._acct.get("NetLiquidation", 0.0)):
            print("[GUARD] daily loss limit hit; pausing new trades")
            return

        # session windows only
        if not C.ALLOW_TRADING_OUTSIDE_SESSION and not within_session():
            print(f"[SESSION] {symbol} outside trading windows")
            return

        bars5 = list(self.cache.bars_5m[symbol])
        bars1 = list(self.cache.bars_1m_today[symbol])
        if len(bars5) < 25 or len(bars1) < 5:
            return

        closes5 = [b[4] for b in bars5]
        sma5, sma20 = sma(closes5, C.SMA_FAST), sma(closes5, C.SMA_SLOW)
        last_px = closes5[-1]

        # base side from SMA cross
        side = "LONG" if (sma5 is not None and sma20 is not None and sma5 > sma20) else "SHORT"

        # momentum slope (close-based)
        mom = slope(closes5, C.SLOPE_LOOKBACK)

        # ATR on 5m
        atr14 = atr(bars5, 14)

        # VWAP (1m today preferred)
        vwap_series = compute_vwap_from_bars(bars1)
        curr_vwap = vwap_series[-1]
        v_trend = vwap_slope(vwap_series, k=C.VWAP_SLOPE_LOOKBACK)

        # SPY regime slope (optional)
        idx_slope = None
        if C.USE_INDEX and len(self.cache.spy_15m) >= 20:
            spy_closes = [b[4] for b in self.cache.spy_15m]
            idx_slope = slope(spy_closes, 20)

        # filter check
        ok, reasons = self.filters.check(
            side=side, price=last_px, sma_fast=sma5, sma_slow=sma20,
            vwap=curr_vwap, vwap_trend=v_trend,
            mom_slope=mom, index_slope=idx_slope, atr=atr14
        )
        print(f"[FILTER] {symbol}: {ok} :: {reasons}")
        if not ok: return

        # execution via snapshot → marketable limit + bracket 2:1
        if sma5 is not None and sma20 is not None:
            side = "LONG" if (sma5 > sma20) else "SHORT"
        last_px = closes5[-1]

        # Heuristics to classify setup (simple examples, tune to taste)
        is_breakout = (
            (side == "LONG"  and last_px >= max(b[4] for b in bars5[-6:-1])) or
            (side == "SHORT" and last_px <= min(b[4] for b in bars5[-6:-1]))
        )
        near_vwap_pullback = (
            curr_vwap is not None and
            ((side == "LONG"  and last_px <= curr_vwap * 1.002) or
            (side == "SHORT" and last_px >= curr_vwap * 0.998))
        )

        qty = C.ENTRY_QTY
        risk_pct = C.RISK_PCT

        if is_breakout:
            # --- Option C1: STOP-LIMIT breakout ---
            trigger = last_px  # or prior swing high/low
            place_stoplimit_bracket(self, symbol, side, stop_px=trigger,
                                    qty=qty, limit_buffer=0.03, risk_pct=risk_pct, rth_only=True)

            # --- or Option C2: PriceCondition breakout ---
            # entry_px can equal trigger or be a tad beyond
            # place_pricecondition_limit_bracket(self, symbol, side,
            #     trigger_px=trigger, entry_px=round(trigger + (0.02 if side=='LONG' else -0.02), 2),
            #     qty=qty, risk_pct=risk_pct, rth_only=True)
        
        elif near_vwap_pullback:
            # --- Option A: Resting LIMIT at your level (e.g., VWAP or last_px ± eps) ---
            entry = round(curr_vwap, 2) if curr_vwap else round(last_px, 2)
            # ensure you don't cross: for longs, don’t price above last; for shorts, don’t price below last
            if side == "LONG":
                entry = min(entry, last_px)
            else:
                entry = max(entry, last_px)
            place_resting_limit_bracket(self, symbol, side, entry=entry, qty=qty,
                                        risk_pct=risk_pct, rth_only=True)
        else:
            print(f"[EXEC] {symbol} no clear breakout/pullback setup; skip")

    
    
    # ---- snapshot/nbbo + bracket
    def _request_snapshot_and_bracket(self, symbol, action, ref_last_px):
        ct = self._contract_by_symbol[symbol]
        tid = self._snap_next_id; self._snap_next_id += 1
        self._snap_req[tid] = {"symbol": symbol, "action": action, "ref": ref_last_px}
        self.reqMarketDataType(1) # switch to REALTIME for snapshot
        self.reqMktData(tid, ct, "", True, False, [])
        print(f"[SNAP] req {tid} {symbol} {action}")

    def tickPrice(self, reqId, tickType, price, attrib):
        info = self._snap_req.get(reqId)
        if not info: return
        sym = info["symbol"]
        store = self._nbbo.setdefault(sym, {"bid": None, "ask": None})
        if tickType == 1: store["bid"] = price
        elif tickType == 2: store["ask"] = price

    def tickSnapshotEnd(self, reqId):
        info = self._snap_req.pop(reqId, None)
        if not info: return
        sym, action, ref_last = info["symbol"], info["action"], info["ref"]
        ba = self._nbbo.get(sym, {})
        bid, ask = ba.get("bid"), ba.get("ask")

        if action == "BUY":
            limit = (ask if (ask and ask>0) else bid or ref_last)
            limit = round((limit or ref_last) + C.PRICE_EPS, 2)
        else:
            limit = (bid if (bid and bid>0) else ask or ref_last)
            limit = round(max(0.01, (limit or ref_last) - C.PRICE_EPS), 2)

        stop, target = self.risk.rr_bracket(action, entry=limit, risk_pct=C.RISK_PCT)
        print(f"[BRACKET] {action} {sym} entry={limit} stop={stop} target={target}")

        parent_id = self._ids.next()
        parent, stopO, takeO = mk_bracket(parent_id, action, C.ENTRY_QTY, limit, stop, target)
        ct = self._contract_by_symbol[sym]
        self.placeOrder(parent.orderId,   ct, parent)
        self.placeOrder(parent.orderId+1, ct, stopO)
        self.placeOrder(parent.orderId+2, ct, takeO)

        # return to delayed after snapshot
        self.reqMarketDataType(C.DELAYED_DATA_TYPE)
