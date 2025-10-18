#!/usr/bin/env python3
"""
ibkr_ema_rsi_bot.py

Automated IBKR paper trading bot using an EMA crossover and RSI filter strategy
with dynamic stop-loss and profit-target management.
Polling historical bars at a fixed interval to avoid real-time data subscription issues.
All configuration is defined within this single file.

Usage:
  1. Install dependencies: pip install ib_insync pandas
  2. Ensure TWS is running (default port 7497)
  3. Run: python ibkr_ema_rsi_bot.py
"""

import time
import pandas as pd
from ib_insync import IB, Stock, MarketOrder, StopOrder, util

# ======== Configuration (edit values below as needed) ========
IB_HOST         = '127.0.0.1'    # TWS host
IB_PORT         = 7497           # TWS port (default paper/live port)
IB_CLIENT_ID    = 1              # Unique client ID for this session

TICKER          = 'AAPL'         # Symbol to trade
EXCHANGE        = 'SMART'        # Exchange for the contract
CURRENCY        = 'USD'          # Currency of the contract
ORDER_SIZE      = 10.0           # Quantity per order (shares or contracts)

EMA_FAST        = 10             # Fast EMA period
EMA_SLOW        = 20             # Slow EMA period
RSI_PERIOD      = 14             # RSI calculation period

HIST_DURATION   = '2 D'          # Historical window to fetch (must cover EMAs)
BAR_SIZE        = '1 min'        # Bar size (e.g., '1 min')
POLL_INTERVAL   = 60             # Seconds between polls
WHAT_TO_SHOW    = 'MIDPOINT'     # WhatToShow parameter
USE_RTH         = False          # Regular Trading Hours only

class EMARSITrader:
    def __init__(self, ib, contract):
        self.ib = ib
        self.contract = contract
        self.df = pd.DataFrame()
        self.position = 0
        self.stop_order = None
        self.target_order = None

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
        if df.empty:
            print("No historical bars returned. Check connection or parameters.")
            return df
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
        return df

    def evaluate(self):
        df = self.fetch_data()
        if df.empty or len(df) < max(EMA_SLOW, RSI_PERIOD) + 2:
            print("Not enough data yet. Waiting...")
            return

        prev = df.iloc[-2]
        latest = df.iloc[-1]
        print(
            f"Prev: {df.index[-2]} | Close: {prev.close:.2f}, EMA_F: {prev.EMA_Fast:.2f}, EMA_S: {prev.EMA_Slow:.2f}, RSI: {prev.RSI:.2f}\n"
            f"Latest: {df.index[-1]} | Close: {latest.close:.2f}, EMA_F: {latest.EMA_Fast:.2f}, EMA_S: {latest.EMA_Slow:.2f}, RSI: {latest.RSI:.2f}"
        )

        bullish = (prev['EMA_Fast'] < prev['EMA_Slow']) and (latest['EMA_Fast'] > latest['EMA_Slow'])
        bearish = (prev['EMA_Fast'] > prev['EMA_Slow']) and (latest['EMA_Fast'] < latest['EMA_Slow'])

        if self.position == 0:
            if bullish and latest['RSI'] > 50:
                self.place_order('BUY', latest)
            elif bearish and latest['RSI'] < 50:
                self.place_order('SELL', latest)
            else:
                print("No entry signal")
        else:
            self.manage_position(latest)

    def place_order(self, action, bar):
        print(f"Placing {action} market order @ {bar.close:.2f}")
        order = MarketOrder(action, ORDER_SIZE)
        trade = self.ib.placeOrder(self.contract, order)
        trade.filledEvent += self.on_filled

    def on_filled(self, trade, fill):
        price = getattr(fill, 'avgPrice', fill.price)
        self.position = fill.filled if trade.order.action == 'BUY' else -fill.filled
        print(f"Filled {trade.order.action} {abs(self.position)} @ {price:.2f}")
        self.set_stop_target()

    def set_stop_target(self):
        df = self.fetch_data()
        if df.empty:
            return
        prev = df.iloc[-2]
        if self.position > 0:
            stop_px = prev['low']
            target_px = prev['high']
            stop_ord = StopOrder('SELL', abs(self.position), stop_px)
            target_ord = MarketOrder('SELL', abs(self.position), lmtPrice=target_px)
        else:
            stop_px = prev['high']
            target_px = prev['low']
            stop_ord = StopOrder('BUY', abs(self.position), stop_px)
            target_ord = MarketOrder('BUY', abs(self.position), lmtPrice=target_px)
        self.stop_order = self.ib.placeOrder(self.contract, stop_ord)
        self.target_order = self.ib.placeOrder(self.contract, target_ord)
        print(f"Stop set @ {stop_px:.2f}, Target set @ {target_px:.2f}")

    def manage_position(self, bar):
        print("Re-managing existing position")
        if self.stop_order:
            self.ib.cancelOrder(self.stop_order)
        if self.target_order:
            self.ib.cancelOrder(self.target_order)
        self.set_stop_target()


def main():
    ib = IB()
    ib.connect(IB_HOST, IB_PORT, IB_CLIENT_ID)
    print(f"Connected to IBKR at {IB_HOST}:{IB_PORT}")
    contract = Stock(TICKER, EXCHANGE, CURRENCY)
    trader = EMARSITrader(ib, contract)
    print("Starting polling loop...")
    try:
        while True:
            trader.evaluate()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("Stopped by user")
        ib.disconnect()

if __name__ == '__main__':
    main()
