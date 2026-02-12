import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta, UTC
from dotenv import load_dotenv
from colorama import init, Fore, Style

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print(Fore.RED + "❌ Ошибка: TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не найдены в .env")
    sys.exit(1)

THRESHOLD = 7.0                     # % размаха для срабатывания
COOLDOWN = 60                      # секунд между алертами
MIN_VOLUME_USD = 0                 # 0 = отключено (вообще не фильтруем)
INTERVAL = 10                      # !!! ВАЖНО: это частота опроса API (10 секунд)
SHOW_MOVEMENTS = True             # показывать движения в консоли
SHOW_ALL_MOVEMENTS = False        # False = только >2%

SPLASH_INTERVAL = 60              # СБОР HIGH/LOW за 60 секунд (1 минута)

MEXC_FUTURES_TICKER_URL = "https://contract.mexc.com/api/v1/contract/ticker"

init(autoreset=True)

# ---------- ХРАНИЛИЩЕ ----------
previous_prices = {}
price_high = {}
price_low = {}
last_alert_time = {}
symbol_info = {}

# ---------- ПОЛУЧЕНИЕ ДАННЫХ ----------
def get_all_futures_tickers():
    try:
        resp = requests.get(MEXC_FUTURES_TICKER_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data.get('success'):
            return None
        tickers = data.get('data', [])
        print(Fore.CYAN + f"📡 Получено {len(tickers)} контрактов")
        return tickers
    except Exception as e:
        print(Fore.RED + f"❌ Ошибка запроса: {e}")
        return None

# ---------- ИНИЦИАЛИЗАЦИЯ ----------
def init_symbols_from_tickers(tickers):
    symbols = []
    for contract in tickers:
        symbol = contract.get('symbol')
        if symbol and symbol.endswith('_USDT'):
            symbols.append(symbol)
            base = symbol.replace('_USDT', '')
            symbol_info[symbol] = {'base': base, 'quote': 'USDT'}
    return symbols

# ---------- ФОРМАТ СООБЩЕНИЯ (ТВОЙ ИДЕАЛЬНЫЙ ДИЗАЙН С РАЗМАХОМ) ----------
def format_alert(symbol, move_pct, high_price, low_price, volume_usd, alert_time):
    base = symbol_info.get(symbol, {}).get('base', symbol.replace('_USDT', ''))
    
    if move_pct > 0:
        direction = "🟢"
        move_str = f"+{move_pct:.2f}%"
        current_price = high_price
    else:
        direction = "🔴"
        move_str = f"{move_pct:.2f}%"
        current_price = low_price
    
    # Форматирование цен
    if current_price >= 1000:
        high_str = f"${high_price:,.2f}"
        low_str = f"${low_price:,.2f}"
        price_str = f"${current_price:,.2f}"
    elif current_price >= 1:
        high_str = f"${high_price:.2f}"
        low_str = f"${low_price:.2f}"
        price_str = f"${current_price:.2f}"
    else:
        high_str = f"${high_price:.6f}"
        low_str = f"${low_price:.6f}"
        price_str = f"${current_price:.6f}"
    
    # Объём
    if volume_usd >= 1e9:
        vol_str = f"${volume_usd/1e9:.2f}B"
    elif volume_usd >= 1e6:
        vol_str = f"${volume_usd/1e6:.2f}M"
    elif volume_usd >= 1e3:
        vol_str = f"${volume_usd/1e3:.2f}K"
    else:
        vol_str = f"${volume_usd:.2f}"
    
    tz_offset = timedelta(hours=3)
    local_time = (alert_time + tz_offset).strftime("%H:%M:%S")
    
    return f"""
🚨 ВСПЛЕСК НА MEXC 🚨

───◇───────────────
🔖 Token: ${base}
📊 Move:     {move_str}

MAX: {high_str}
MIN: {low_str}

💵 Price:     {price_str}
📦 Volume 24h: {vol_str}
⏰ Time:    {local_time} UTC+3
───◇───────────────
😎 @LBScalp
📉 @aslgw
""".strip()

# ---------- ОТПРАВКА В TELEGRAM ----------
def send_telegram_alert(text, mexc_url):
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 MEXC Futures", url=mexc_url)],
            [InlineKeyboardButton("📢 LBScalp", url="https://t.me/LBScalp")]
        ])
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML',
            'reply_markup': keyboard.to_json()
        }
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json=payload, timeout=10)
        print(Fore.GREEN + "✅ Алерт отправлен в Telegram!")
    except Exception as e:
        print(Fore.RED + f"❌ Ошибка отправки: {e}")

# ---------- ОБРАБОТКА ТИКЕРОВ ----------
def process_tickers(tickers, now_utc, now_ts, cycle_count):
    alerts_count = 0
    
    for contract in tickers:
        symbol = contract.get('symbol')
        if not symbol or not symbol.endswith('_USDT'):
            continue
        
        if symbol not in symbol_info:
            base = symbol.replace('_USDT', '')
            symbol_info[symbol] = {'base': base, 'quote': 'USDT'}
            print(Fore.CYAN + f"➕ Добавлен: {symbol}")
        
        current_price = float(contract.get('lastPrice', 0))
        volume_24h = float(contract.get('volume24', 0))
        
        if current_price == 0:
            continue
        if MIN_VOLUME_USD > 0 and volume_24h < MIN_VOLUME_USD:
            continue
        
        # ---------- ИНИЦИАЛИЗАЦИЯ HIGH/LOW ----------
        if symbol not in price_high:
            price_high[symbol] = current_price
            price_low[symbol] = current_price
        else:
            # Обновляем максимум и минимум
            if current_price > price_high[symbol]:
                price_high[symbol] = current_price
            if current_price < price_low[symbol]:
                price_low[symbol] = current_price
        
        # ---------- ПРОВЕРКА КАЖДЫЕ SPLASH_INTERVAL СЕКУНД ----------
        if cycle_count % (SPLASH_INTERVAL // INTERVAL) == 0:
            if symbol in price_high and symbol in price_low:
                high = price_high[symbol]
                low = price_low[symbol]
                
                if low > 0:
                    move_pct = ((high - low) / low) * 100
                    
                    # Логируем движения (SHOW_MOVEMENTS = True)
                    if SHOW_MOVEMENTS:
                        if move_pct >= 2.0 or SHOW_ALL_MOVEMENTS:
                            direction = "📈" if move_pct > 0 else "📉"
                            if current_price >= 1:
                                print(Fore.YELLOW + f"{direction} {symbol}: размах {move_pct:+.2f}% | HIGH: ${high:.2f}, LOW: ${low:.2f}")
                            else:
                                print(Fore.YELLOW + f"{direction} {symbol}: размах {move_pct:+.2f}% | HIGH: ${high:.6f}, LOW: ${low:.6f}")
                    
                    # Проверяем порог
                    if move_pct >= THRESHOLD:
                        last_time = last_alert_time.get(symbol, 0)
                        if now_ts - last_time >= COOLDOWN:
                            msg = format_alert(symbol, move_pct, high, low, volume_24h, now_utc)
                            base = symbol_info[symbol]['base']
                            mexc_link = f"https://www.mexc.com/ru-RU/futures/{base}_USDT?type=linear_swap"
                            
                            print(Fore.MAGENTA + "\n" + "🚨 АЛЕРТ! " + "="*45)
                            print(msg)
                            print(Fore.MAGENTA + "="*60 + "\n")
                            
                            send_telegram_alert(msg, mexc_link)
                            last_alert_time[symbol] = now_ts
                            alerts_count += 1
                
                # Сбрасываем HIGH/LOW для следующего интервала
                price_high[symbol] = current_price
                price_low[symbol] = current_price
        
        # Сохраняем цену
        previous_prices[symbol] = current_price
    
    return alerts_count

# ---------- ГЛАВНЫЙ ЦИКЛ ----------
def main():
    print(Fore.CYAN + Style.BRIGHT + "\n⚡ MEXC SPLASH PARSER ⚡")
    print(Fore.CYAN + "="*60)
    print(Fore.CYAN + f"📊 Порог: {THRESHOLD}% | Cooldown: {COOLDOWN}s")
    print(Fore.CYAN + f"🔄 Опрос API: каждые {INTERVAL}s | Сбор HIGH/LOW: {SPLASH_INTERVAL}s")
    print(Fore.CYAN + f"💰 Min Volume: {'ВЫКЛЮЧЕН' if MIN_VOLUME_USD == 0 else f'${MIN_VOLUME_USD:,}'}")
    print(Fore.CYAN + "✅ Режим: ВСЕ USDT-КОНТРАКТЫ (HIGH/LOW)")
    print(Fore.CYAN + "="*60 + "\n")
    
    # ПЕРВЫЙ ЗАПРОС
    print(Fore.YELLOW + "🔄 Получение списка контрактов...")
    first_tickers = get_all_futures_tickers()
    if not first_tickers:
        print(Fore.RED + "❌ Не удалось получить данные.")
        sys.exit(1)
    
    symbols = init_symbols_from_tickers(first_tickers)
    if not symbols:
        print(Fore.RED + "❌ Нет USDT-контрактов.")
        sys.exit(1)
    
    print(Fore.GREEN + f"📡 Получено {len(first_tickers)} контрактов")
    print(Fore.GREEN + f"✅ Загружено {len(symbols)} USDT-контрактов")
    print(Fore.GREEN + f"📋 Первые 5: {symbols[:5]}")
    print(Fore.GREEN + f"📋 Последние 5: {symbols[-5:]}\n")
    
    # ИНИЦИАЛИЗАЦИЯ HIGH/LOW
    for contract in first_tickers:
        symbol = contract.get('symbol')
        if symbol and symbol.endswith('_USDT'):
            price = float(contract.get('lastPrice', 0))
            previous_prices[symbol] = price
            price_high[symbol] = price
            price_low[symbol] = price
    
    print(Fore.GREEN + f"✅ Погнали! Собираем MAX/MIN за {SPLASH_INTERVAL} секунд...\n")
    
    cycle_count = 0
    total_alerts = 0
    
    while True:
        try:
            cycle_start = time.time()
            cycle_count += 1
            now_utc = datetime.now(UTC)
            now_ts = now_utc.timestamp()
            
            tickers = get_all_futures_tickers()
            
            if tickers:
                alerts = process_tickers(tickers, now_utc, now_ts, cycle_count)
                total_alerts += alerts
                
                # Статистика каждые 6 циклов
                if cycle_count % 6 == 0:
                    print(Fore.CYAN + f"\n📈 Статистика: циклов: {cycle_count}, алертов: {total_alerts}, контрактов: {len(previous_prices)}\n")
            
            # Пауза 10 секунд (ТВОЙ INTERVAL)
            elapsed = time.time() - cycle_start
            time.sleep(max(0.1, INTERVAL - elapsed))
            
        except KeyboardInterrupt:
            print(Fore.YELLOW + "\n⏹️ Скрипт остановлен.")
            print(Fore.GREEN + f"📊 Итог: циклов: {cycle_count}, алертов: {total_alerts}")
            sys.exit(0)
        except Exception as e:
            print(Fore.RED + f"❌ Ошибка: {e}")
            time.sleep(INTERVAL)

if __name__ == "__main__":
    main()