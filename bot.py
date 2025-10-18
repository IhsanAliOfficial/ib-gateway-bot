import time
import random
import logging
import pandas as pd
from dotenv import load_dotenv
import os
from ib_insync import IB, Stock, MarketOrder, StopOrder, util, BarData

# ========= Load .env =========
load_dotenv()

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", random.randint(1, 999)))
TICKER = os.getenv("TICKER", "AAPL")
ORDER_SIZE = float(os.getenv("ORDER_SIZE", 10))
EMA_FAST = int(os.getenv("EMA_FAST", 5))
EMA_SLOW = int(os.getenv("EMA_SLOW", 10))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
DUMMY_MODE = os.getenv("DUMMY_MODE", "False").lower() == "true"

BAR_SIZE = "1 min"
HIST_DURATION = "2 D"
POLL_INTERVAL = 10
WHAT_TO_SHOW = "MIDPOINT"
USE_RTH = False

# ========= Setup Logging =========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("ibkr_bot_dummy.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ========= Dummy IB Class (for offline testing) =========
class DummyIB:
    """Simulated IBKR class for offline testing"""
    def __init__(self):
        self.position = 0

    def reqHistoricalData(self, *args, **kwargs):
        """Generate fake price data as BarData objects"""
        import numpy as np
        times = pd.date_range(end=pd.Timestamp.now(), periods=100, freq="1min")
        prices = 170 + np.cumsum(np.random.randn(100)) * 0.2

        bars = []
        for i in range(100):
            bars.append(
                BarData(
                    date=times[i].strftime("%Y%m%d %H:%M:%S"),
                    open=prices[i] + random.uniform(-0.1, 0.1),
                    high=prices[i] + 0.3,
                    low=prices[i] - 0.3,
                    close=prices[i],
                    volume=random.randint(1000, 5000),
                    average=prices[i],
                    barCount=1
                )
            )
        return bars

    def positions(self):
        return []

    def placeOrder(self, contract, order):
        print(f"[DUMMY] Simulating {order.action} {order.totalQuantity} shares...")
        return True


# ========= Trader Class =========
class EMARSITrader:
    def __init__(self, ib, contract):
        self.ib = ib
        self.contract = contract
        self.position = 0

    def fetch_data(self):
        bars = self.ib.reqHistoricalData(
            self.contract,
            endDateTime='',
            durationStr=HIST_DURATION,
            barSizeSetting=BAR_SIZE,
            whatToShow=WHAT_TO_SHOW,
            useRTH=USE_RTH,
            formatDate=1
        )
        df = util.df(bars)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df['EMA_Fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
        df['EMA_Slow'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()

        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=RSI_PERIOD).mean()
        avg_loss = loss.rolling(window=RSI_PERIOD).mean()
        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))
        df['RSI'] = df['RSI'].fillna(50)
        return df

    def evaluate(self):
        df = self.fetch_data()
        if df.empty:
            print("No data fetched.")
            return
        prev = df.iloc[-2]
        latest = df.iloc[-1]

        bullish = latest['EMA_Fast'] > latest['EMA_Slow'] and latest['RSI'] > 55
        bearish = latest['EMA_Fast'] < latest['EMA_Slow'] and latest['RSI'] < 45

        print(f"\n=== {self.contract.symbol} Update ===")
        print(f"Close: {latest['close']:.2f} | EMA_Fast: {latest['EMA_Fast']:.2f} | EMA_Slow: {latest['EMA_Slow']:.2f} | RSI: {latest['RSI']:.2f}")

        if self.position == 0:
            if bullish:
                self.position += ORDER_SIZE
                print(f"[DUMMY] 🚀 BUY Signal -> {ORDER_SIZE} @ {latest['close']:.2f}")
            elif bearish:
                self.position -= ORDER_SIZE
                print(f"[DUMMY] 🔻 SELL Signal -> {ORDER_SIZE} @ {latest['close']:.2f}")
            else:
                print("[INFO] No trade signal.")
        else:
            print(f"[HOLDING] Current position: {self.position}")


# ========= Main =========
def main():
    print("🧠 Starting EMA-RSI Bot (Dummy Mode)" if DUMMY_MODE else "⚙️ Starting Live Bot")

    ib = DummyIB() if DUMMY_MODE else IB()
    if not DUMMY_MODE:
        ib.connect(IB_HOST, IB_PORT, IB_CLIENT_ID)

    contract = Stock(TICKER, "SMART", "USD", primaryExchange="NASDAQ")
    trader = EMARSITrader(ib, contract)

    while True:
        trader.evaluate()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
