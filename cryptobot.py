import time
import requests
import pandas as pd
import hmac
import hashlib
import json
import datetime

# --- YOUR COINDCX API CREDENTIALS ---
API_KEY = "b8504fcb8259c47834e79b507cdc45e4b49a8052953fbebc"
SECRET_KEY = "39968d26602485acea663358593b0337772199cb7c1dfe87b6f33ca412295ec6"

# --- TELEGRAM BOT CONFIGURATION ---
TELEGRAM_BOT_TOKEN = "8778032944:AAF5Tg85Zx9ctU20rvyAWpEw4z42y48eSM8"
TELEGRAM_CHAT_ID = "5395581406"

BASE_URL = "https://api.coindcx.com"
PAIR = "B-BTC_USDT"           # Candle data pair
FUTURES_MARKET = "BTCUSDT"    # Futures Pair Name

LEVERAGE = 3                  # 3x Safe Leverage
MARGIN_USAGE_PCT = 0.25       # 25% Wallet Balance per trade

# Position & State Tracking
current_position = None       # None, 'LONG', or 'SHORT'
entry_price = 0.0
target_price = 0.0
stop_loss_price = 0.0
trade_quantity = 0.0

# 12-Hour Reporting Counter & Metrics
loop_count = 0
total_trades = 0
target_hits = 0
sl_hits = 0
total_profit_usdt = 0.0
total_loss_usdt = 0.0

# --- TELEGRAM MESSAGE FUNCTION ---
def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram Token/Chat ID missing.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"Failed to send Telegram message: {response.text}")
    except Exception as e:
        print("Telegram API Error:", e)

# --- 1. FETCH & CALCULATE MULTI-STRATEGY INDICATORS ---
def fetch_futures_candles():
    url = f"{BASE_URL}/market_data/candles?pair={PAIR}&interval=15m&limit=100"
    response = requests.get(url)
    data = response.json()

    if not isinstance(data, list):
        print("API Error:", data)
        return None

    df = pd.DataFrame(data)
    df['time'] = pd.to_numeric(df['time'])
    df = df.sort_values(by='time', ascending=True).reset_index(drop=True)
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)

    # 1. EMA 9/21
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()

    # 2. RSI 14
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi_14'] = 100 - (100 / (1 + rs))

    # 3. Bollinger Bands (20, 2)
    df['sma_20'] = df['close'].rolling(window=20).mean()
    df['std_20'] = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['sma_20'] + (df['std_20'] * 2)
    df['bb_lower'] = df['sma_20'] - (df['std_20'] * 2)

    return df

# --- 2. DYNAMIC QUANTITY (25% MARGIN CALCULATION) ---
def get_calculated_quantity(current_price):
    wallet_balance_usdt = 48.0  # Simulated Balance

    used_margin = wallet_balance_usdt * MARGIN_USAGE_PCT  # 25% = $12
    position_value = used_margin * LEVERAGE                # 3x = $36

    btc_qty = round(position_value / current_price, 4)
    return btc_qty if btc_qty >= 0.0001 else 0.001

# --- 3. COINDCX ORDER EXECUTION ---
def place_futures_order(side, position_intent, quantity):
    timeStamp = int(round(time.time() * 1000))
    body = {
        "side": side,
        "order_type": "market_order",
        "market": FUTURES_MARKET,
        "total_quantity": quantity,
        "leverage": LEVERAGE,
        "position_intent": position_intent,
        "timestamp": timeStamp
    }
    json_body = json.dumps(body, separators=(',', ':'))
    signature = hmac.new(SECRET_KEY.encode('utf-8'), json_body.encode('utf-8'), hashlib.sha256).hexdigest()

    headers = {
        'Content-Type': 'application/json',
        'X-AUTH-APIKEY': API_KEY,
        'X-AUTH-SIGNATURE': signature
    }

    url = f"{BASE_URL}/exchange/v1/derivatives/futures/orders/create"
    response = requests.post(url, data=json_body, headers=headers)
    return response.json()

# --- 4. EXACT CANDLE CLOCK SYNCHRONIZATION ---
def wait_for_next_candle():
    now = datetime.datetime.now()
    minutes_to_next = 15 - (now.minute % 15)
    next_time = now + datetime.timedelta(minutes=minutes_to_next)
    next_time = next_time.replace(second=2, microsecond=0)

    sleep_seconds = (next_time - now).total_seconds()
    print(f"\n⏳ Syncing: Waiting {round(sleep_seconds)} seconds for candle close ({next_time.strftime('%H:%M:%S')})...")
    time.sleep(sleep_seconds)

# --- 5. MULTI-STRATEGY LOGIC & REPORTING ---
def run_futures_strategy():
    global current_position, entry_price, target_price, stop_loss_price, trade_quantity
    global loop_count, total_trades, target_hits, sl_hits, total_profit_usdt, total_loss_usdt

    loop_count += 1
    df = fetch_futures_candles()
    if df is None or len(df) < 25:
        return

    last_closed = df.iloc[-2]
    prev_closed = df.iloc[-3]
    current_price = df.iloc[-1]['close']

    # Indicators Logic
    ema_long = (
        (prev_closed['ema_9'] <= prev_closed['ema_21']) and
        (last_closed['ema_9'] > last_closed['ema_21']) and
        (last_closed['rsi_14'] > 50)
    )

    ema_short = (
        (prev_closed['ema_9'] >= prev_closed['ema_21']) and
        (last_closed['ema_9'] < last_closed['ema_21']) and
        (last_closed['rsi_14'] < 50)
    )

    bb_long = (last_closed['close'] > last_closed['bb_upper']) and (last_closed['rsi_14'] > 55)
    bb_short = (last_closed['close'] < last_closed['bb_lower']) and (last_closed['rsi_14'] < 45)

    long_signal = ema_long or bb_long
    short_signal = ema_short or bb_short

    print(f"[{time.strftime('%H:%M:%S')}] Live Price: ${current_price} | RSI: {round(last_closed['rsi_14'],2)} | Position: {current_position}")

    # --- NO ACTIVE POSITION ---
    if current_position is None:
        if long_signal:
            print("\n📈 CONFIRMED LONG SIGNAL DETECTED! Opening Long Position...")
            entry_price = current_price
            target_price = entry_price * 1.015      # +1.5% Profit
            stop_loss_price = entry_price * 0.990    # -1.0% Stop Loss
            trade_quantity = get_calculated_quantity(current_price)

            # res = place_futures_order(side="buy", position_intent="open_long", quantity=trade_quantity)

            current_position = 'LONG'
            total_trades += 1
            print(f"LONG Entry: ${entry_price} | Qty: {trade_quantity} BTC | Target: ${round(target_price,2)} | SL:${round(stop_loss_price,2)}")
        elif short_signal:
            print("\n📉 CONFIRMED SHORT SIGNAL DETECTED! Opening Short Position...")
            entry_price = current_price
            target_price = entry_price * 0.985      # +1.5% Profit
            stop_loss_price = entry_price * 1.010    # -1.0% Stop Loss
            trade_quantity = get_calculated_quantity(current_price)

            # res = place_futures_order(side="sell", position_intent="open_short", quantity=trade_quantity)

            current_position = 'SHORT'
            total_trades += 1
            print(f"SHORT Entry: ${entry_price} | Qty: {trade_quantity} BTC | Target: ${round(target_price,2)} | SL:${round(stop_loss_price,2)}")
    # --- MANAGING LONG POSITION ---
    elif current_position == 'LONG':
        if current_price >= target_price:
            pnl = (target_price - entry_price) * trade_quantity
            total_profit_usdt += pnl
            target_hits += 1
            print(f"\n🎯 LONG TARGET REACHED! Profit: +${round(pnl, 2)}")
            # place_futures_order(side="sell", position_intent="close_long", quantity=trade_quantity)
            current_position = None

        elif current_price <= stop_loss_price:
            pnl = (entry_price - stop_loss_price) * trade_quantity
            total_loss_usdt += pnl
            sl_hits += 1
            print(f"\n🛑 LONG STOP LOSS HIT! Loss: -${round(pnl, 2)}")
            # place_futures_order(side="sell", position_intent="close_long", quantity=trade_quantity)
            current_position = None

    # --- MANAGING SHORT POSITION ---
    elif current_position == 'SHORT':
        if current_price <= target_price:
            pnl = (entry_price - target_price) * trade_quantity
            total_profit_usdt += pnl
            target_hits += 1
            print(f"\n🎯 SHORT TARGET REACHED! Profit: +${round(pnl, 2)}")
            # place_futures_order(side="buy", position_intent="close_short", quantity=trade_quantity)
            current_position = None

        elif current_price >= stop_loss_price:
            pnl = (stop_loss_price - entry_price) * trade_quantity
            total_loss_usdt += pnl
            sl_hits += 1
            print(f"\n🛑 SHORT STOP LOSS HIT! Loss: -${round(pnl, 2)}")
            # place_futures_order(side="buy", position_intent="close_short", quantity=trade_quantity)
            current_position = None

    # --- 12-HOUR PERFORMANCE REPORT ---
    if loop_count % 48 == 0:
        win_rate = round((target_hits / total_trades * 100), 2) if total_trades > 0 else 0
        net_pnl = total_profit_usdt - total_loss_usdt

        report_msg = (
            "📊 *12-HOUR TRADING BOT SUMMARY REPORT* 📊\n"
            f"• *Market Pair:* `{FUTURES_MARKET}`\n"
            f"• *Total Trades Taken:* {total_trades}\n"
            f"• *Target Hits (Profit):* {target_hits} 🎯\n"
            f"• *Stop Loss Hits (Loss):* {sl_hits} 🛑\n"
            f"• *Win Rate:* {win_rate}%\n"
            f"─────────────────\n"
            f"• *Total Profit:* +${round(total_profit_usdt, 2)} USDT\n"
            f"• *Total Loss:* -${round(total_loss_usdt, 2)} USDT\n"
            f"• *Net PnL:* *{'+$' if net_pnl >= 0 else '-$'}{abs(round(net_pnl, 2))} USDT*\n"
        )

        print("\n" + "="*50)
        print(report_msg)
        print("="*50 + "\n")

        send_telegram_message(report_msg)

# --- 6. 24/7 AUTOMATIC LOOP WITH CLOCK SYNC ---
if __name__ == "__main__":
    start_msg = "🤖 *BTC Futures Trading Bot Started*\nClock-Synced 15m Strategy Active.\n12-Hour reporting enabled."
    print(start_msg)
    send_telegram_message(start_msg)

    wait_for_next_candle()

    while True:
        try:
            run_futures_strategy()
        except Exception as e:
            print("Error occurred:", e)

        wait_for_next_candle()
