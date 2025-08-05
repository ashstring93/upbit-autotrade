import os
from dotenv import load_dotenv
from decimal import Decimal

load_dotenv()

# --- API 및 기본 연결 설정 ---
ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY", "YOUR_ACCESS_KEY")
SECRET_KEY = os.getenv("UPBIT_SECRET_KEY", "YOUR_SECRET_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") 

API_CALL_DELAY_SEC = 0.3

# --- 거래 규칙 및 대상 설정 ---
TICKER_ALLOCATION = {
    "KRW-BTC": Decimal('0.25'),
    "KRW-ETH": Decimal('0.25'),
    "KRW-XRP": Decimal('0.25'),
    "KRW-USDT": Decimal('0.25')
}
# 코인별 소수점 정밀도 설정
TICKER_CONFIG = {
    "KRW-BTC": Decimal('0.00000001'),
    "KRW-ETH": Decimal('0.000001'),
    "KRW-XRP": Decimal('1'),
    "KRW-USDT": Decimal('0.001')
}
FEE_RATE = Decimal('0.0005')
MIN_ORDER_KRW = 5000

# --- 파일 및 데이터베이스 경로 ---
LOG_FILE = "trading_bot.log"
DB_FILE = "trading_bot.db"

# --- 매매 전략 파라미터 ---
STRATEGY_CONFIG = {
    "bbands_length": 20,
    "bbands_std": 2.0,
    "cci_length": 20,
    "cci_overbought": 100,
    "cci_oversold": -100,
    "short_rsi_length": 14,
    "SUPERTREND_TIMEFRAME": "60m",
    "SUPERTREND_PERIOD": 10,
    "SUPERTREND_MULTIPLIER": 2.0
}

# --- 리스크 관리 설정 ---
RISK_CONFIG = {
    "ENABLE_DAILY_LOSS_LIMIT": True,
    "DAILY_LOSS_LIMIT_PERCENTAGE": Decimal('-0.05'),
}