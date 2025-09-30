from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.utils import *
import time


class IBClient(EClient, EWrapper):
    def __init__(self, host, port_id, client_id):
        EClient.__init__(self, self)
        self.connect(host, port_id, client_id)
    
    def pnlSingle(reqId: int, pos: Decimal, dailyPnL: float, unrealizedPnL: float, realizedPnL: float, value: int):
        print("Daily PnL Single. ReqId:", reqId, "Position:", pos, "Daily PnL:", dailyPnL, "Unrealized:", unrealizedPnL,
              "RealizedPnL:", realizedPnL, "Value:", value)
    
    
app = IBClient("127.0.0.1", 7497, 0)
time.sleep(1)
app.reqPnLSingle(101, "DUH528149", "", 8314)
app.run()