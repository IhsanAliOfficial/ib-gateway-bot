
import time
import pandas as pd
from ib_insync import IB, Stock, MarketOrder, StopOrder, util
import logging
import random

# ======== Configuration (edit values below as needed) ========
IB_HOST         = '127.0.0.1'    # TWS host
IB_PORT         = 7497           # Paper trading (default). Use 7496 for live trading
IB_CLIENT_ID    = random.randint(1, 1000)  # Unique client ID for this session

TICKER          = 'AAPL'         # Symbol to trade
EXCHANGE        = 'SMART'        # Exchange for the contract
CURRENCY        = 'USD'          # Currency of the contract
ORDER_SIZE      = 10.0           # Quantity per order (shares or contracts)

EMA_FAST        = 5              # Fast EMA period
EMA_SLOW        = 10             # Slow EMA period
RSI_PERIOD      = 14             # RSI calculation period

HIST_DURATION   = '2 D'          # Historical window to fetch (must cover EMAs)
BAR_SIZE        = '1 min'        # Bar size (e.g., '1 min')
POLL_INTERVAL   = 60             # Seconds between polls
WHAT_TO_SHOW    = 'MIDPOINT'     # WhatToShow parameter
USE_RTH         = False          # Regular Trading Hours only

# Setup logging
logging.basicConfig(level=logging.INFO, filename='ibkr_bot.log', format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class EMARSITrader:
    def __init__(self, ib, contract):
        self.ib = ib
        self.contract = contract
        self.df = pd.DataFrame()
        self.position = 0
        self.stop_order = None
        self.target_order = None
        self.ib.updatePortfolioEvent += self.on_update_portfolio
        self.ib.execDetailsEvent += self.on_exec_details
        self.ib.commissionReportEvent += self.on_commission_report

    def on_update_portfolio(self, item):
        if (item.contract.symbol == self.contract.symbol and 
            item.contract.currency == self.contract.currency):
            self.position = item.position
            logger.info(f"Portfolio update for {self.contract.symbol}: Position={self.position}, MarketPrice={item.marketPrice:.2f}, UnrealizedPNL={item.unrealizedPNL:.2f}")

    def on_exec_details(self, trade, fill):
        if (fill.contract.symbol == self.contract.symbol and
            fill.contract.currency == self.contract.currency):
            price = fill.execution.avgPrice if hasattr(fill.execution, 'avgPrice') else fill.execution.price
            qty = fill.execution.shares if fill.execution.side == 'BOT' else -fill.execution.shares
            self.position += qty
            logger.info(f"Execution: {fill.execution.side} {abs(qty)} shares @ {price:.2f}, New position: {self.position}")
            self.set_stop_target()

    def on_commission_report(self, trade, fill, commission_report):
        logger.info(f"Commission: {commission_report.commission:.2f} {commission_report.currency}, Realized P&L: {commission_report.realizedPNL:.2f}")

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
        if df.empty or df['close'].isna().any():
            logger.warning("Invalid or empty data returned. Check connection or parameters.")
            return pd.DataFrame()
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return self.calculate_indicators(df)

    def calculate_indicators(self, df):
        df['EMA_Fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
        df['EMA_Slow'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=RSI_PERIOD).mean()
        avg_loss = loss.rolling(window=RSI_PERIOD).mean()
        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))
        df['RSI'] = df['RSI'].fillna(50)  # Default to neutral RSI if NaN
        return df

    def sync_position(self):
        positions = self.ib.positions()
        for pos in positions:
            if pos.contract.conId == self.contract.conId or (
                pos.contract.symbol == self.contract.symbol and
                pos.contract.currency == self.contract.currency and
                pos.contract.primaryExchange == 'NASDAQ'
            ):
                self.position = pos.position
                logger.info(f"Synced position for {self.contract.symbol}: {self.position}")
                return
        self.position = 0
        logger.info(f"No position found for {self.contract.symbol}, set to: {self.position}")

    def evaluate(self):
        self.sync_position()
        df = self.fetch_data()
        if df.empty or len(df) < max(EMA_SLOW, RSI_PERIOD) + 2:
            logger.warning("Not enough data yet. Waiting...")
            return

        prev = df.iloc[-2]
        latest = df.iloc[-1]
        logger.info(
            f"Prev: {df.index[-2]} | Close: {prev.close:.2f}, EMA_F: {prev.EMA_Fast:.2f}, EMA_S: {prev.EMA_Slow:.2f}, RSI: {prev.RSI:.2f}\n"
            f"Latest: {df.index[-1]} | Close: {latest.close:.2f}, EMA_F: {latest.EMA_Fast:.2f}, EMA_S: {latest.EMA_Slow:.2f}, RSI: {latest.RSI:.2f}"
        )

        bullish = latest['EMA_Fast'] > latest['EMA_Slow'] and latest['RSI'] > 50
        bearish = latest['EMA_Fast'] < latest['EMA_Slow'] and latest['RSI'] < 50

        if self.position == 0:
            if bullish:
                self.place_order('BUY', latest)
            elif bearish:
                self.place_order('SELL', latest)
            else:
                logger.info("No entry signal")
        else:
            logger.info(f"Managing existing position: {self.position}")
            self.manage_position(latest)

    def place_order(self, action, bar):
        logger.info(f"Placing {action} market order @ {bar.close:.2f}")
        order = MarketOrder(action, ORDER_SIZE)
        trade = self.ib.placeOrder(self.contract, order)
        trade.filledEvent += self.on_filled

    def on_filled(self, trade, fill):
        price = fill.execution.avgPrice if hasattr(fill.execution, 'avgPrice') else fill.execution.price
        self.position = fill.filled if trade.order.action == 'BUY' else -fill.filled
        logger.info(f"Filled {trade.order.action} {abs(self.position)} @ {price:.2f}")
        self.set_stop_target()

    def set_stop_target(self):
        df = self.fetch_data()
        if df.empty:
            logger.warning("No data to set stop/target")
            return
        prev = df.iloc[-2]
        if self.position > 0:
            stop_px = prev['low']
            target_px = prev['high']
            stop_ord = StopOrder('SELL', abs(self.position), stop_px)
            target_ord = MarketOrder('SELL', abs(self.position), lmtPrice=target_px)
        elif self.position < 0:
            stop_px = prev['high']
            target_px = prev['low']
            stop_ord = StopOrder('BUY', abs(self.position), stop_px)
            target_ord = MarketOrder('BUY', abs(self.position), lmtPrice=target_px)
        else:
            logger.info("No position to set stop/target")
            return
        self.stop_order = self.ib.placeOrder(self.contract, stop_ord)
        self.target_order = self.ib.placeOrder(self.contract, target_ord)
        if self.stop_order.orderId and self.target_order.orderId:
            logger.info(f"Stop set @ {stop_px:.2f}, Target set @ {target_px:.2f}")
        else:
            logger.warning("Failed to place stop/target orders")

    def manage_position(self, bar):
        logger.info("Re-managing existing position")
        if self.stop_order:
            self.ib.cancelOrder(self.stop_order)
        if self.target_order:
            self.ib.cancelOrder(self.target_order)
        self.set_stop_target()

def connect_with_retry(ib, host, port, client_id, max_attempts=5, delay=5):
    for attempt in range(max_attempts):
        try:
            ib.connect(host, port, client_id)
            logger.info(f"Connected to IBKR at {host}:{port}")
            return True
        except Exception as e:
            logger.error(f"Connection attempt {attempt+1} failed: {e}")
            time.sleep(delay)
    logger.error("Failed to connect after max attempts")
    return False

def main():
    ib = IB()
    if not connect_with_retry(ib, IB_HOST, IB_PORT, IB_CLIENT_ID):
        return
    contract = Stock(TICKER, EXCHANGE, CURRENCY, primaryExchange='NASDAQ')
    trader = EMARSITrader(ib, contract)
    logger.info("Starting polling loop...")
    try:
        while True:
            trader.evaluate()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        ib.disconnect()

if __name__ == '__main__':
    main()