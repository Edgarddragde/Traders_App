import time, datetime, queue
import pandas as pd
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.client import Contract, Order

from threading import Thread
from lightweight_charts import Chart

INITIAL_SYMBOL = "TSM"
INITIAL_TIMEFRAME = "5 mins"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_CLIENT_ID = 1

LIVE_TRADING = False
LIVE_TRADING_PORT = 7496
PAPER_TRADING_PORT = 7497
TRADING_PORT = PAPER_TRADING_PORT
if LIVE_TRADING:
    TRADING_PORT = LIVE_TRADING_PORT

data_queue = queue.Queue()


class IBClient(EWrapper, EClient):

    def __init__(self, host, port, client_id):
        EClient.__init__(self, self)
        
        self.connect(host, port, client_id)

        thread = Thread(target=self.run)
        thread.start()
        
    def error(self, req_id, code, msg, misc):
        if code in [2104, 2106, 2158]:
            print(msg)
        else:
            print('Error {}: {}'.format(code, msg))

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.order_id = orderId
        print(f"next valid id is {self.order_id}")

    def historicalData(self, req_id, bar):
        print(bar)

        t = datetime.datetime.fromtimestamp(int(bar.date))

        # creation bar dictionary for each bar received
        data = {
            'date': t,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': int(bar.volume)
        }

        print(data)
        data_queue.put(data)

    # callback when all historical data has been received
    def historicalDataEnd(self, reqId, start, end):
        print(f"end of data {start} {end}")

        update_chart()


def get_bar_data(symbol, timeframe):
    print(f"getting bar data for {symbol} {timeframe}")

    contract = Contract()
    contract.symbol = symbol
    contract.secType = 'STK'
    contract.exchange = 'SMART'
    contract.currency = 'USD'
    what_to_show = 'TRADES'


    client.reqHistoricalData(2, contract, '', '30 D', timeframe, what_to_show, True, 2, False, [])

    time.sleep(1)

    chart.watermark(symbol)
    

def on_timeframe_selection(chart):
    print("Selected Timeframe")
    print(chart.topbar['symbol'].value, chart.topbar['timeframe'].value)
    get_bar_data(chart.topbar["symbol"].value, chart.topbar['timeframe'].value)



# called when we want to updates what is rendered on the chart
def update_chart():
    try:
        bars = []
        while True: # Keep checking the queue for new data
            data = data_queue.get_nowait()
            bars.append(data)
    except queue.Empty:
        print("empty queue")
    finally:
        # once data is received, convert to pandas dataframe
        df = pd.DataFrame(bars)
        print(df)

        # set the data on the chart
        if not df.empty:
            chart.set(df)

            # once we get the data back, we don't need a spinner anymore
            #chart.spinner(False)

# get new bar data when the user changes timeframes
def on_timeframe_selection(chart):
    print("selected timeframe")
    print(chart.topbar['symbol'].value, chart.topbar['timeframe'].value)
    get_bar_data(chart.topbar['symbol'].value, chart.topbar['timeframe'].value)

#  get new bar data when the user enters a different symbol
def on_search(chart, searched_string):
    get_bar_data(searched_string, chart.topbar['timeframe'].value)
    chart.topbar['symbol'].set(searched_string)

# handles when the user uses an order hotkey combination
def place_order(key):
    # get current symbol
    symbol = chart.topbar['symbol'].value
    
    # build contract object
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.currency = "USD"
    contract.exchange = "SMART"
    
    # build order object
    order = Order()
    order.orderType = "MKT"
    order.totalQuantity = 1
    
    # get next order id
    client.reqIds(-1)
    time.sleep(2)
    
    # set action to buy or sell depending on key pressed
    # shift+O is for a buy order
    if key == 'O':
        print("buy order")
        order.action = "BUY"


    # shift+P for a sell order
    if key == 'P':
        print("buy order")
        order.action = "SELL"

    if client.order_id:
        print("got order id, placing buy order")
        client.placeOrder(client.order_id, contract, order)


if __name__ == "__main__":
    client = IBClient('127.0.0.1', 7497, 1)

    time.sleep(1)
    chart = Chart(toolbox=True, width=1000, inner_width=0.6, inner_height=1)
    chart.legend(True)

    chart.topbar.textbox('symbol', INITIAL_SYMBOL)
    chart.topbar.switcher('timeframe', ("1 secs", "1 min", '5 mins', '1 hour'), default='5 mins', func=on_timeframe_selection)

    # hotkey to place a buy order
    chart.hotkey("shift", "O", place_order)

    # hotkey to place a sell order
    chart.hotkey("shift", "P", place_order)

    # set up a function to call when searching for symbol
    chart.events.search += on_search
    get_bar_data(INITIAL_SYMBOL, INITIAL_TIMEFRAME)
    time.sleep(1)

    chart.show(block=True)
