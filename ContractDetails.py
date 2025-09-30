from ibapi.client import *
from ibapi.wrapper import *
from ibapi.contract import *
import threading
import time


class TradingApp(EClient, EWrapper):
    def __init__(self, host, port_id, client_id):
        EClient.__init__(self, self)

        self.connect(host, port_id, client_id)

    def contractDetails(self, reqId: int, contractDetails: ContractDetails):
        contract = contractDetails.contract
        print("Contract Details for ReqID:", reqId)
        print("Symbol:", contract.symbol)
        print("Exchange:", contract.exchange)
        print("PrimaryExchange:", contract.primaryExchange)
        print("Currency:", contract.currency)
        print("-"*50)

    def contractDetailsEnd(self, reqId: int):
        print("ContractDetailsEnd. ReqId:", reqId)



if __name__ == "__main__":
    app = TradingApp("127.0.0.1", 7497, 0)
    threading.Thread(target=app.run, daemon=True).start()

    time.sleep(1)
    contract = Contract()
    contract.exchange = "SMART"
    contract.symbol = "F"
    contract.secType = "STK"
    contract.currency = "USD"
    app.reqContractDetails(9599491, contract)

    time.sleep(3)
    app.disconnect()

