import sqlite3
import pandas as pd
from decimal import Decimal
import json
from logger_config import logger

DB_FILE = "trading_bot.db"

def connect_db():
    """데이터베이스에 연결하고 커서를 반환합니다."""
    # isolation_level=None으로 설정하여 auto-commit 모드로 작동
    return sqlite3.connect(DB_FILE, isolation_level=None)

def create_tables():
    conn = connect_db()
    cursor = conn.cursor()
    # TradingBot의 모든 상태를 저장하도록 컬럼 추가
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bot_states (
        ticker TEXT PRIMARY KEY,
        capital TEXT NOT NULL,
        position_status TEXT NOT NULL,
        avg_entry_price TEXT DEFAULT '0',
        total_position_size TEXT DEFAULT '0',
        trailing_stop_active BOOLEAN DEFAULT FALSE,
        supertrend_stop_price TEXT DEFAULT '0',
        today_date TEXT,
        today_pnl TEXT DEFAULT '0',
        trading_enabled BOOLEAN DEFAULT TRUE,
        entry_date TEXT,
        trade_capital TEXT DEFAULT '0',
        last_briefing TEXT,
        entry_ai_reasons TEXT,
        pending_order_uuid TEXT,
        pending_order_type TEXT,
        is_take_profit_ready BOOLEAN DEFAULT FALSE -- [신규] 1차 익절 준비 상태 플래그
    )
    """)
    
    # 완료된 거래 내역을 기록 (realtime_trade_log.csv 대체)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trade_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        entry_time TEXT,
        exit_time TEXT NOT NULL,
        pnl TEXT NOT NULL,
        pnl_percentage TEXT NOT NULL,
        exit_reason TEXT,
        entry_ai_reason TEXT,
        avg_entry_price TEXT,
        exit_price TEXT,
        quantity TEXT,
        total_fee TEXT
    )
    """)
    
    # 자산 현황을 기록 (capital_log.csv 대체)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS capital_log (
        timestamp TEXT PRIMARY KEY,
        total_equity REAL NOT NULL
    )
    """)
    
    logger.info("데이터베이스 테이블 준비 완료.")
    conn.close()

def load_all_states():
    """데이터베이스에서 모든 코인의 상태를 불러옵니다."""
    conn = connect_db()
    # 딕셔너리 형태로 결과를 받기 위해 row_factory 설정
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT * FROM bot_states")
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        # 테이블이 아직 없는 경우 빈 리스트 반환
        logger.warning("bot_states 테이블이 존재하지 않아 새로 생성됩니다.")
        create_tables()
        rows = []

    states = {}
    for row in rows:
        state = dict(row)
        # DB에서 불러온 텍스트 값을 적절한 타입으로 변환
        for key in ['capital', 'avg_entry_price', 'total_position_size', 'supertrend_stop_price', 'today_pnl', 'trade_capital']:
            if key in state and state[key] is not None:
                state[key] = Decimal(state[key])
        
        # AI 진입 이유는 '||' 구분자로 분리하여 리스트로 복원
        if 'entry_ai_reasons' in state and state['entry_ai_reasons']:
            state['entry_ai_reasons'] = state['entry_ai_reasons'].split('||')
        else:
            state['entry_ai_reasons'] = []
            
        states[state['ticker']] = state
        
    conn.close()
    return states

def update_state(ticker, state_dict):
    """특정 코인의 상태를 데이터베이스에 업데이트(또는 삽입)합니다."""
    conn = connect_db()
    cursor = conn.cursor()
    
    # DB에 저장하기 위해 타입들을 텍스트로 변환
    values_to_save = state_dict.copy()
    for k, v in values_to_save.items():
        if isinstance(v, Decimal):
            values_to_save[k] = str(v)
        elif isinstance(v, list):
            # AI 진입 이유는 '||' 구분자를 사용하여 하나의 문자열로 결합
            values_to_save[k] = '||'.join(v)
        elif isinstance(v, dict) or isinstance(v, pd.DataFrame):
            # 딕셔너리나 데이터프레임은 JSON 문자열로 변환
            values_to_save[k] = json.dumps(v)

    columns = ', '.join(values_to_save.keys())
    placeholders = ', '.join(['?'] * len(values_to_save))
    # ON CONFLICT ... DO UPDATE 구문을 사용하여 Upsert(Update or Insert) 처리
    update_clause = ', '.join([f"{key} = excluded.{key}" for key in values_to_save if key != 'ticker'])
    
    query = f"INSERT INTO bot_states ({columns}) VALUES ({placeholders}) ON CONFLICT(ticker) DO UPDATE SET {update_clause}"
    
    cursor.execute(query, list(values_to_save.values()))
    conn.close()

def log_trade(trade_data):
    """완료된 거래를 trade_log 테이블에 기록합니다."""
    conn = connect_db()
    cursor = conn.cursor()
    
    values = {k: str(v) if isinstance(v, Decimal) else v for k, v in trade_data.items()}
    columns = ', '.join(values.keys())
    placeholders = ', '.join(['?'] * len(values))
    
    cursor.execute(f"INSERT INTO trade_log ({columns}) VALUES ({placeholders})", list(values.values()))
    conn.close()
    logger.info(f"[{trade_data['ticker']}] 거래가 데이터베이스에 기록되었습니다.")

def log_capital(timestamp, total_equity):
    """자산 현황을 capital_log 테이블에 기록합니다."""
    conn = connect_db()
    cursor = conn.cursor()
    # INSERT OR REPLACE 구문을 사용하여 동일한 timestamp의 데이터는 덮어쓰기
    cursor.execute("INSERT OR REPLACE INTO capital_log (timestamp, total_equity) VALUES (?, ?)", (timestamp, float(total_equity)))
    conn.close()