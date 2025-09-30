from ibapi.wrapper import EWrapper
from ibapi.client import EClient
from ibapi.contract import Contract
import time

# need to establish a client
class IBClient(EClient, EWrapper): # inherit

    def __init__(self): 
        EClient.__init__(self, self)
        
    def accountSummary(self, reqId: int, account:  str, tag: str, value: str, currency: str):
        print("AccountSummary: ReqId:", reqId, "Account:", account, "Tag:", tag, "Value:", value, "Currency:", currency)
    def accountSummaryEnd(self, reqId: int):
        print("AccountSummaryEnd. ReqId:", reqId)
    


if __name__== "__main__":
    
    app = IBClient()
    app.connect("127.0.0.1", 7497, clientId=0)

    time.sleep(1)

    app.reqAccountSummary(9001, "All", "NetLiquidation")
    app.run()