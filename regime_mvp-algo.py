import time, threading
from collections import defaultdict, deque
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.utils import Decimal
from ibapi.scanner import ScannerSubscription
from ibapi.tag_value import TagValue

DELAYED = 3 # 1=REALTIME, 2=FROZEN, 3=DELAYED, 4=DELAYED_FROZEN
CYCLE_SECS = 100
CASH = 140
PRICE_EPS = 0.01
ENTRY_QTY = "3"
INFO_CODES = {2104, 2106, 2108, 2158, 162}
USE_SNAPSHOT = False
USE_MARKET = False


class Ids:
    def __init__(self, start_id: int): self.curr = start_id
    def next(self): self.curr += 1; return self.curr


# Useful Functions
def mk_stock(symbol: str) -> Contract: 
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.currency = "USD"
    contract.exchange = "SMART"
    return contract

def mk_order(action: str, qty: str, order_type="LMT", limit_price=None, tif="Day", outside_rth=True) -> Order:
    order = Order()
    order.action = action.upper() # uppercase just in case
    order.totalQuantity = Decimal(qty)
    order.orderType = order_type.upper() # uppercase just in case
    if order.orderType == "LMT":
        assert limit_price is not None, "limit price required for LMT"
        order.lmtPrice = float(limit_price)
    order.tif = tif
    order.outsideRth = bool(outside_rth)
    return order

def place(app, ids, symbol: str, action: str, qty: str, order_type="LMT", limit_price=None, tif="DAY", outside_rth=True):
    orderId = ids.next()
    contract = mk_stock(symbol)
    order = mk_order(action, qty, order_type, limit_price, tif, outside_rth)
    print(f"[ORDER] {orderId} {action} {qty} {symbol} {order_type} {limit_price or ''} tif={tif} oRTH={outside_rth}")
    app.placeOrder(orderId, contract, order)


# bracket order for risk
def mk_bracket(parent_id, action, qty, entry_price, stop_price, take_price):
    parent = mk_order(action, qty, "LMT", entry_price); parent.transmit = False
    parent.orderId = parent_id
    
    
    # stop-loss (opposite action)
    stop = Order()
    stop.action = "SELL" if action == "BUY" else "BUY"
    stop.orderType = "STP"
    stop.auxPrice = float(stop_price)
    stop.totalQuantity = Decimal(qty)
    stop.parentId = parent_id
    stop.transmit = False
    
    # Take-profit
    take = Order()
    take.action = stop.action
    take.orderType = "LMT"
    take.lmtPrice = float(take_price)
    take.totalQuantity = Decimal(qty)
    take.parentId = parent_id
    take.transmit = True # last child transmits the whole bracket
    
    return parent, stop, take

def qty_by_risk(equity, risk_pct, entry, stop):
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0: return 0
    dollars_at_risk = equity * risk_pct
    return max(0, int(dollars_at_risk // per_share_risk))


def true_ranges(h, l, c):
    trs = []
    for i in range(1, len(c)):
        hl = h[i] - l[i]
        hc = abs(h[i] - c[i-1])
        lc = abs(l[i] - c[i-1])
        trs.append(max(hl, hc, lc))
    return trs


# --------- App -----------
class TradingApp(EClient, EWrapper):
    def __init__(self, host, port_id, clientId):
        EClient.__init__(self, self)
        self.connect(host, port_id, clientId)
        
        # ids / locking
        self._ids = None
        self._lock = threading.Lock()
        
        # scanner + contracts
        self._scanner_req_id = 9001
        self._contract_by_symbol = {}
        self._scanner_rows = []
        
        # account summary
        self._acct = {}
        self._acct_ready = False
        
        # historical queues & maps
        self._hist_req_base = 10000
        self._expected_intraday = 0
        self._inflight = set()
        self._req_kind = {}
        self._hist_pending = deque()
        self._hist_data = defaultdict(list) # reqId
        self._symbol_by_req = {}
        self._expected_daily = 0
        
        # snapshot
        self._snap_next_id = 20000
        self._snap_req = {} # ticketId -> {"symbol": ..., "action": ..., "qty": ...}
        self._nbbo = {} # symbol -> {"bid": ..., "ask": ...}
        
        # daily gap regs (y_close, t_open)
        self._daily_refs = {}
        
        # regime
        self._regime = None
        self._regime_meta = {}
        
        # lifecycle
        self._cycle_timer = None
        self._done = threading.Event()
        
    
    def _reset_cycle_state(self):
        """clear only per-cycle state; keep account, contracts, and daily_refs."""
        self._scanner_rows.clear()
        self._hist_pending.clear()
        self._hist_data.clear()
        self._symbol_by_req.clear()
        self._req_kind.clear()
        self._inflight.clear()
        self._expected_intraday = 0
        self._expected_daily = 0
        # DO NOT clear: _acct, _acct_ready, _contract_by_symbol, _daily_refs, regime state, etc.
    
    
    def start_cycle(self):
        """Begin one full scan -> fetch -> rank cycle"""
        print(f"\n[CYCLE] starting new cycle...")
        self._reset_cycle_state()
        self.run_scanner() # this triggers: scanner -> request_daily_gap_refs -> request hist_for
    
    
    def _schedule_next_cycle(self, delay=CYCLE_SECS):
        """Run another cycle after a delay."""
        def _kick():
            try:
                self.start_cycle()
            except Exception as e:
                print(f"[CYCLE] error starting next cycle: {e}")
        
        timer = threading.Timer(delay, _kick)
        timer.daemon = True
        timer.start()
        self._cycle_timer = timer
    
    def nextValidId(self, orderId: int):
        self._ids = Ids(orderId)
        print(f"[OK] connected, nextValidId={orderId}")
        
        # choose delayed data mode for free-first
        self.reqMarketDataType(DELAYED)
        
        # small heartbeat
        self.reqCurrentTime()
        
        self.reqAccountSummary(700, "All",
                               "AvailableFunds,BuyingPower,FullAvailableFunds,NetLiquidation,Cushion,DayTradesRemaining"
                               )
        
        threading.Timer(2.0, self.start_cycle).start()
        
        # regime first (SPY daily)
        self.request_regime()    
    
    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str):
        # store as floats when possible
        try:
            self._acct[tag] = float(value)
        except:
            self._acct[tag] = value # e.g., 'N/A'
        if tag in ("AvailableFunds", "BuyingPower", "NetLiquidations", "Cushion"):
            print(f"[ACCT] {tag}={value} {currency}")
    
    
    def accountSummaryEnd(self, reqId: int):
        self._acct_ready = True
        print("[ACCT] summary ready")

    
    def request_regime(self):
        rid = 4000
        contract = mk_stock("SPY")
        self._contract_by_symbol["SPY"] = contract
        self._symbol_by_req[rid] = "SPY|REGIME"
        self._req_kind[rid] = "REGIME"
        print("[REQ] REGIME SPY dur=40 D bar=1 day useRTH=1")
        self.reqHistoricalData(rid, contract, "", "40 D", "1 day", "TRADES", 1, 1, False, [])
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in INFO_CODES:
            return # info
        print(f"[ERR] reqId={reqId} errorCode={errorCode} msg={errorString}")
        
        if reqId in self._inflight:
            self._inflight.discard(reqId)
            self._kick_hist_request()
    
    
    def currentTime(self, time):
            print(f"[TIME] {time}")
    
    
    def _start_scanner(self, profile=0):
        sub = ScannerSubscription()
        sub.instrument = "STK"
        sub.stockTypeFilter = "STOCK"
        
        cap, avail = self._affordable_cap(shares=3)
        
        if profile == 0:
            sub.locationCode = "STK.US.MAJOR"
            sub.scanCode = "MOST_ACTIVE"
            sub.abovePrice = 5
            sub.aboveVolume = 500000
            sub.numberOfRows = 25
            label = "MOST_ACTIVE majors (>=$5, >=500k)"
        elif profile == 1:
            sub.locationCode = "STK.US.MAJOR"
            sub.scanCode = "TOP_PERC_GAIN"
            sub.abovePrice = 3
            sub.numberOfRows = 25
            label = "TOP_PERC_GAIN majors (>=$3)"
        else:
            sub.locationCode = "STK.US" # broader universe if majors return nothing
            sub.scanCode = "MOST_ACTIVE"
            sub.abovePrice = 1
            sub.numberOfRows = 25
            label = "MOST_ACTIVE US (>= $1)"
        
        
        # apply 'belowPrice' only if we actually have cash info
        if cap is not None:
            sub.belowPrice = cap
            # ensure abovePrice is not higher than belowPrize (IBKR rejects that)
            if hasattr(sub, "abovePrice") and sub.abovePrice and sub.belowPrice <= sub.abovePrice:
                sub.abovePrice = max(0.01, round(0.2 * sub.belowPrice, 2)) # loosen lower bound
        
        self._scanner_profile = profile
        self._scanner_rows.clear()
        print(f"[SCAN] requesting... profile {profile}: {label}")
        self.reqScannerSubscription(self._scanner_req_id, sub, [], [])
    
    
    def _retry_scanner_or_fallback(self):
        # try looser profiles; if still empty, use a safe seed list
        if getattr(self, "_scanner_profile", 0) < 2:
            self.cancelScannerSubscription(self._scanner_req_id)
            time.sleep(0.2)
            self._start_scanner(self._scanner_profile + 1)
        else:
            self.cancelScannerSubscription(self._scanner_req_id)
            seeds = ["SPY", "AAPL", "TSLA", "NVDA", "AMD", "MSFT", "META", "QQQ", "MU", "NFLX"]
            print(f"[SCAN] no results after retries - using fallback: {seeds}")
            for s in seeds:
                if s not in self._contract_by_symbol:
                    self._contract_by_symbol[s] = mk_stock(s)
            self.request_daily_gap_refs(seeds)
            self.request_hist_for(seeds)
    
    
    def run_scanner(self):
        self._start_scanner(0)

    
    def scannerData(self, reqId, rank, contractDetails, distance, benchmark, projection, legsStr):
        c = contractDetails.contract
        symbol = c.symbol
        self._scanner_rows.append((rank, symbol, projection or "", c.primaryExchange or ""))
        
        # keep a resuable minimal Contract
        if symbol not in self._contract_by_symbol:
            self._contract_by_symbol[symbol] = mk_stock(symbol)
    
    
    def scannerDataEnd(self, reqId: int):
        print(f"[SCAN] done ({len(self._scanner_rows)} rows). ")
        
        if len(self._scanner_rows) == 0:
            self._retry_scanner_or_fallback()
            return
        self.cancelScannerSubscription(reqId)
        
        self._scanner_rows.sort(key=lambda x: x[0])
        top = [s for _, s,*_ in self._scanner_rows[:15]]
        print("[SCAN] top symbols: ", top)
        
        self.request_daily_gap_refs(top)
        
        # ask for true gap regs
        # request historical data for each (in small bursts to avoid pacing)
        self.request_hist_for(top)
    
    
    # --- historical (delayed is fine)
    def request_hist_for(self, symbols):
        burst = 3 # concurrent requests
        
        for sym in symbols:
            self._hist_pending.append(sym)
        for _ in range(min(burst, len(self._hist_pending))):
            self._kick_hist_request()
    
    
    def _kick_hist_request(self):
        if not self._hist_pending:
            return
        
        sym = self._hist_pending.popleft()
        rid = self._hist_req_base + len(self._symbol_by_req) + 1 # new reqId for different symbol
        self._symbol_by_req[rid] = sym
        self._req_kind[rid] = "INTRA"
        ct = self._contract_by_symbol[sym]
        print(f"[REQ] HIST {sym} dur=2 bar=5 mins useRTH=0")
        self._inflight.add(rid)
        
        # 30 min, 1-min bar, TRADES, useRTH=0 (include extended hours)
        self.reqHistoricalData(rid, ct, "", "2 D", "5 mins", "TRADES", 0, 1, False, [])
        
        # be gentle
        time.sleep(0.25)
    
    
    def historicalData(self, reqId, bar):
        sym = self._symbol_by_req.get(reqId, "?")
        # store close + volume for quick calc
        self._hist_data[reqId].append((bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume))
        if len(self._hist_data[reqId]) in (1, 5, 10, 20):
            print(f"[HIST] {sym} +{len(self._hist_data[reqId])} bars, last={bar.close}")
    
    
    def request_daily_gap_refs(self, symbols):
        self._expected_daily = len(symbols)
        for symbol in symbols:
            reqId = 5000 + len(self._symbol_by_req) + 1
            self._symbol_by_req[reqId] = symbol + "|DAILY"
            self._req_kind[reqId] = "DAILY"
            contract = self._contract_by_symbol[symbol]
            
            # 2 daily bars, RTH=1 so opens/closes are clean
            print(f"[REQ] DAILY {symbol} dur=3 Days bar=1 day useRTH=1")
            self.reqHistoricalData(reqId, contract, "", "3 D", "1 day", "TRADES", 1, 1, False, [])
            time.sleep(0.2)


    def historicalDataEnd(self, reqId: int, start: str, end: str):
        sym_tag = self._symbol_by_req.get(reqId)
        kind = self._req_kind.get(reqId)
        
        
        # regime branch
        if kind == "REGIME":
            bars = self._hist_data[reqId]
            closes = [b[4] for b in bars]
            highs = [b[2] for b in bars]
            lows = [b[3] for b in bars]
            
            
            atr_pct, mom10 = 0.0, 0.0
            if len(closes) >= 16:
                trs = true_ranges(highs, lows, closes)
                atr = sum(trs[-14:]) / 14.0
                last_close = closes[-1]
                atr_pct = (atr / last_close) if last_close > 0 else 0.0
            if len(closes) >= 11 and closes[-11] != 0:
                mom10 = (closes[-1] - closes[-11]) / closes[-11]
            
            if atr_pct >= 0.02 and abs(mom10) >= 0.01:
                self._regime = "trend-bull" if mom10 > 0 else "trend-bear"
            elif atr_pct <= 0.012:
                self._regime = "range"
            else:
                self._regime = "neutral"
            
            self._regime_meta = {"atr_pct": atr_pct, "mom10": mom10}
            print(f"[REGIME] {self._regime} atr%={atr_pct:.2%} mom10={mom10:+.2%}")
            return
            
        
        # daily refs: store (y_close, t_open) | don't touch intraday queue
        if kind == "DAILY":
            base = sym_tag.split("|")[0] if sym_tag else "?"
            bars = self._hist_data[reqId]
            print(f"[HIST-END-DAILY] {base} ({len(bars)} bars)")
            if len(bars) >= 2:
                y_close = bars[-2][4] # yesterday close
                t_open = bars[-1][1] # today open
                self._daily_refs[base] = (y_close, t_open)
            
            # check if we can rank once intraday is also done:
            daily_done = len(self._daily_refs) >= self._expected_daily
            intraday_done = (not self._hist_pending) and (not self._inflight)
            if daily_done and intraday_done:
                self.rank_and_print()
            return
        
        # intraday flow
        sym = self._symbol_by_req.get(reqId)
        print(f"[HIST] {sym} {len(self._hist_data[reqId])} bars")
        
        # mark this request as finished
        self._inflight.discard(reqId) 
        # request Next
        self._kick_hist_request()
        
        # if finished all (no pending, no inflight, ran all expected intradays), aggregate
        all_intraday_done = (not self._hist_pending) and (not self._inflight)
        daily_done = (len(self._daily_refs) >= self._expected_daily)
        if all_intraday_done and daily_done:
            self.rank_and_print()


    # --- simple free signals
    @staticmethod
    def _slope(values):
        n = len(values)
        if n < 3: return 0.0
        
        # linear regression slope on index vs value
        xbar = (n - 1) / 2
        ybar = sum(values) / n
        num = sum((i - xbar) * (v - ybar) for i, v in enumerate(values))
        den = sum((i - xbar) ** 2 for i in range(n))
        return num / den if den else 0.0
    
    
    def _entry_from_last(self, action: str, last_px: float):
        if last_px is None or last_px <= 0:
            return None
        if action.upper() == "BUY":
            return round(last_px + PRICE_EPS, 2)
        else:
            return round(max(0.01, last_px - PRICE_EPS), 2)
    
    
    def place_bracket_rr(self, symbol, action: str, qty, entry, risk_pct=0.02, rr=2.0):
        # risk_pct = % stop below (or above for short)
        # rr = reward:risk ration (2.0 = 2:1)
        symbol = symbol.split("|")[0]
        
        if action.upper() == "BUY":
            stop = round(entry * (1 - risk_pct), 2)
            target = round(entry * (1 + risk_pct * rr), 2)
        else:
            stop = round(entry * (1 + risk_pct), 2)
            target = round(entry * (1 - risk_pct * rr), 2)
        
        contract = mk_stock(symbol)
        parent_id = self._ids.next()
        parent, stopO, takeO = mk_bracket(parent_id, action, qty, entry, stop, target)
        print(f"[BRACKET] {action} {qty} {symbol} entry={entry} stop={stop} target={target}")
        self.placeOrder(parent.orderId, contract, parent)
        self.placeOrder(parent.orderId+1, contract, stopO)
        self.placeOrder(parent.orderId+2, contract, takeO)
    
    
    def _affordable_cap(self, shares=3):
        """Max share price we can afford given AvailableFunds and a target share count. """
        try:
            avail = float(self._acct.get("AvailableFunds", 0.0))
        except Exception:
            avail = 0.0
        if avail <= 0:
            return None, 0.0
        cap = max(0.5, round(avail / float(shares), 2)) # floor so we don't go below pennies
        return cap, avail
    
    
    def rank_and_print(self):
        rows = []
        avail = float(self._acct.get("AvailableFunds", 0.0))
                
        if avail <= 0:
            print(f"[GUARD] no available funds, skipping orders this cycle")
            self._done.set(); return
        
        if not self._acct_ready:
            print(f"[GUARD] account summary not ready: skipping orders this cycle")
            self._done.set()
            return
        
        by_sym = {}
        for reqId, bars in self._hist_data.items():
            
            # only use intraday requests
            if self._req_kind.get(reqId) != "INTRA":
                continue
            
            sym = self._symbol_by_req.get(reqId, "")
            base_sym = sym.split("|")[0]
            # error checking
            if not sym: continue # if there is no symbol
            closes = [b[4] for b in bars]
            vols = [b[5] for b in bars]
            if len(closes) < 5:
                continue
            
            
            # momentum = slope of at last 10 closes
            momentum = self._slope(closes[-10:]) if len(closes) >= 10 else self._slope(closes)
            
            # true gap metrics if we have refs; else fallback to coarse window gap
            last_close = closes[-1]
            
            max_price = avail / 3.0
            if last_close > max_price:
                print(f"[FILTER] skip {sym}: px={last_close:.2f} too expensive for 5 shares (limit {max_price:.2f})")
                continue
            
            y_close, t_open = self._daily_refs.get(sym, (None, None))
            if y_close and t_open and y_close > 0:
                opening_gap = (t_open - y_close) / y_close
                current_gap = (last_close - y_close) / y_close
                denom = (t_open -y_close)
                gap_fill_pct = ((last_close - t_open) / denom) if denom else 0.0
            else:
                opening_gap = ((last_close - closes[0]) / closes[0]) if closes[0] else 0.0
                current_gap = opening_gap
                gap_fill_pct = 0.0
            
            if not (-0.5 <= opening_gap <= 0.5):
                continue
            
            avg_vol = sum(vols[-10:]) / min(10, len(vols))
            rows.append((sym, opening_gap, current_gap, gap_fill_pct, momentum, avg_vol, last_close))
            prev = by_sym.get(base_sym)
            if prev is None or momentum > prev[4]:
                by_sym[base_sym] = rows
        
        # filter + rank
        rows = [r for r in rows if abs(r[1]) >= 0.02] # |gap| >= 2%
        rows.sort(key=lambda r: (r[4], abs(r[1])), reverse=True) # momentum first, then |gap|
        
        print("\n=== CANDIDATES (DELAYED) ===")
        for sym, ogap, cgap, fill, mom, av, px in rows[:10]:
            print(f"{sym:6s} Opengap={ogap:+.2%} currGap={cgap:+.2%} fill={fill:+.2f} mom={mom:+.4f} avgVol10={av:.0f} px={px:.2f}")
        
        # --- regime-aware signal -> 2:1 bracket
        if rows:
            sym, ogap, cgap, fill, mom, av, px = rows[0]
            regime = self._regime or "neutral"

            if regime.startswith("trend"):
                if regime == "trend-bull" and ogap > 0.02 and mom > 0 and fill > -0.3:
                    print(f"[SIGNAL] LONG {sym} ({regime})")
                    entry = self._entry_from_last("BUY", px)
                    if entry: self.place_bracket_rr(sym, "BUY", ENTRY_QTY, entry, risk_pct=0.02, rr=2.0)
                elif regime == "trend-bear" and ogap < -0.02 and mom < 0 and fill < 0.3:
                    print(f"[SIGNAL] SHORT {sym} ({regime})")
                    entry = self._entry_from_last("SELL", px)
                    if entry: self.place_bracket_rr(sym, "SELL", ENTRY_QTY, entry, risk_pct=0.02, rr=2.0)
                else:
                    print(f"[SIGNAL] no trade under {regime}")

            elif regime == "range":
                if ogap > 0.04 and mom < 0 and fill < -0.2:
                    print(f"[SIGNAL] SHORT {sym} (range)")
                    entry = self._entry_from_last("SELL", px)
                    if entry: self.place_bracket_rr(sym, "SELL", ENTRY_QTY, entry, risk_pct=0.02, rr=2.0)
                elif ogap < -0.04 and mom > 0 and fill > 0.2:
                    print(f"[SIGNAL] LONG {sym} (range)")
                    entry = self._entry_from_last("BUY", px)
                    if entry: self.place_bracket_rr(sym, "BUY", ENTRY_QTY, entry, risk_pct=0.02, rr=2.0)
                else:
                    print("[SIGNAL] no trade (range rules not met)")

            else:  # neutral
                if ogap > 0.03 and mom > 0 and fill > -0.2:
                    print(f"[SIGNAL] LONG {sym} (neutral)")
                    entry = self._entry_from_last("BUY", px)
                    if entry: self.place_bracket_rr(sym, "BUY", ENTRY_QTY, entry, risk_pct=0.02, rr=2.0)
                else:
                    print("[SIGNAL] no trade (neutral)")

        print("[CYCLE] done.")
        self._schedule_next_cycle(CYCLE_SECS)
        self._done.set()


    def _place_marketable_limit_from_last(self, symbol, action, last_px):
        # simple marketable limit using last price as anchor ( FREE MODE )
        if last_px is None or last_px <= 0:
            print(f"[ORDER] skip {symbol} (no last price)")
            return
        
        if USE_MARKET:
            # pure market order (fills immediately; less price control)
            place (self, self._ids, symbol, action, ENTRY_QTY, "MKT", None, tif="DAY", outside_rth=False)
            return
        
        # marketable limit: a tick beyond last price for quick fill but with protection
        if action.upper() == "BUY":
            limit = round(last_px + PRICE_EPS, 2)
        else: # sell
            limit = round(max(0.01, last_px - PRICE_EPS), 2)
        place(self, self._ids, symbol, action, ENTRY_QTY, "LMT", limit, tif="DAY", outside_rth=False)
    
    
    def _request_snapshot_and_place(self, symbol, action, qty):
        # paid snapshot path ( sets up bid/ask then places limit through the spread)
        contract = self._contract_by_symbol[symbol]
        tid = self._snap_next_id
        self._snap_next_id += 1
        self._snap_req[tid] = {"symbol": symbol, "action": action, "qty": qty}
        print(f"[SNAP] req {tid} {symbol} {action} x{qty}")
        # genericTickList="", snapshot=True, regulatorySnapshot=False
        self.reqMarketDataType(1) # switch to REALTIME just-in-time
        self.reqMktData(tid, contract, "", True, False, [])
        # we'll flip back to delayed after tickSnapshotEnd
    
    
    def tickPrice(self, reqId, tickType, price, attrib):
        # field 1=bid, 2=ask, 4=last; we only care aobut bid/ask for snapshot
        if reqId not in self._snap_req:
            return
        symbol = self._snap_req[reqId]['symbol']
        store = self._nbbo.setdefault(symbol, {"bid": None, "ask": None})
        if tickType == 1:
            store["bid"] = price
        elif tickType == 2:
            store["ask"] = price
    
    
    def tickSnapshotEnd(self, reqId: int):
        if reqId not in self._snap_req:
            return
        info = self._snap_req.pop(reqId)
        sym, action, qty = info["symbol"], info["action"], info["qty"]
        ba = self._nbbo.get(sym, {})
        bid, ask = ba.get("bid"), ba.get("ask")
        
        # pick a marketable limit using NBBO if available; fallback to simple epsilon
        if action == "BUY":
            limit = (ask if ask and ask > 0 else (bid if bid and bid > 0 else None))
            limit = round((limit or 0) + PRICE_EPS, 2) if limit else None
        else:
            limit = (bid if bid and bid > 0 else (ask if ask and ask > 0 else None))
            limit = round(max(0.01, (limit or 0) - PRICE_EPS), 2) if limit else None
        
        if limit:
            place(self, self._ids, sym, action, qty, "LMT", limit, tif="DAY", outside_rth=False)
        else:
            print(f"[SNAP] no NBBO for {sym}, skipping order")
        
        # return to delayed mode for free-first scanning
        self.reqMarketDataType(DELAYED)
    
    
    
def main():
    app = TradingApp("127.0.0.1", 7497, 0)
    t = threading.Thread(target=app.run, daemon=True)
    t.start()
    
    # wait until MVP completes one cycle
    app._done.wait(timeout=180)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n[STOP] shutting down.")
        if hasattr(app, "_cycle_timer"):
            try:
                app._cycle_timer.cancel()
            except:
                pass
        app.disconnect()
            
        
        

if __name__ == "__main__":
    main()