from dotenv import load_dotenv
load_dotenv()

import pyupbit
import pandas as pd
import time
import config
import bot_utils
from strategies import situation_a, situation_b, situation_c, ai_interface
import sys
from decimal import Decimal

if __name__ == "__main__":
    bot_utils.logger.info("✅ 자동매매 봇이 시작되었습니다.")
    bot_utils.send_telegram_notification("✅ 자동매매 봇이 시작되었습니다.")
    
    # ... (API 키 유효성 검사 및 봇 초기 설정은 이전과 동일) ...
    if config.ACCESS_KEY == "YOUR_ACCESS_KEY" or config.SECRET_KEY == "YOUR_SECRET_KEY":
        bot_utils.logger.error("업비트 API 키가 설정되지 않았습니다. config.py 파일을 확인해주세요.")
        sys.exit()

    try:
        upbit = pyupbit.Upbit(config.ACCESS_KEY, config.SECRET_KEY)
        bot_states = bot_utils.initialize_bot_states(upbit)
        if bot_states is None:
            bot_utils.logger.error("봇 상태 초기화 실패. 프로그램을 종료합니다.")
            sys.exit()
        bot_utils.save_state(bot_states)
        bot_utils.logger.info(f"업비트 연결 성공! 현재 보유 KRW: {upbit.get_balance('KRW'):,.0f}원")
    except Exception as e:
        bot_utils.logger.error(f"업비트 연결 또는 초기화 실패: {e}")
        sys.exit()
    
    tickers_to_trade = list(config.TICKER_ALLOCATION.keys())
    bot_current_task = {ticker: 'WAITING_FOR_4H_SIGNAL' for ticker in tickers_to_trade}
    a_hold_reasons = {ticker: [] for ticker in tickers_to_trade}
    b_hold_reasons = {ticker: [] for ticker in tickers_to_trade}
    c_hold_reasons = {ticker: [] for ticker in tickers_to_trade}

    while True:
        try:
            today_str = pd.Timestamp.now(tz="Asia/Seoul").strftime('%Y-%m-%d')
            for ticker in tickers_to_trade:
                if bot_states[ticker].get('today_date') != today_str:
                    bot_utils.logger.info(f"[{ticker}] 새 거래일({today_str}) 시작. 일일 손실 한도를 초기화합니다.")
                    bot_states[ticker]['today_date'] = today_str
                    bot_states[ticker]['today_pnl'] = Decimal('0')
                    bot_states[ticker]['trading_enabled'] = True
                    bot_utils.save_state(bot_states)

            data_cache = {}
            for ticker in tickers_to_trade:
                data_cache[ticker] = {
                    '10m': pyupbit.get_ohlcv(ticker, interval="minute10", count=50),
                    '60m': pyupbit.get_ohlcv(ticker, interval="minute60", count=50),
                    '240m': pyupbit.get_ohlcv(ticker, interval="minute240", count=50),
                    'price': pyupbit.get_current_price(ticker)
                }
                time.sleep(0.3)

            now = pd.Timestamp.now(tz="Asia/Seoul")
            bot_utils.logger.info(f"--- 주기 시작 ({now.strftime('%H:%M:%S')}) ---")

            for ticker in tickers_to_trade:
                state = bot_states[ticker]
                cached_data_for_ticker = data_cache.get(ticker, {})

                # [수정] 일일 손실 한도 초과 시 거래 중단
                if not state.get('trading_enabled', True):
                    bot_utils.logger.warning(f"[{ticker}] 일일 손실 한도 초과로 오늘 거래가 중단되었습니다.")
                    continue

                if state['position_status'] == 'ORDER_PENDING':
                    uuid = state.get('pending_order_uuid')
                    if uuid:
                        try:
                            order_info = upbit.get_order(uuid)
                            if order_info and order_info.get('state') == 'done':
                                order_details = bot_utils.wait_for_order_completion(upbit, uuid, timeout_sec=10)
                                if order_details:
                                    # [수정] cached_data 전달
                                    bot_utils.process_completed_order(ticker, state, bot_current_task, bot_states, order_details, cached_data_for_ticker)
                                continue
                            elif order_info and order_info.get('state') in ['cancel', 'reject']:
                                bot_utils.logger.error(f"[{ticker}] 보류 중인 주문({uuid})이 '{order_info.get('state')}' 상태입니다. 이전 상태로 복귀합니다.")
                                order_type = state.get('pending_order_type')
                                prev_status = 'NONE'
                                if order_type in ['BUY_MAIN_FORCE', 'SELL_VANGUARD']:
                                    prev_status = 'VANGUARD_IN'
                                elif order_type in ['SELL_PARTIAL', 'SELL_REMAINDER', 'SELL_ALL_FINAL']:
                                    prev_status = 'FULL_POSITION'
                                state['position_status'] = prev_status
                                state['pending_order_uuid'] = None
                                state['pending_order_type'] = None
                                state['pending_order_data'] = None
                                bot_utils.save_state(bot_states)
                                bot_utils.send_telegram_notification(f"⚠️ [{ticker}] 주문 실패! ({order_info.get('state')}). 이전 상태({prev_status})로 복귀합니다.")
                                continue
                            else:
                                # [수정] 주문이 계속 대기 중일 때 타임아웃 경고 로직 추가
                                bot_utils.logger.info(f"[{ticker}] 주문({uuid}) 체결을 계속 대기합니다...")
                                pending_time_str = state.get('pending_order_timestamp')
                                if pending_time_str and not state.get('pending_warning_sent', False):
                                    pending_time = pd.Timestamp(pending_time_str)
                                    elapsed_seconds = (pd.Timestamp.now(tz="Asia/Seoul") - pending_time).total_seconds()
                                    
                                    # 예: 5분(300초) 이상 지연 시 경고
                                    if elapsed_seconds > 300:
                                        msg = f"⚠️ [{ticker}] 주문 지연 경고!\n- 주문({uuid})이 5분 이상 보류 상태입니다.\n- 수동 확인이 필요할 수 있습니다."
                                        bot_utils.send_telegram_notification(msg)
                                        state['pending_warning_sent'] = True
                                        bot_utils.save_state(bot_states)
                                continue # 아직 대기 중이므로 이번 주기에는 추가 행동 없이 넘어감
                        except Exception as e:
                            bot_utils.logger.error(f"[{ticker}] 보류 주문({uuid}) 확인 중 오류 발생: {e}")
                
                task = bot_current_task.get(ticker, 'WAITING_FOR_4H_SIGNAL')
                decision, data = None, None
                
                # ... (이하 모든 if/elif 로직은 이전 답변과 동일) ...
                bot_utils.logger.info(f"--- [{ticker}] 확인 (임무: {task} / 포지션: {state['position_status']}) ---")
                if state['position_status'] == 'NONE':
                    b_hold_reasons[ticker].clear(); c_hold_reasons[ticker].clear()
                    new_task, result = situation_a.check(ticker, task, cached_data_for_ticker)
                    if task != new_task:
                        bot_utils.logger.info(f"[{ticker}] 임무 변경: {task} -> {new_task}")
                        bot_current_task[ticker] = new_task
                        a_hold_reasons[ticker].clear()
                    if isinstance(result, dict):
                        ai_output = ai_interface.get_ai_decision(ticker, result, a_hold_reasons[ticker])
                        if ai_output.get('decision', '').lower() == 'buy':
                            decision, data = 'BUY_VANGUARD', ai_output
                        elif ai_output.get('decision', '').lower() == 'hold':
                            a_hold_reasons[ticker].append(ai_output.get('reason'))
                elif state['position_status'] == 'VANGUARD_IN':
                    a_hold_reasons[ticker].clear(); c_hold_reasons[ticker].clear()
                    decision, data = situation_b.check(ticker, state, cached_data_for_ticker)
                    if decision == 'ACTIVATE_MAIN_FORCE_SIGNAL':
                        state['main_force_signal_active'] = True
                        bot_utils.save_state(bot_states)
                    elif decision == 'DEACTIVATE_MAIN_FORCE_SIGNAL':
                        state['main_force_signal_active'] = False
                        bot_utils.save_state(bot_states)
                elif state['position_status'] == 'FULL_POSITION':
                    a_hold_reasons[ticker].clear(); b_hold_reasons[ticker].clear()
                    decision, data = situation_c.check(ticker, state, cached_data_for_ticker)
                if decision == 'BUY_VANGUARD':
                    raw_percentage = data.get('percentage', 0)
                    percentage = float(raw_percentage) if isinstance(raw_percentage, (int, float)) else 0.0
                    if 0.1 <= percentage <= 0.5:
                        bot_utils.logger.info(f"[{ticker}] AI 결정(매수 비중: {percentage*100:.1f}%)에 따라 선발대 매수를 시도합니다.")
                        state['trade_capital'] = state['capital']
                        amount_to_buy = state['trade_capital'] * Decimal(str(percentage))
                        if amount_to_buy >= config.MIN_ORDER_KRW:
                            result_order = upbit.buy_market_order(ticker, float(amount_to_buy))
                            if result_order and 'uuid' in result_order:
                                state['position_status'] = 'ORDER_PENDING'
                                state['pending_order_uuid'] = result_order['uuid']
                                state['pending_order_type'] = 'BUY_VANGUARD'
                                state['pending_order_timestamp'] = pd.Timestamp.now(tz="Asia/Seoul").isoformat()
                                state['pending_warning_sent'] = False # [추가]
                                bot_utils.save_state(bot_states)
                                msg = f"⏳ [{ticker}] 선발대 매수 주문 제출 완료.\n- 체결을 기다립니다."
                                bot_utils.send_telegram_notification(msg)
                                bot_utils.logger.info(f"  -> [{ticker}] {msg.splitlines()[0]}")
                            else:
                                bot_utils.logger.error(f"[{ticker}] 선발대 매수 주문 제출에 실패했습니다. API 응답: {result_order}")
                        else:
                            bot_utils.logger.warning(f"[{ticker}] 계산된 매수 금액({amount_to_buy:,.0f}원)이 최소 주문 금액 미만입니다.")
                    else:
                        bot_utils.logger.warning(f"[{ticker}] AI가 유효하지 않은 매수 비중({raw_percentage})을 반환하여 매수를 취소합니다.")
                elif decision == 'EVALUATE_MAIN_FORCE':
                    ai_output = ai_interface.get_ai_main_force_decision(ticker, data, b_hold_reasons[ticker])
                    if ai_output.get('decision') == 'BUY_MAIN_FORCE':
                        raw_percentage = ai_output.get('percentage', 0)
                        percentage = float(raw_percentage) if isinstance(raw_percentage, (int, float)) else 0.0
                        if 0.5 <= percentage <= 1.0:
                            bot_utils.logger.info(f"[{ticker}] AI 결정(추가 매수 비중: {percentage*100:.1f}%)에 따라 후발대 매수를 시도합니다.")
                            vanguard_value = state['avg_entry_price'] * state['total_position_size']
                            remaining_capital = state['trade_capital'] - vanguard_value
                            amount_to_buy = remaining_capital * Decimal(str(percentage))
                            if amount_to_buy >= config.MIN_ORDER_KRW:
                                result_order = upbit.buy_market_order(ticker, float(amount_to_buy))
                                if result_order and 'uuid' in result_order:
                                    state['position_status'] = 'ORDER_PENDING'
                                    state['pending_order_uuid'] = result_order['uuid']
                                    state['pending_order_type'] = 'BUY_MAIN_FORCE'
                                    state['pending_order_timestamp'] = pd.Timestamp.now(tz="Asia/Seoul").isoformat()
                                    state['pending_warning_sent'] = False # [추가]
                                    bot_utils.save_state(bot_states)
                                    msg = f"⏳ [{ticker}] 후발대 매수 주문 제출 완료.\n- 체결을 기다립니다."
                                    bot_utils.send_telegram_notification(msg)
                                    bot_utils.logger.info(f"  -> [{ticker}] {msg.splitlines()[0]}")
                                else:
                                    bot_utils.logger.error(f"[{ticker}] 후발대 매수 주문 제출에 실패했습니다. API 응답: {result_order}")
                            else:
                                bot_utils.logger.warning(f"[{ticker}] 계산된 추가 매수 금액({amount_to_buy:,.0f}원)이 최소 주문 금액 미만입니다.")
                        else:
                            bot_utils.logger.warning(f"[{ticker}] AI가 유효하지 않은 추가 매수 비중({raw_percentage})을 반환하여 매수를 취소합니다.")
                    elif ai_output.get('decision') == 'Hold':
                        b_hold_reasons[ticker].append(ai_output.get('reason'))
                        bot_utils.logger.info(f"  -> [{ticker}] AI가 후발대 투입을 보류했습니다 (이유: {b_hold_reasons[ticker][-1]})")
                elif decision == 'SELL_VANGUARD':
                    bot_utils.logger.warning(f"[{ticker}] 1시간봉 종가 손절 조건 충족. 선발대 전량 매도를 시도합니다.")
                    precision = config.TICKER_CONFIG.get(ticker, Decimal('0.00000001'))
                    amount_to_sell = state['total_position_size'].quantize(precision)
                    if amount_to_sell > 0:
                        result_order = upbit.sell_market_order(ticker, float(amount_to_sell))
                        if result_order and 'uuid' in result_order:
                            state['position_status'] = 'ORDER_PENDING'
                            state['pending_order_uuid'] = result_order['uuid']
                            state['pending_order_type'] = 'SELL_VANGUARD'
                            state['pending_order_data'] = data
                            state['pending_order_timestamp'] = pd.Timestamp.now(tz="Asia/Seoul").isoformat()
                            state['pending_warning_sent'] = False # [추가]
                            bot_utils.save_state(bot_states)
                            msg = f"⏳ [{ticker}] 선발대 손절 주문 제출 완료.\n- 체결을 기다립니다."
                            bot_utils.send_telegram_notification(msg)
                            bot_utils.logger.info(f"  -> [{ticker}] {msg.splitlines()[0]}")
                        else:
                            bot_utils.logger.error(f"[{ticker}] 선발대 손절 주문 제출에 실패했습니다. API 응답: {result_order}")
                    else:
                        bot_utils.logger.warning(f"[{ticker}] 매도할 수량이 없습니다.")
                elif decision == 'EVALUATE_TAKE_PROFIT':
                    ai_output = ai_interface.get_ai_take_profit_decision(ticker, data, c_hold_reasons[ticker])
                    ai_decision = ai_output.get('decision')
                    percentage = float(ai_output.get('percentage', 0))
                    reason = ai_output.get('reason')
                    if ai_decision == 'Sell' and 0.1 <= percentage <= 1.0:
                        if percentage < 1.0:
                            bot_utils.logger.info(f"[{ticker}] AI 결정에 따라 {percentage*100:.1f}% 부분 익절을 시도합니다.")
                            pending_order_type = 'SELL_PARTIAL'
                        else:
                            bot_utils.logger.info(f"[{ticker}] AI 결정에 따라 100% 전량 익절을 시도합니다.")
                            pending_order_type = 'SELL_ALL_FINAL'
                        precision = config.TICKER_CONFIG.get(ticker, Decimal('0.00000001'))
                        amount_to_sell_raw = state['total_position_size'] * Decimal(str(percentage))
                        amount_to_sell = amount_to_sell_raw.quantize(precision)
                        if amount_to_sell > 0:
                            result_order = upbit.sell_market_order(ticker, float(amount_to_sell))
                            if result_order and 'uuid' in result_order:
                                state['position_status'] = 'ORDER_PENDING'
                                state['pending_order_uuid'] = result_order['uuid']
                                state['pending_order_type'] = pending_order_type
                                state['pending_order_data'] = {'reason': reason, 'percentage': percentage}
                                state['pending_order_timestamp'] = pd.Timestamp.now(tz="Asia/Seoul").isoformat()
                                state['pending_warning_sent'] = False # [추가]
                                bot_utils.save_state(bot_states)
                                msg = f"⏳ [{ticker}] AI 결정({percentage*100:.0f}%) 매도 주문 제출 완료.\n- 체결을 기다립니다."
                                bot_utils.send_telegram_notification(msg)
                                bot_utils.logger.info(f"  -> [{ticker}] {msg.splitlines()[0]}")
                            else:
                                bot_utils.logger.error(f"[{ticker}] AI 결정 매도 주문 제출에 실패했습니다. API 응답: {result_order}")
                        else:
                            bot_utils.logger.warning(f"[{ticker}] 매도할 수량이 없습니다.")
                    elif ai_decision == 'Hold':
                        c_hold_reasons[ticker].append(reason)
                        bot_utils.logger.info(f"  -> [{ticker}] AI가 익절을 보류했습니다 (이유: {c_hold_reasons[ticker][-1]})")
                elif decision == 'UPDATE_TRAILING_STOP_PRICE':
                    state['supertrend_stop_price'] = data.get('stop_price', state['supertrend_stop_price'])
                    bot_utils.save_state(bot_states)
                    bot_utils.logger.info(f"  -> [{ticker}] SuperTrend Stop 가격 갱신: {state['supertrend_stop_price']:,.0f}원")
                if decision == 'SELL_REMAINDER' or decision == 'SELL_ALL_FINAL':
                    reason_text = "잔량 익절(SuperTrend)" if decision == 'SELL_REMAINDER' else "포지션 종료(BB손절)"
                    if 'reason' in data:
                        reason_text += f"({data.get('reason')})"
                    bot_utils.logger.warning(f"[{ticker}] {reason_text}. 남은 물량 전체를 매도합니다.")
                    precision = config.TICKER_CONFIG.get(ticker, Decimal('0.00000001'))
                    amount_to_sell = state['total_position_size'].quantize(precision)
                    if amount_to_sell > 0:
                        result_order = upbit.sell_market_order(ticker, float(amount_to_sell))
                        if result_order and 'uuid' in result_order:
                            state['position_status'] = 'ORDER_PENDING'
                            state['pending_order_uuid'] = result_order['uuid']
                            state['pending_order_type'] = decision
                            state['pending_order_data'] = data
                            state['pending_order_timestamp'] = pd.Timestamp.now(tz="Asia/Seoul").isoformat()
                            state['pending_warning_sent'] = False # [추가]
                            bot_utils.save_state(bot_states)
                            msg = f"⏳ [{ticker}] {reason_text} 주문 제출 완료.\n- 체결을 기다립니다."
                            bot_utils.send_telegram_notification(msg)
                            bot_utils.logger.info(f"  -> [{ticker}] {msg.splitlines()[0]}")
                        else:
                            bot_utils.logger.error(f"[{ticker}] {reason_text} 주문 제출에 실패했습니다. API 응답: {result_order}")
                    else:
                        bot_utils.logger.warning(f"[{ticker}] 매도할 수량이 없습니다.")

            try:
                # 1. 모든 코인의 실현된 자본(capital)을 합산
                total_realized_capital = sum(
                    s.get('capital', Decimal('0')) for s in bot_states.values()
                )

                # 2. 현재 보유 중인 포지션의 미실현 손익(unrealized PnL) 계산
                unrealized_pnl = Decimal('0')
                for ticker, state in bot_states.items():
                    if state['position_status'] != 'NONE' and state['total_position_size'] > 0:
                        current_price = data_cache.get(ticker, {}).get('price')
                        if current_price:
                            # 현재 평가금액 = 현재가 * 보유수량
                            market_value = Decimal(str(current_price)) * state['total_position_size']
                            # 진입 비용 = 평단가 * 보유수량
                            cost = state['avg_entry_price'] * state['total_position_size']
                            unrealized_pnl += (market_value - cost)
                
                # 3. 최종 총자산 = 실현 자본 + 미실현 손익
                total_equity = total_realized_capital + unrealized_pnl
                bot_utils.log_capital(float(total_equity)) # 로그 파일에는 float으로 기록
                bot_utils.logger.info(f"총자산 기록 완료: {total_equity:,.0f}원 (실현자본: {total_realized_capital:,.0f}, 미실현손익: {unrealized_pnl:,.0f})")

            except Exception as e:
                bot_utils.logger.error(f"총자산 기록 중 오류 발생: {e}")

            bot_utils.logger.info(f"모든 코인 확인 완료. 1분 후 다음 주기를 시작합니다.")
            time.sleep(60)

        except Exception as e:
            error_msg = f"메인 루프 오류 발생: {e}"
            bot_utils.logger.error(error_msg)
            bot_utils.send_telegram_notification(f"🚨 봇 중단 위기! 🚨\n{error_msg}")
            time.sleep(60)