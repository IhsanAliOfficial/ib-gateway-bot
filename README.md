
# IB Gateway Python Integration

Connects to IB Gateway via `ibapi`, fetches live account summary and real-time PnL updates.

## Features
- Fetch account summary: net liquidation, cash, margin requirements, available funds  
- Subscribe to real-time PnL (daily, unrealized, realized)  
- Console-logging of all requests, responses and errors  

## Prerequisites
- Python 3.10+  
- IB Gateway or TWS running with API enabled (default port 7497)  
- IB API package:  
  ```bash
  pip install ibapi
```

## Getting Started

1. **Clone or download** this repo
2. **Configure** your IB Gateway host/port/clientId in `bot.py` if needed
3. **Run** the script:

   ```bash
   python bot.py
   ```

Logs will show connection steps, account-summary lines and PnL updates.
