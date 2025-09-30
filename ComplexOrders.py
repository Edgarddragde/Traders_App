from ibapi.client import *
from ibapi.wrapper import *
from ibapi.contract import Contract, ComboLeg
from ibapi.tag_value import TagValue
from ibapi.order import *


class TestApp(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)

    def nextValidId(self, orderId: int):
        # Order info
        mycontract = Contract()
        mycontract.symbol = "AAPL"
        mycontract.secType = "BAG"
        mycontract.exchange = "SMART"
        mycontract.currency = "USD"

        # ComboLeg
        leg1 = ComboLeg()
        leg1.conId = 76792991
        leg1.ratio = 1
        leg1.action = "BUY"
        leg1.exchange = "SMART"

        leg2 = ComboLeg()
        leg2.conId = 265598
        leg2.ratio = 1
        leg2.action = "SELL"
        leg2.exchange = "SMART"

        mycontract.comboLegs = []
        mycontract.comboLegs.append(leg1)
        mycontract.comboLegs.append(leg2)

        myorder = Order()
        myorder.orderId = orderId
        myorder.action = "BUY"
        myorder.orderType = "LMT"
        myorder.lmtPrice = 80
        myorder.totalQuantity = Decimal(10)
        myorder.tif = "GTC"
        myorder.smartComboRoutingParams = [(TagValue("NonGuaranteed", "1"))] # type: ignore

        self.placeOrder(orderId, mycontract, myorder)

    def openOrder(self, orderId: OrderId, contract: Contract, order: Order, orderState: OrderState):
        print(f"orderId: {orderId}, contract {contract}, order: {order}, Maintenance Margin: {orderState.maintMarginChange}")


    def orderStatus(self, orderId: OrderId, status: str, filled: Decimal, remaining: Decimal, avgFillPrice: float, permId: int, parentId: int, lastFillPrice: float, clientId: int, whyHeld: str, mktCapPrice: float):
        print(f"orderStatus. orderId: {orderId}, status: {status}, filled: {filled}, remaining: {remaining}, avgFillPrice: {avgFillPrice}, permId: {permId}, parentId: {parentId}, lastFillPrice: {lastFillPrice}, clientId: {clientId}, whyHeld: {whyHeld}, mktCapPrice: {mktCapPrice}")

    def execDetails(self, reqId: int, contract: Contract, execution: Execution):
        print(f"execDetails. reqId: {reqId}, contract: {contract}, execution: {execution}")


if __name__ == "__main__":
    app = TestApp()
    app.connect("127.0.0.1", 7497, 1000)
    app.run()
        
