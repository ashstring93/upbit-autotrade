import os
from decimal import Decimal

# --- API 및 기본 설정 ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") 
ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")
TELEGRAM_CONFIG = {
    "ENABLE": True,
    "TOKEN": os.getenv("TELEGRAM_TOKEN"),
    "CHAT_ID": os.getenv("TELEGRAM_CHAT_ID")
}
TICKER_ALLOCATION = {
    "KRW-BTC": Decimal('0.6'),  
    "KRW-ETH": Decimal('0.4'),  
}
# 코인별 소수점 정밀도 설정
TICKER_CONFIG = {
    "KRW-BTC": Decimal('0.00000001'),
    "KRW-ETH": Decimal('0.000001')
}
FEE_RATE = Decimal('0.0005')
MIN_ORDER_KRW = 5000

# --- 파일 경로 설정 ---
STATE_FILE = "bot_state.json"
TRADE_LOG_FILE = "realtime_trade_log.csv"
LOG_FILE = "trading_bot_adv.log"
CAPITAL_LOG_FILE = "capital_log.csv"

# --- 전략 설정값 ---
STRATEGY_CONFIG = {
    # 기본 지표 설정
    "bbands_length": 20,
    "bbands_std": 2.0,
    "cci_length": 20,
    "cci_overbought": 100,
    "cci_oversold": -100,

    # AI 최종 판단을 위한 설정값
    "SHORT_RSI_LENGTH": 14,

    # SuperTrend Trailing Stop 설정
    "SUPERTREND_TIMEFRAME": "60m",
    "SUPERTREND_PERIOD": 14,
    "SUPERTREND_MULTIPLIER": 1.5,
}

# --- 리스크 관리 설정 ---
RISK_CONFIG = {
    "ENABLE_DAILY_LOSS_LIMIT": True,            # 기능 사용 여부
    "DAILY_LOSS_LIMIT_PERCENTAGE": Decimal('-0.05'), # -5% 한도
}