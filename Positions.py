from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.utils import *
import threading
import time


class IBClient(EClient, EWrapper):
    def __init__(self, host, port_id, client_id):
        EClient.__init__(self, self)
        
        self.connect(host, port_id, client_id)
    
    def position(self, account: str, contract: Contract, 
                 position: Decimal, avgCost: float):
        print("Position. Account:", account, "Contract:", contract, 
              "Position:", position, "Avg cost:", avgCost)
    
    
    def positionEnd(self):
        print("PositionEnd")
    

def websocket_con():
    app.run()
    
app = IBClient("127.0.0.1", 7497, 0)

con_thread = threading.Thread(target=websocket_con, daemon=True)

con_thread.start()
time.sleep(1)
app.reqPositions()
time.sleep(1)