import pandas as pd
import json
import os
import time
import logging
from logging.handlers import RotatingFileHandler
import portalocker
import config
from decimal import Decimal, getcontext
import requests
import pandas_ta as ta
from decimal import Decimal, InvalidOperation

# ... (상단 로깅 및 변환기 설정은 이전과 동일) ...
# --- 로깅 설정 ---
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('TradingBot') 
logger.setLevel(logging.INFO) 

if not logger.handlers:
    file_handler = RotatingFileHandler(config.LOG_FILE, maxBytes=10*1024*1024, backupCount=5,encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)

def log_capital(total_capital):
    """현재 총 자산을 CSV 파일에 기록합니다."""
    # 파일이 없으면 헤더와 함께 생성
    if not os.path.exists(config.CAPITAL_LOG_FILE):
        with open(config.CAPITAL_LOG_FILE, 'w', encoding='utf-8') as f:
            f.write("timestamp,capital\n")

    # 현재 시간과 자산을 파일에 추가
    now_ts = pd.Timestamp.now(tz="Asia/Seoul").strftime('%Y-%m-%d %H:%M:%S')
    with open(config.CAPITAL_LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{now_ts},{total_capital}\n")

def default_converter(o):
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")
# --- 파일 및 상태 관리 함수 ---
def save_state(state_to_save):
    try:
        with portalocker.Lock(config.STATE_FILE, 'w', encoding='utf-8', timeout=5) as f:
            json.dump(state_to_save, f, indent=4, ensure_ascii=False, default=default_converter)
    except Exception as e:
        logger.error(f"상태 저장 실패: {e}")

def load_state():
    if os.path.exists(config.STATE_FILE):
        try:
            with open(config.STATE_FILE, 'r', encoding='utf-8') as f:
                portalocker.lock(f, portalocker.LOCK_SH)
                logger.info("저장된 상태 파일을 불러옵니다.")
                loaded_data = json.load(f, parse_float=Decimal)
                return loaded_data
        except Exception as e:
            logger.error(f"상태 파일 로딩 실패: {e}")
    return None

def _to_decimal_if_numeric(s):
    """
    문자열을 정밀도 손실 없이 안전하게 Decimal로 변환합니다.
    지수 표기법 등은 float을 통해 fallback으로 처리합니다.
    """
    if not isinstance(s, str):
        return s
    try:
        # 1. 직접 변환을 시도하여 정밀도를 최대한 보존
        return Decimal(s)
    except InvalidOperation:
        try:
            # 2. 직접 변환 실패 시 (예: 지수 표기법), float을 거쳐 변환
            return Decimal(str(float(s)))
        except (ValueError, TypeError):
            # 3. float 변환도 실패하면 원본 문자열 반환
            return s

def initialize_bot_states(upbit_instance):
    loaded_states = load_state()
    tickers_to_trade = config.TICKER_ALLOCATION.keys()
    initial_states = {}
    for ticker in tickers_to_trade:
         initial_states[ticker] = {
            "capital": Decimal('0'),
            "position_status": "NONE",
            "avg_entry_price": Decimal('0'),
            "total_position_size": Decimal('0'),
            "entry_date": None,
            "trade_capital": Decimal('0'),
            "main_force_entry_date": None,
            "main_force_signal_active": False,
            "trailing_stop_active": False,
            "supertrend_stop_price": Decimal('0'),
            "pending_order_uuid": None,
            "pending_order_type": None,
            "pending_order_data": None,
            "pending_order_timestamp": None, # 보류 주문 제출 시간
            "pending_warning_sent": False,   # 경고 발송 여부
            "today_date": pd.Timestamp.now(tz="Asia/Seoul").strftime('%Y-%m-%d'),
            "today_pnl": Decimal('0'),
            "trading_enabled": True
        }

    if loaded_states:
        for ticker in initial_states:
            if ticker in loaded_states:
                # [수정] 더 명확한 Decimal 변환 로직
                for key, value in loaded_states[ticker].items():
                    loaded_states[ticker][key] = _to_decimal_if_numeric(value)
                initial_states[ticker].update(loaded_states[ticker])
        logger.info("기존 상태에 최신 코인 목록을 반영하여 상태를 구성했습니다.")
        return initial_states
    else:
        try:
            if sum(config.TICKER_ALLOCATION.values()) != Decimal('1'):
                logger.error("config.py의 TICKER_ALLOCATION 비율 합계가 1이 아닙니다. 프로그램을 종료합니다.")
                return None
            total_krw_balance = Decimal(str(upbit_instance.get_balance("KRW")))
            logger.info(f"총 보유 KRW: {total_krw_balance:,.0f}원. 설정된 비율에 따라 자본을 할당합니다.")
            
            for ticker, ratio in config.TICKER_ALLOCATION.items():
                allocated_capital = total_krw_balance * ratio
                initial_states[ticker]['capital'] = allocated_capital
                logger.info(f"  -> [{ticker}]: {ratio:.0%} 할당 -> {allocated_capital:,.0f}원")

            return initial_states
        except Exception as e:
            logger.error(f"업비트 잔고 조회 실패: {e}")
            return None

def reset_state(bot_states, bot_current_task, ticker, pnl=Decimal('0')):
    state = bot_states[ticker]
    state['capital'] += pnl
    
    # [수정] 일일 손실 누적 및 한도 확인
    state['today_pnl'] += pnl
    if config.RISK_CONFIG["ENABLE_DAILY_LOSS_LIMIT"]:
        loss_limit_percentage = config.RISK_CONFIG["DAILY_LOSS_LIMIT_PERCENTAGE"]
        # 자본금 대비 손실 한도 계산
        loss_limit_amount = state['capital'] * loss_limit_percentage
        if state['today_pnl'] < 0 and state['today_pnl'] <= loss_limit_amount:
            state['trading_enabled'] = False
            msg = f"🚨 [{ticker}] 일일 손실 한도 초과! 오늘({state['today_date']}) 이 코인에 대한 추가 거래를 중단합니다. (누적손실: {state['today_pnl']:,.0f}원)"
            logger.critical(msg)
            send_telegram_notification(msg)

    state['position_status'] = 'NONE'
    state['avg_entry_price'] = Decimal('0')
    state['total_position_size'] = Decimal('0')
    state['entry_date'] = None
    state['trade_capital'] = Decimal('0')
    state['main_force_entry_date'] = None
    state['main_force_signal_active'] = False # [추가]
    state['trailing_stop_active'] = False
    state['supertrend_stop_price'] = Decimal('0')
    state['pending_order_uuid'] = None
    state['pending_order_type'] = None
    state['pending_order_data'] = None
    bot_states[ticker]['pending_order_timestamp'] = None # [추가]
    bot_states[ticker]['pending_warning_sent'] = False   # [추가]
    
    
    bot_current_task[ticker] = 'WAITING_FOR_4H_SIGNAL' 
    logger.info(f"--- [{ticker}] 상태 초기화 및 대기 모드 전환 완료. 현재 추정 자본: {state['capital']:,.0f}원 ---")

# --- 거래 기록 함수 ---
def log_trade_to_csv(trade_info):
    try:
        df = pd.DataFrame([trade_info]).applymap(
            lambda x: float(x) if isinstance(x, Decimal) else x
        )
        if not os.path.exists(config.TRADE_LOG_FILE):
            df.to_csv(config.TRADE_LOG_FILE, index=False, mode='w', encoding='utf-8-sig')
        else:
            df.to_csv(config.TRADE_LOG_FILE, index=False, mode='a', header=False, encoding='utf-8-sig')
    except Exception as e:
        logger.error(f"매매일지 기록 실패: {e}")

def calculate_pnl_and_create_log(ticker, state, order_details, exit_reason, ai_reason="N/A"):
    actual_exit_price = order_details['avg_price']
    executed_volume = order_details['volume']
    
    entry_cost = state['avg_entry_price'] * executed_volume
    exit_value = actual_exit_price * executed_volume
    entry_fee = entry_cost * config.FEE_RATE
    exit_fee = exit_value * config.FEE_RATE
    total_fee = entry_fee + exit_fee
    pnl = (exit_value - entry_cost) - total_fee

    # [수정] 수익률 계산을 Decimal로 통일
    pnl_percentage = (actual_exit_price / state.get('avg_entry_price', Decimal('1')) - Decimal('1')) * Decimal('100') if state.get('avg_entry_price') > 0 else Decimal('0')

    trade = {
        "진입 시점": state.get('entry_date'), 
        "후발대 진입 시점": state.get('main_force_entry_date'),
        "청산 시점": pd.Timestamp.now(tz="Asia/Seoul").strftime('%Y-%m-%d %H:%M:%S'), 
        "티커": ticker,
        "청산 사유": exit_reason,
        "AI 판단 이유": ai_reason,
        "평균 진입 가격": state.get('avg_entry_price'), 
        "청산 가격": actual_exit_price,
        "청산 수량": executed_volume,
        "수익/손실": pnl, 
        "수수료": total_fee,
        "수익률(%)": pnl_percentage
    }
    return pnl, trade
# ... (wait_for_order_completion 함수는 이전과 동일) ...
def wait_for_order_completion(upbit_instance, order_uuid, timeout_sec=60):
    start_time = time.time()
    while time.time() - start_time < timeout_sec:
        try:
            order_info = upbit_instance.get_order(order_uuid)
            if order_info and order_info['state'] == 'done':
                total_cost = sum(Decimal(trade['price']) * Decimal(trade['volume']) for trade in order_info['trades'])
                executed_volume = Decimal(order_info['executed_volume'])
                avg_price = total_cost / executed_volume if executed_volume > 0 else Decimal('0')
                if avg_price > 0:
                    return {"avg_price": avg_price, "volume": executed_volume}
        except Exception as e:
            logger.error(f"주문 정보 조회 중 오류: {e}")
        time.sleep(1.5)
    logger.warning(f"주문 체결 대기 시간({timeout_sec}초) 초과.")
    return None
# ... (update_state_after_buy 함수는 이전과 동일) ...
def update_state_after_buy(bot_states, ticker, order_details):
    state = bot_states[ticker]
    avg_price = order_details['avg_price']
    executed_volume = order_details['volume']
    current_value = state['total_position_size'] * state['avg_entry_price']
    new_value = executed_volume * avg_price
    state['total_position_size'] += executed_volume
    if state['total_position_size'] > 0:
        state['avg_entry_price'] = (current_value + new_value) / state['total_position_size']
    logger.info(f" -> [{ticker}] 매수 체결 완료! 평단가: {state['avg_entry_price']:,.0f}원, 총 보유수량: {state['total_position_size']}")
# ... (send_telegram_notification 함수는 이전과 동일) ...
def send_telegram_notification(message):
    if config.TELEGRAM_CONFIG["ENABLE"]:
        token = config.TELEGRAM_CONFIG["TOKEN"]
        chat_id = config.TELEGRAM_CONFIG["CHAT_ID"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            response = requests.post(url, json={'chat_id': chat_id, 'text': message})
            response.raise_for_status()
        except Exception as e:
            logger.error(f"텔레그램 알림 전송 실패: {e}")

# [수정] process_completed_order 함수 수정
def process_completed_order(ticker, state, bot_current_task, bot_states, order_details, cached_data):
    """
    체결 완료된 주문을 후처리하는 중앙 함수. (SuperTrend 초기화 로직 추가)
    """
    order_type = state.get('pending_order_type')
    logger.info(f"[{ticker}] 주문 체결 확인 완료. 후처리를 시작합니다. (유형: {order_type})")
    
    if order_type == 'BUY_VANGUARD':
        update_state_after_buy(bot_states, ticker, order_details)
        state['position_status'] = 'VANGUARD_IN'
        state['entry_date'] = pd.Timestamp.now(tz="Asia/Seoul").strftime('%Y-%m-%d %H:%M:%S')
        msg = f"📈 [{ticker}] 선발대 매수 체결 완료!\n- 평단가: {state['avg_entry_price']:,.0f}원"
        send_telegram_notification(msg)
        bot_current_task[ticker] = 'WAITING_FOR_4H_SIGNAL'

    elif order_type == 'BUY_MAIN_FORCE':
        update_state_after_buy(bot_states, ticker, order_details)
        state['position_status'] = 'FULL_POSITION'
        state['main_force_entry_date'] = pd.Timestamp.now(tz="Asia/Seoul").strftime('%Y-%m-%d %H:%M:%S')
        msg = f"➕ [{ticker}] 후발대 추가 매수 체결 완료!\n- 평단가: {state['avg_entry_price']:,.0f}원"
        send_telegram_notification(msg)
    
    elif order_type == 'SELL_PARTIAL':
        reason_data = state.get('pending_order_data', {})
        pnl, trade_log = calculate_pnl_and_create_log(ticker, state, order_details, f"AI 결정 부분 익절({reason_data.get('percentage', 0)*100:.0f}%)", reason_data.get('reason'))
        log_trade_to_csv(trade_log)
        
        # [수정] 부분 익절 후 자본금 및 일일 손익 업데이트
        state['capital'] += pnl
        state['today_pnl'] += pnl
        
        precision = config.TICKER_CONFIG.get(ticker, Decimal('0.00000001'))
        executed_volume = order_details['volume'].quantize(precision)
        state['total_position_size'] -= executed_volume
        state['trailing_stop_active'] = True
        
        # [수정] SuperTrend Stop 가격을 즉시 계산하여 초기화
        ts_conf = config.STRATEGY_CONFIG
        df_ts = cached_data.get(ts_conf['SUPERTREND_TIMEFRAME'])
        if df_ts is not None and len(df_ts) >= ts_conf['SUPERTREND_PERIOD']:
            period = ts_conf['SUPERTREND_PERIOD']
            multiplier = ts_conf['SUPERTREND_MULTIPLIER']
            supertrend_col = get_supertrend_col_name(period, multiplier)
            if supertrend_col not in df_ts.columns:
                df_ts.ta.supertrend(length=period, multiplier=multiplier, append=True)
            supertrend_col = f"SUPERT_{period}_{multiplier:.1f}"
            initial_stop_price_raw = df_ts[supertrend_col].iloc[-2]
            if pd.isna(initial_stop_price_raw):
                state['supertrend_stop_price'] = Decimal('0')
                msg = f"💰 [{ticker}] 부분 익절 체결 완료!\n- 실현 손익: {pnl:,.0f}원\n- SuperTrend 시작 (데이터 부족으로 다음 주기에 Stop 설정)"
            else:
                state['supertrend_stop_price'] = Decimal(str(initial_stop_price_raw))
                msg = f"💰 [{ticker}] 부분 익절 체결 완료!\n- 실현 손익: {pnl:,.0f}원\n- SuperTrend 시작 (초기 Stop: {state['supertrend_stop_price']:,.0f}원)"
        
        send_telegram_notification(msg)

    elif order_type in ['SELL_VANGUARD', 'SELL_REMAINDER', 'SELL_ALL_FINAL']:
        reason_data = state.get('pending_order_data', {})
        reason_text_map = {
            'SELL_VANGUARD': f"선발대 손절({reason_data.get('reason')})",
            'SELL_REMAINDER': f"잔량 익절(SuperTrend)({reason_data.get('reason')})",
            'SELL_ALL_FINAL': f"포지션 종료({reason_data.get('reason')})"
        }
        pnl, trade_log = calculate_pnl_and_create_log(ticker, state, order_details, reason_text_map.get(order_type, "N/A"), "N/A")
        log_trade_to_csv(trade_log)
        reset_state(bot_states, bot_current_task, ticker, pnl) # 리셋 함수가 손실 한도 체크까지 수행
        msg = f"🔔 [{ticker}] 포지션 종료 체결 완료!\n- 최종 손익: {pnl:,.0f}원\n- 사유: {reason_data.get('reason')}"
        send_telegram_notification(msg)

    state['pending_order_uuid'] = None
    state['pending_order_type'] = None
    state['pending_order_data'] = None
    save_state(bot_states)

def get_supertrend_col_name(period, multiplier):
    """SuperTrend 표준 컬럼명을 생성합니다."""
    return f"SUPERT_{period}_{multiplier:.1f}"