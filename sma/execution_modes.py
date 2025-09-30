"""
A) Resting LIMIT for fades/pullbacks
    - Places a parent LMT with OCO stop/target (2:1 via RiskManager)
    - Best when you want price to come to you (no chasing on delayed feed)

C) Breakout entries without snapshots

"""

from __future__ import annotations
from ibapi.order import Order
from ibapi.order_condition import PriceCondition
from ibapi.utils import Decimal
from typing import Tuple


def _mk_bracket(parent_id: int, action: str, qty: str, entry: float, stop: float, target: float):
    parent = Order(); parent.orderId=parent_id; parent.action=action; parent.totalQuantity=Decimal(qty)
    parent.orderType="LMT"; parent.lmtPrice=float(entry); parent.transmit=False


    stopO = Order(); stopO.action=("SELL" if action=="BUY" else "BUY"); stopO.orderType="STP"
    stopO.auxPrice=float(stop); stopO.totalQuantity=Decimal(qty); stopO.parentId=parent_id; stopO.transmit=False


    takeO = Order(); takeO.action=stopO.action; takeO.orderType="LMT"; takeO.lmtPrice=float(target)
    takeO.totalQuantity=Decimal(qty); takeO.parentId=parent_id; takeO.transmit=True
    return parent, stopO, takeO

# ---------- A) RESTING LIMIT (fade / pullback)
def place_resting_limit_bracket(app, symbol: str, side: str, entry: float, qty: str,
    risk_pct: float = 0.02, rth_only: bool = True):
    """Place a LMT parent at entry with OCO stop/target computed from RiskManager (2:1 by default).
    Suitable for fades/pullbacks on delayed data.
    """
    side = side.upper(); action = "BUY" if side=="LONG" else "SELL"
    stop, target = app.risk.rr_bracket(side, entry, risk_pct)
    pid = app._ids.next()
    parent, stopO, takeO = _mk_bracket(pid, action, qty, entry, stop, target)
    parent.outsideRth = stopO.outsideRth = takeO.outsideRth = (not rth_only)
    ct = app._contract_by_symbol[symbol]
    app.placeOrder(parent.orderId, ct, parent)
    app.placeOrder(parent.orderId+1, ct, stopO)
    app.placeOrder(parent.orderId+2, ct, takeO)
    print(f"[EXEC-A] {action} {symbol} LMT={entry} stop={stop} target={target}")




# ---------- C1) STOP-LIMIT breakout ----------


def place_stoplimit_bracket(app, symbol: str, side: str, stop_px: float, qty: str,
    limit_buffer: float = 0.03, risk_pct: float = 0.02,
    rth_only: bool = True):
    """Breakout: parent is STP LMT. Triggers at stop_px, fills up to stop_px±buffer.
    BUY: limit = stop_px + buffer; SELL: limit = stop_px - buffer.
    """
    side = side.upper(); action = "BUY" if side=="LONG" else "SELL"
    parent = Order()
    parent.orderId = app._ids.next()
    parent.action = action
    parent.orderType = "STP LMT"
    parent.totalQuantity = Decimal(qty)
    parent.auxPrice = float(stop_px) # stop trigger
    lmt = stop_px + abs(limit_buffer) if action=="BUY" else max(0.01, stop_px - abs(limit_buffer))
    parent.lmtPrice = float(round(lmt, 2))
    parent.tif = "DAY"; parent.outsideRth = (not rth_only); parent.transmit = False


# Bracket OCO around the expected entry (use lmt as entry anchor)
    entry_anchor = parent.lmtPrice
    stop, target = app.risk.rr_bracket(side, entry_anchor, risk_pct)


    stopO = Order(); stopO.action=("SELL" if action=="BUY" else "BUY"); stopO.orderType="STP"
    stopO.auxPrice=float(stop); stopO.totalQuantity=Decimal(qty); stopO.parentId=parent.orderId; stopO.transmit=False
    takeO = Order(); takeO.action=stopO.action; takeO.orderType="LMT"; takeO.lmtPrice=float(target)
    takeO.totalQuantity=Decimal(qty); takeO.parentId=parent.orderId; takeO.transmit=True


    ct = app._contract_by_symbol[symbol]
    app.placeOrder(parent.orderId, ct, parent)
    app.placeOrder(parent.orderId+1, ct, stopO)
    app.placeOrder(parent.orderId+2, ct, takeO)
    print(f"[EXEC-C1] {action} {symbol} STP_LMT stop={stop_px} lmt={parent.lmtPrice} oco_stop={stop} target={target}")




# ---------- C2) PriceCondition + LIMIT breakout ----------


def place_pricecondition_limit_bracket(app, symbol: str, side: str, trigger_px: float, entry_px: float, qty: str,
    risk_pct: float = 0.02, rth_only: bool = True):
    """Breakout via server-side real-time condition.
    Places inactive LMT parent that activates when LAST price crosses trigger_px.
    """
    side = side.upper(); action = "BUY" if side=="LONG" else "SELL"


    parent_id = app._ids.next()
    parent = Order(); parent.orderId = parent_id
    parent.action = action; parent.totalQuantity = Decimal(qty)
    parent.orderType = "LMT"; parent.lmtPrice = float(entry_px)
    parent.tif = "DAY"; parent.outsideRth = (not rth_only); parent.transmit = False
    parent.conditionsCancelOrder = True


    pc = PriceCondition()
    pc.isMore = True if action=="BUY" else False # BUY when price >= trigger; SELL when price <= trigger
    pc.triggerMethod = 0 # LAST price
    pc.price = float(trigger_px)
    parent.conditions = [pc]


    stop, target = app.risk.rr_bracket(side, entry_px, risk_pct)
    stopO = Order(); stopO.action=("SELL" if action=="BUY" else "BUY"); stopO.orderType="STP"
    stopO.auxPrice=float(stop); stopO.totalQuantity=Decimal(qty); stopO.parentId=parent_id; stopO.transmit=False


    takeO = Order(); takeO.action=stopO.action; takeO.orderType="LMT"; takeO.lmtPrice=float(target)
    takeO.totalQuantity=Decimal(qty); takeO.parentId=parent_id; takeO.transmit=True


    ct = app._contract_by_symbol[symbol]
    app.placeOrder(parent.orderId, ct, parent)
    app.placeOrder(parent.orderId+1, ct, stopO)
    app.placeOrder(parent.orderId+2, ct, takeO)
    print(f"[EXEC-C2] {action} {symbol} PC trigger={trigger_px} LMT={entry_px} stop={stop} target={target}")