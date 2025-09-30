from trader_app import TradingApp
import config as C
import threading


def main():
    app = TradingApp(C.HOST, C.PORT, C.CLIENT_ID)
    t = threading.Thread(target=app.run, daemon=True)
    t.start()
    
    # let it run; internal guards stop trades outside windows
    try:
        t.join()
    except KeyboardInterrupt:
        print("Shutting down...")
        app.disconnect()


if __name__ == "__main__":
    main()
    