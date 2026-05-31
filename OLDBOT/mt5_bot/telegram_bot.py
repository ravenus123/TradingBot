# telegram notifications

import os
import sys
import json
import requests
from datetime import datetime
from pathlib import Path

# config
config_file = Path(__file__).parent / 'telegram_config.json'
if config_file.exists():
    with open(config_file, 'r') as f:
        config = json.load(f)
    TELEGRAM_BOT_TOKEN = config.get('bot_token', 'YOUR_BOT_TOKEN_HERE')
    TELEGRAM_CHAT_ID = config.get('chat_id', 'YOUR_CHAT_ID_HERE')
else:
    TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
    TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"


_telegram_warning_shown = False

def send_telegram_message(message, silent: bool = True):
    """
    Send a message to Telegram using the Bot API
    
    Args:
        message (str): Message text to send
        silent (bool): If True, suppress error messages (default True)
    
    Returns:
        bool: True if successful, False otherwise
    """
    global _telegram_warning_shown
    
    # Validate configuration
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        if not silent and not _telegram_warning_shown:
            print("❌ ERROR: Please configure your TELEGRAM_BOT_TOKEN in telegram_bot.py")
            _telegram_warning_shown = True
        return False
    
    if TELEGRAM_CHAT_ID == "YOUR_CHAT_ID_HERE":
        if not silent and not _telegram_warning_shown:
            print("❌ ERROR: Please configure your TELEGRAM_CHAT_ID in telegram_bot.py")
            _telegram_warning_shown = True
        return False
    
    # Build API URL
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Prepare message with timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"🤖 MT5 Trading Bot\n{timestamp}\n\n{message}"
    
    # Prepare request payload
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": full_message,
        "parse_mode": "HTML"
    }
    
    try:
        # Send POST request to Telegram API
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            return True
        else:
            if not silent:
                print(f"❌ Telegram API error: {response.status_code}")
                try:
                    print(response.text)
                except Exception:
                    pass
            return False
            
    except requests.exceptions.RequestException as e:
        if not silent:
            print(f"❌ Connection error: {e}")
        return False


def main():
    """Main entry point when script is called from MQL5"""
    
    # Check if message was provided as command line argument
    if len(sys.argv) < 2:
        print("Usage: python telegram_bot.py \"Your message here\"")
        print("\nExample:")
        print('python telegram_bot.py "🟢 BUY Trade Opened | Symbol: EURUSD | Price: 1.08500"')
        return
    
    # Get message from command line arguments
    message = sys.argv[1]
    
    # Send the message
    success = send_telegram_message(message)
    
    if success:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
