from ibapi.client import EClient
from ibapi.utils import *
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
import decimal
import time

class IBClient(EClient, EWrapper):
    
    def __init__(self):
        EClient.__init__(self, self)
    
    
    def updateAccountValue(self, key: str, val: str, currency: str, accountName: str):
        print("UpdateAccountValue. Key:", key, "Value:", val, "Currency:", currency, "AccountName:", accountName)
    
    
    def updatePortfolio(self, contract: Contract, position: Decimal, 
                        marketPrice: float, marketValue: float, averageCost: float, 
                        unrealizedPNL: float, realizedPNL: float, accountName: str):
        print("UpdatedPortfolio. Symbol:", contract.symbol, "SecType:", contract.secType, 
              "Exchange:", contract.exchange, "Position:", decimalMaxString(position), 
              "MarketPrice:", floatMaxString(marketPrice), "MarketValue", floatMaxString(marketValue),
              "AverageCost:", floatMaxString(averageCost), "UnrealizedPNL:", floatMaxString(unrealizedPNL),
              "RealizedPNL:", floatMaxString(realizedPNL), "AccountName:", accountName)
    
    def updateAccountTime(self, timeStamp: str):
        print("UpdateAccountTime. Time:", timeStamp)
        
    def accountDownloadEnd(self, accountName: str):
        print("AccountDownloadEnd. Account:", accountName)
        

if __name__ == "__main__":
    app = IBClient()
    app.connect("127.0.0.1", 7497, clientId=0)
    
    time.sleep(1)
    
    app.reqAccountUpdates(True, "DUH528149")
    app.run()