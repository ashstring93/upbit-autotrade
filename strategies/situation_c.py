import pandas as pd
import pandas_ta as ta
from bot_utils import logger
import config
from decimal import Decimal
import bot_utils

def check(ticker, state, cached_data):
    """
    (최종 수정) 포지션 보유 시, 익절(CCI), 최종 손절(BB), SuperTrend를 결정합니다.
    """
    # --- 0. 설정 및 데이터 준비 ---
    strategy_conf = config.STRATEGY_CONFIG
    now = pd.Timestamp.now(tz="Asia/Seoul")
    df_4h = cached_data.get('240m')     
    if df_4h is None or len(df_4h) < 3:
        logger.warning(f"[{ticker}] 상황 C 판단을 위한 4시간봉 데이터가 부족합니다.")
        return None, None

    # --- 1. 최종 손절 조건 (최우선 순위) ---
    bbl_col = f"BBL_{strategy_conf['bbands_length']}_{strategy_conf['bbands_std']}"
    if bbl_col not in df_4h.columns:
        df_4h.ta.bbands(length=strategy_conf['bbands_length'], std=strategy_conf['bbands_std'], append=True)
    
    if df_4h.iloc[-2]['close'] < df_4h.iloc[-2][bbl_col]:
        reason = f"최종 손절! 4시간봉 종가({df_4h.iloc[-2]['close']:,.0f})가 BB하단({df_4h.iloc[-2][bbl_col]:,.0f}) 이탈."
        logger.warning(f"[{ticker}] {reason}")
        return 'SELL_ALL_FINAL', {'reason': reason}

    # --- 2. 익절 전략 ---

    # 2-1. Trailing Stop 활성화된 경우: SuperTrend로 추적
    if state.get('trailing_stop_active', False):
        ts_conf = {
            'timeframe': strategy_conf['SUPERTREND_TIMEFRAME'],
            'period': strategy_conf['SUPERTREND_PERIOD'],
            'multiplier': strategy_conf['SUPERTREND_MULTIPLIER']
        }
        df_ts = cached_data.get(ts_conf['timeframe'])

        if df_ts is None or len(df_ts) < ts_conf['period']:
            logger.warning(f"[{ticker}] SuperTrend 계산을 위한 데이터가 부족합니다.")
            return None, None

        supertrend_col_name = bot_utils.get_supertrend_col_name(ts_conf['period'], ts_conf['multiplier'])
        if supertrend_col_name not in df_ts.columns:
            df_ts.ta.supertrend(length=ts_conf['period'], multiplier=ts_conf['multiplier'], append=True)

        prev_stop_price = state.get('supertrend_stop_price', Decimal('0'))
        current_stop_price_raw = df_ts[supertrend_col_name].iloc[-2]

        if not pd.isna(current_stop_price_raw):
            current_stop_price = Decimal(str(current_stop_price_raw))
            current_price = cached_data.get('price')

            # [수정된 로직]
            # 1. (최우선) 현재가가 기존 스탑 가격을 하회했는지 먼저 확인
            if current_price is not None and Decimal(str(current_price)) < prev_stop_price:
                reason = f"SuperTrend Stop 발동! 현재가({Decimal(str(current_price)):,.0f})가 Stop가격({prev_stop_price:,.0f}) 하회."
                logger.warning(f"[{ticker}] {reason}")
                return 'SELL_REMAINDER', {'reason': reason}

            # 2. 매도 조건이 아니라면, 스탑 가격을 올려야 하는지 확인
            if current_stop_price > prev_stop_price:
                return 'UPDATE_TRAILING_STOP_PRICE', {'stop_price': current_stop_price}

        return None, None

    # 2-2. Trailing Stop 비활성 상태: 4시간마다 CCI 조건 확인
    elif now.hour in [1, 5, 9, 13, 17, 21] and now.minute == 0:
        cci_len = strategy_conf['cci_length']
        cci_ob = strategy_conf['cci_overbought']
        cci_col = f"CCI_{cci_len}"
        if cci_col not in df_4h.columns:
            df_4h.ta.cci(length=cci_len, append=True, col_names=(cci_col,))

        last_cci = df_4h[cci_col].iloc[-2]
        prev_cci = df_4h[cci_col].iloc[-3]

        trigger_reason = None
        # 조건 A: CCI 과매수 지속
        if prev_cci > cci_ob and last_cci > cci_ob:
            trigger_reason = "4-hour CCI sustained in overbought zone."
        # 조건 B: CCI 과매수 이탈
        elif prev_cci > cci_ob and last_cci < cci_ob:
            trigger_reason = f"4-hour CCI crossed down the overbought line ({prev_cci:.2f} -> {last_cci:.2f})."

        if trigger_reason:
            logger.info(f"[{ticker}] 익절 평가 신호 포착 ({trigger_reason}). AI 최종 판단 요청...")

            df_1h = cached_data.get('60m')
            if df_1h is None or len(df_1h) < 8:
                logger.warning(f"[{ticker}] AI 판단을 위한 1시간봉 데이터가 부족합니다.")
                return None, None

            # AI 브리핑 데이터 준비
            current_price = cached_data.get('price')
            pnl_percentage = ((Decimal(str(current_price)) / state['avg_entry_price']) - 1) * 100 if state['avg_entry_price'] > 0 else Decimal('0')

            # 1시간봉 RSI 및 거래량 비율 계산
            rsi_col = f"RSI_{config.STRATEGY_CONFIG['SHORT_RSI_LENGTH']}"
            if rsi_col not in df_1h.columns: df_1h.ta.rsi(length=config.STRATEGY_CONFIG['SHORT_RSI_LENGTH'], append=True)
            current_1h_rsi = df_1h[rsi_col].iloc[-2]
            last_1h_vol = df_1h['volume'].iloc[-2]
            prev_6h_mean_vol = df_1h['volume'].iloc[-8:-2].mean()
            volume_ratio_1h = (last_1h_vol / prev_6h_mean_vol) if prev_6h_mean_vol > 0 else 1.0

            briefing_data = {
                "pnl_percentage": pnl_percentage,
                "trigger_reason": trigger_reason,
                "1h_rsi_value": current_1h_rsi,
                "1h_volume_ratio": volume_ratio_1h
            }
            return 'EVALUATE_TAKE_PROFIT', briefing_data

    return None, None