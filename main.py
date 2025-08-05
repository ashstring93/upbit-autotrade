import time
import pyupbit
import pandas as pd
from decimal import Decimal, getcontext, ROUND_DOWN
import sys

# --- 모듈 임포트 ---
from apscheduler.schedulers.blocking import BlockingScheduler
import config
from logger_config import logger
import database_manager as db
from trading_bot import TradingBot
import ai_interface

# Decimal 정밀도 설정
getcontext().prec = 30

# --- 주문 및 결과 처리 유틸리티 함수 ---
def wait_for_order_completion(upbit, uuid, timeout=120):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            order = upbit.get_order(uuid)
            if order and order['state'] == 'done':
                trades = order.get('trades', [])
                total_cost = sum(Decimal(trade['price']) * Decimal(trade['volume']) for trade in trades)
                total_volume = Decimal(order['executed_volume'])
                if total_volume > 0:
                    avg_price = total_cost / total_volume
                    return {'avg_price': avg_price, 'volume': total_volume}
                return None
        except Exception as e:
            logger.error(f"주문({uuid}) 정보 조회 중 오류: {e}")
        time.sleep(2)
    logger.warning(f"주문({uuid}) 체결 대기 시간 초과.")
    try:
        upbit.cancel_order(uuid)
        logger.info(f"주문({uuid})을 취소했습니다.")
    except Exception as e:
        logger.error(f"주문({uuid}) 취소 실패: {e}")
    return None

def process_buy_order(bot, order_details):
    state = bot.state
    avg_price = order_details['avg_price']
    volume = order_details['volume']
    
    current_value = state['avg_entry_price'] * state['total_position_size']
    new_value = avg_price * volume
    
    state['total_position_size'] += volume
    if state['total_position_size'] > 0:
        state['avg_entry_price'] = (current_value + new_value) / state['total_position_size']

def process_sell_order(bot, order_details, exit_reason, order_type):
    state = bot.state
    exit_price = order_details['avg_price']
    volume = order_details['volume']
    
    entry_cost = state['avg_entry_price'] * volume
    exit_value = exit_price * volume
    fee = (entry_cost + exit_value) * config.FEE_RATE
    pnl = (exit_value - entry_cost) - fee
    pnl_percentage = (exit_price / state['avg_entry_price'] - 1) * 100 if state['avg_entry_price'] > 0 else Decimal('0')
    
    trade_log = { 'ticker': bot.ticker, 'entry_time': state.get('entry_date'), 'exit_time': pd.Timestamp.now(tz="Asia/Seoul").strftime('%Y-%m-%d %H:%M:%S'), 'pnl': pnl, 'pnl_percentage': pnl_percentage, 'exit_reason': exit_reason, 'entry_ai_reason': ", ".join(state.get('entry_ai_reasons', [])), 'avg_entry_price': state['avg_entry_price'], 'exit_price': exit_price, 'quantity': volume, 'total_fee': fee }
    db.log_trade(trade_log)
    
    state['capital'] += pnl
    state['today_pnl'] += pnl
    state['total_position_size'] -= volume
    
    precision = config.TICKER_CONFIG.get(bot.ticker, Decimal('0.00000001'))
    if state['total_position_size'] < precision:
        reset_bot_state(bot)
    elif order_type == 'SELL_PARTIAL':
        state['position_status'] = 'PARTIAL_EXIT'
        state['trailing_stop_active'] = True
        logger.info(f"[{bot.ticker}] 부분 익절. PARTIAL_EXIT 상태로 전환하고 Trailing Stop을 활성화합니다.")
    
    return pnl

def reset_bot_state(bot):
    logger.info(f"--- [{bot.ticker}] 포지션 완전 종료. 상태 초기화 ---")
    bot.state.update({ "position_status": "NONE",
                       "avg_entry_price": Decimal('0'),
                       "total_position_size": Decimal('0'),
                       "trailing_stop_active": False,
                       "supertrend_stop_price": Decimal('0'),
                       "entry_date": None,
                       "trade_capital": Decimal('0'),
                       "entry_ai_reasons": [],
                       "pending_order_uuid": None,
                       "pending_order_type": None,
                       "is_take_profit_ready": False
                    })
    bot.current_task = 'WAITING_FOR_CONDITION1'
    bot.last_briefing_data = None
    bot.hold_reasons.clear()
    if config.RISK_CONFIG["ENABLE_DAILY_LOSS_LIMIT"]:
        loss_limit = bot.state['capital'] * config.RISK_CONFIG["DAILY_LOSS_LIMIT_PERCENTAGE"]
        if bot.state['today_pnl'] < 0 and bot.state['today_pnl'] <= loss_limit:
            bot.state['trading_enabled'] = False
            logger.critical(f"🚨 [{bot.ticker}] 일일 손실 한도 초과! 오늘 거래를 중단합니다.")

def initialize_bots(upbit):
    """DB에서 상태를 로드하거나, 초기 자본을 할당하여 봇 인스턴스를 생성합니다."""
    db.create_tables()
    all_states = db.load_all_states()
    tickers = list(config.TICKER_ALLOCATION.keys())
    
    new_tickers = [t for t in tickers if t not in all_states]
    if new_tickers:
        logger.info(f"새로운 코인 발견: {new_tickers}. 초기 자본을 할당합니다.")
        try:
            total_krw = Decimal(str(upbit.get_balance("KRW")))
            existing_capital = sum(s.get('capital', Decimal('0')) for s in all_states.values())
            available_krw = total_krw - existing_capital
            
            new_ratio_sum = sum(config.TICKER_ALLOCATION[t] for t in new_tickers)
            if new_ratio_sum > 0 and available_krw > config.MIN_ORDER_KRW * len(new_tickers):
                for ticker in new_tickers:
                    ratio = config.TICKER_ALLOCATION[ticker]
                    allocated_capital = (available_krw * ratio) / new_ratio_sum
                    if allocated_capital > 0:
                        all_states[ticker] = {"capital": allocated_capital}
                        logger.info(f" -> [{ticker}]: {allocated_capital:,.0f}원 할당")
            else:
                logger.warning("새로운 코인에 할당할 가용 자본이 없거나 할당 비율이 0입니다.")
        except Exception as e:
            logger.error(f"초기 자본 할당 실패: {e}")
            sys.exit()

    bot_instances = [TradingBot(t, all_states.get(t)) for t in tickers]
    for bot in bot_instances:
        db.update_state(bot.ticker, bot.state)
    return bot_instances

def check_pending_order(upbit, bot):
    uuid = bot.state.get('pending_order_uuid')
    order_type = bot.state.get('pending_order_type')
    
    if not uuid or not order_type:
        logger.warning(f"[{bot.ticker}] PENDING 상태이나 UUID/Type 정보가 없습니다. 상태를 NONE으로 강제 복구합니다.")
        reset_bot_state(bot)
        db.update_state(bot.ticker, bot.state)
        return

    try:
        order_info = upbit.get_order(uuid)
        if not order_info:
            logger.warning(f"[{bot.ticker}] 주문({uuid}) 정보를 가져올 수 없습니다. 다음 주기에 재시도.")
            return

        # 시나리오 1: 주문 성공
        if order_info['state'] == 'done':
            logger.info(f"[{bot.ticker}] 보류 주문({uuid}, {order_type}) 체결을 확인했습니다.")
            details = wait_for_order_completion(upbit, uuid, timeout=10) # 데이터 확보를 위해 짧게 재확인
            if details:
                if 'BUY' in order_type:
                    process_buy_order(bot, details)
                    bot.state['position_status'] = 'VANGUARD_IN' if order_type == 'BUY_VANGUARD' else 'FULL_POSITION'
                elif 'SELL' in order_type:
                    pnl = process_sell_order(bot, details, order_type)
                    logger.info(f" -> [{bot.ticker}] 매도 체결 완료! 실현 손익: {pnl:,.0f}원")
                bot.state['pending_order_uuid'] = None
                bot.state['pending_order_type'] = None
            else: # 체결은 됐는데 정보 가져오기 실패
                logger.error(f"[{bot.ticker}] 주문({uuid})은 체결되었으나 상세 정보 조회에 실패했습니다. 수동 확인 필요.")
                bot.state['trading_enabled'] = False # 안전을 위해 해당 코인 거래 중지

        # 시나리오 3: 주문 실패/취소
        elif order_info['state'] in ['cancel', 'reject']:
            logger.warning(f"[{bot.ticker}] 보류 주문({uuid}, {order_type})이 '{order_info['state']}' 상태입니다. 주문 이전으로 상태를 복구합니다.")
            # 주문 제출 시 미리 차감했던 자본 복구
            if 'BUY' in order_type and bot.state.get('pending_order_amount'):
                bot.state['capital'] += bot.state['pending_order_amount']
            
            # 이전 포지션 상태로 복구
            if order_type == 'BUY_VANGUARD': bot.state['position_status'] = 'NONE'
            elif order_type == 'BUY_MAIN_FORCE': bot.state['position_status'] = 'VANGUARD_IN'
            else: bot.state['position_status'] = 'FULL_POSITION'
            
            bot.state['pending_order_uuid'] = None
            bot.state['pending_order_type'] = None
            bot.state['pending_order_amount'] = None
        
        # 시나리오 2: 주문 대기
        else: # wait, watch
            logger.info(f"[{bot.ticker}] 주문({uuid}, {order_type}) 체결을 계속 대기합니다.")
            # 타임아웃 로직 추가 가능 (예: 제출 후 2주기(30분) 이상 대기 시 강제 취소)

        db.update_state(bot.ticker, bot.state)

    except Exception as e:
        logger.error(f"[{bot.ticker}] 보류 주문({uuid}) 확인 중 심각한 오류 발생: {e}")

# --- 봇의 핵심 로직 (이전 while 루프의 내용) ---
def run_trading_cycle(upbit, bots):
    """15분마다 실행될 봇의 메인 사이클"""
    try:
        now = pd.Timestamp.now(tz="Asia/Seoul")
        today_str = now.strftime('%Y-%m-%d')
        logger.info(f"--- 15분 주기 시작 ({now.strftime('%H:%M:%S')}) ---")

        # --- 1. 자정마다 일일 데이터 리셋 ---
        for bot in bots:
            if bot.state.get('today_date') != today_str:
                logger.info(f"[{bot.ticker}] 새 거래일({today_str}) 시작. 일일 데이터를 초기화합니다.")
                bot.state['today_date'] = today_str
                bot.state['today_pnl'] = Decimal('0')
                bot.state['trading_enabled'] = True
                db.update_state(bot.ticker, bot.state)

        # --- 2. 모든 코인 데이터 캐시 ---
        data_cache = {}
        for bot in bots:
            try:
                data_cache[bot.ticker] = {
                    '15m': pyupbit.get_ohlcv(bot.ticker, interval="minute15", count=50),
                    '60m': pyupbit.get_ohlcv(bot.ticker, interval="minute60", count=50),
                    '240m': pyupbit.get_ohlcv(bot.ticker, interval="minute240", count=50),
                    'price': pyupbit.get_current_price(bot.ticker)
                }
                time.sleep(config.API_CALL_DELAY_SEC)
            except Exception as e:
                logger.error(f"[{bot.ticker}] 데이터 수집 중 오류 발생: {e}")
                data_cache[bot.ticker] = None # 오류 발생 시 None으로 처리

        # --- 3. 각 봇의 전략 실행 및 주문 처리 ---
        for bot in bots:
            # --- 3-1. 보류 주문 상태 최우선 확인 ---
            if bot.state['position_status'] == 'ORDER_PENDING':
                check_pending_order(upbit, bot)
                continue
            
            # --- 3-2. 거래 중지 상태 확인 ---
            if not bot.state.get('trading_enabled', True):
                continue

            cached_data_for_ticker = data_cache.get(bot.ticker)
            if not cached_data_for_ticker: # 데이터 수집 실패 시 건너뛰기
                continue

            decision, data = bot.run_strategy(cached_data_for_ticker)
            if not decision:
                continue

            ai_decision_result = None
            order_to_execute, order_data = None, {}

            # --- 3-3. AI 판단 요청 ---
            if decision.startswith('EVALUATE'):
                ai_func_map = {
                    'EVALUATE_VANGUARD': ai_interface.get_ai_decision,
                    'EVALUATE_MAIN_FORCE': ai_interface.get_ai_main_force_decision,
                    'EVALUATE_TAKE_PROFIT': ai_interface.get_ai_take_profit_decision
                }
                ai_func = ai_func_map[decision]
                ai_output = ai_func(bot.ticker, data, bot.hold_reasons)
                
                if ai_output.get('decision') in ['Buy', 'BUY_MAIN_FORCE', 'Sell']:
                    order_to_execute = decision.replace('EVALUATE_', '')
                    if order_to_execute == 'TAKE_PROFIT': 
                        order_to_execute = 'SELL_PARTIAL'
                    order_data = ai_output
                else:
                    bot.hold_reasons.append(ai_output.get('reason'))
                    if decision == 'EVALUATE_VANGUARD':
                        logger.info(f"[{bot.ticker}] AI가 선발대 진입을 보류. 다음 15분 주기에 재평가합니다.")
                    
                    elif decision == 'EVALUATE_MAIN_FORCE':
                        bot.current_task = 'CHECKING_MAIN_FORCE_EVERY_15_MIN'
                        db.update_state(bot.ticker, bot.state)
                        logger.info(f"[{bot.ticker}] AI가 후발대 투입을 보류. CHECKING_MAIN_FORCE_EVERY_15_MIN 임무로 전환합니다.")
                    
                    else:
                        logger.info(f"[{bot.ticker}] AI가 익절을 보류. 다음 주기에 모든 조건을 다시 확인합니다.")

            # --- 3-4. 기계적 매매 신호 처리 ---
            elif decision in ['SELL_VANGUARD', 'SELL_ALL_FINAL', 'SELL_REMAINDER']:
                order_to_execute, order_data = decision, data
            
            elif decision == 'UPDATE_TRAILING_STOP_PRICE':
                bot.state['supertrend_stop_price'] = data['stop_price']
                db.update_state(bot.ticker, bot.state)
                logger.info(f" -> [{bot.ticker}] SuperTrend Stop 가격 갱신: {bot.state['supertrend_stop_price']:,.0f}원")

            # --- 3-5. 주문 실행 ---
            if order_to_execute:
                order_uuid, res = None, None
                
                # 주문 실행 전, 최종적으로 거래 가능 상태인지 다시 한번 확인 (손실 한도 우회 방지)
                if not bot.state.get('trading_enabled', True):
                    logger.warning(f"[{bot.ticker}] 주문 실행 직전, 거래 중지 상태가 확인되어 주문을 취소합니다.")
                    continue
                
                try:
                    # 매수 주문
                    if order_to_execute.startswith('BUY'):
                        amount = Decimal('0')
                        if order_to_execute == 'BUY_VANGUARD':
                            percentage = Decimal(str(order_data.get('percentage', 0)))
                            amount = bot.state['capital'] * percentage
                        elif order_to_execute == 'BUY_MAIN_FORCE':
                            vanguard_value = bot.state['avg_entry_price'] * bot.state['total_position_size']
                            remaining_capital = bot.state['trade_capital'] - vanguard_value
                            percentage = Decimal(str(order_data.get('percentage', 0)))
                            amount = remaining_capital * percentage
                        
                        if amount >= config.MIN_ORDER_KRW:
                            logger.info(f"[{bot.ticker}] {order_to_execute} 신호에 따라 매수 주문 제출 (예상 금액: {amount:,.0f}원)")
                            res = upbit.buy_market_order(bot.ticker, float(amount))
                            if res and 'uuid' in res:
                                order_uuid = res['uuid']
                                # [버그 수정] 자본 즉시 차감
                                bot.state['capital'] -= amount
                                bot.state['pending_order_amount'] = amount # 복구용 금액 저장
                                if order_to_execute == 'BUY_VANGUARD':
                                    bot.state['trade_capital'] = bot.state['capital'] + amount
                                    bot.state['entry_ai_reasons'] = [f"Vanguard: {order_data.get('reason')}"]
                                    bot.state['entry_date'] = now.strftime('%Y-%m-%d %H:%M:%S')
                                else: # BUY_MAIN_FORCE
                                    bot.state['entry_ai_reasons'].append(f"Main Force: {order_data.get('reason')}")

                    # 매도 주문
                    elif order_to_execute.startswith('SELL'):
                        amount_to_sell = bot.state['total_position_size']
                        if order_to_execute == 'SELL_PARTIAL':
                            percentage = Decimal(str(order_data.get('percentage', 0)))
                            amount_to_sell *= percentage
                        
                        precision = config.TICKER_CONFIG.get(bot.ticker, Decimal('0.00000001'))

                        final_amount_to_sell = amount_to_sell.quantize(precision, rounding=ROUND_DOWN)
                        if final_amount_to_sell > 0:
                            logger.info(f"[{bot.ticker}] {order_to_execute} 신호에 따라 매도 주문 제출 (예상 수량: {float(final_amount_to_sell)})")
                            res = upbit.sell_market_order(bot.ticker, float(final_amount_to_sell))
                            if res and 'uuid' in res:
                                order_uuid = res['uuid']
                
                except Exception as e:
                    logger.error(f"[{bot.ticker}] 주문 제출 중 오류 발생: {e}. API 응답: {res}")
                    # 주문 제출 실패 시, 미리 차감했던 자본 복구
                    if 'BUY' in order_to_execute and bot.state.get('pending_order_amount'):
                        bot.state['capital'] += bot.state['pending_order_amount']
                        bot.state['pending_order_amount'] = None
                        db.update_state(bot.ticker, bot.state)

                # --- 3-6. 주문 제출 후 상태 변경 ---
                if order_uuid:
                    bot.state['position_status'] = 'ORDER_PENDING'
                    bot.state['pending_order_uuid'] = order_uuid
                    bot.state['pending_order_type'] = order_to_execute
                    db.update_state(bot.ticker, bot.state)
                    logger.info(f"[{bot.ticker}] 주문 제출 성공. UUID: {order_uuid}. PENDING 상태로 전환합니다.")

        # --- 4. 총자산 기록 ---
        try:
            all_bot_states = db.load_all_states().values()
            total_realized_capital = sum(s.get('capital', Decimal('0')) for s in all_bot_states)
            unrealized_pnl = Decimal('0')
            for state in all_bot_states:
                if state.get('position_status') != 'NONE' and state.get('total_position_size', Decimal('0')) > 0:
                    current_price = data_cache.get(state['ticker'], {}).get('price')
                    if current_price:
                        market_value = Decimal(str(current_price)) * state['total_position_size']
                        cost = state['avg_entry_price'] * state['total_position_size']
                        unrealized_pnl += (market_value - cost)
            total_equity = total_realized_capital + unrealized_pnl
            db.log_capital(now.strftime('%Y-%m-%d %H:%M:%S'), total_equity)
            logger.info(f"총자산 기록 완료: {total_equity:,.0f}원")
        except Exception as e:
            logger.error(f"총자산 기록 중 오류 발생: {e}")

    except (KeyboardInterrupt, SystemExit):
        logger.info("종료 신호(Ctrl+C)가 감지되어 거래 주기를 중단하고 프로그램을 종료합니다.")
        raise  # 스케줄러의 메인 except 블록으로 예외를 다시 던져서 정상 종료시킴
    except Exception as e:
        logger.error(f"거래 주기 실행 중 심각한 오류 발생: {e}")

def main():
    logger.info("✅ 자동매매 봇 프로그램이 시작되었습니다.")
    try:
        upbit = pyupbit.Upbit(config.ACCESS_KEY, config.SECRET_KEY)
        logger.info(f"업비트 연결 성공! 현재 보유 KRW: {upbit.get_balance('KRW'):,.0f}원")
    except Exception as e:
        logger.error(f"업비트 연결 실패: {e}")
        return

    bots = initialize_bots(upbit)
    
    scheduler = BlockingScheduler(timezone='Asia/Seoul')
    scheduler.add_job(run_trading_cycle, 'cron', minute='*/15', args=[upbit, bots])
    
    logger.info("스케줄러가 시작되었습니다. 15분 주기로 작업을 실행합니다.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러가 종료되었습니다.")

if __name__ == "__main__":
    main()