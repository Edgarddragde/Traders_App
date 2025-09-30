from ibapi.client import *
from ibapi.wrapper import *
from ibapi.contract import *
import threading
import time


class TradingApp(EClient, EWrapper):
    def __init__(self, host, port_id, client_id):
        EClient.__init__(self, self)
        
        self.connect(host, port_id, client_id)
        
    
    def symbolSamples(self,  reqId: int, contractDescriptions: ListOfContractDescription):
        print("Symbol Samples. Request Id:", reqId)
        
        for contractDescription in contractDescriptions:
            derivSecTypes = ""
            for derivSecType in contractDescription.derivativeSecTypes:
                derivSecTypes += " "
                derivSecTypes += derivSecType
                print("Contract: conId: %s, symbol:%s, secType: %s, primeExchange:%s, "
                      "currency:%s, derivativeSecTypes:%s, description:%s, issuerId:%s" % (
                          contractDescription.contract.conId,
                          contractDescription.contract.symbol,
                          contractDescription.contract.secType,
                          contractDescription.contract.primaryExchange,
                          contractDescription.contract.currency,
                          derivSecTypes,
                          contractDescription.contract.description,
                          contractDescription.contract.issuerId
                      ))
    


if __name__ == "__main__":
    app = TradingApp("127.0.0.1", 7497, 0)
    
    threading.Thread(target=app.run, daemon=True).start()
    
    app.reqMatchingSymbols()