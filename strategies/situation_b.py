import pandas as pd
import pandas_ta as ta
from bot_utils import logger
import config
from decimal import Decimal

def check(ticker, state, cached_data):
    """
    (최종 수정) 선발대 진입 후, '허가'(4시간봉)를 받고 '정밀 타격'(1시간봉+AI)으로 추가 매수하거나 손절합니다.
    """
    now = pd.Timestamp.now(tz="Asia/Seoul")
    strategy_conf = config.STRATEGY_CONFIG
    cci_len = strategy_conf['cci_length']
    cci_os = strategy_conf['cci_oversold']
    cci_ob = strategy_conf['cci_overbought']
    cci_col = f"CCI_{cci_len}"
    rsi_len = strategy_conf['SHORT_RSI_LENGTH']
    rsi_col = f"RSI_{rsi_len}"

    # --- 1. 손절 조건 확인 (매시 정각, 1시간봉 기준) ---
    if now.minute == 0:
        logger.info(f"[{ticker}] (상황 B) 정각 도달. 손절 및 AI 판단 조건 확인...")
        df_1h = cached_data.get('60m')
        if df_1h is None or len(df_1h) < 2:
            logger.warning(f"[{ticker}] (상황 B) 판단을 위한 1시간봉 데이터가 부족합니다.")
        else:
            # 1-1. 볼린저밴드 하단 이탈 시 즉시 손절
            bbl_col = f"BBL_{strategy_conf['bbands_length']}_{strategy_conf['bbands_std']}"
            if bbl_col not in df_1h.columns:
                df_1h.ta.bbands(length=strategy_conf['bbands_length'], std=strategy_conf['bbands_std'], append=True)
            last_candle = df_1h.iloc[-2]
            if not pd.isna(last_candle['close']) and not pd.isna(last_candle[bbl_col]) and Decimal(str(last_candle['close'])) < Decimal(str(last_candle[bbl_col])):
                reason = f"선발대 손절! 1시간봉 종가({Decimal(str(last_candle['close'])):,.0f})가 BB하단({Decimal(str(last_candle[bbl_col])):,.0f})을 이탈."
                return 'SELL_VANGUARD', {'reason': reason}

            # 1-2. 후발대 투입 '허가' 상태일 경우, 1시간마다 AI에게 정밀 타격 판단 요청
            if state.get('main_force_signal_active', False):
                logger.info(f"[{ticker}] (상황 B) 후발대 투입 '허가' 상태. 1시간봉 기준 AI 정밀 타격 판단 시작...")
                if len(df_1h) < 8:
                    logger.warning(f"[{ticker}] AI 판단을 위한 1시간봉 데이터가 부족합니다.")
                else:
                    # AI 브리핑 데이터 준비
                    current_1h_rsi = df_1h.ta.rsi(length=rsi_len).iloc[-2]
                    last_1h_vol = df_1h['volume'].iloc[-2]
                    prev_6h_mean_vol = df_1h['volume'].iloc[-8:-2].mean()
                    volume_ratio_1h = (last_1h_vol / prev_6h_mean_vol) if prev_6h_mean_vol > 0 else 1.0
                    df_4h = cached_data.get('240m')
                    last_4h_cci = df_4h.ta.cci(length=cci_len).iloc[-2] if df_4h is not None else 0

                    briefing = {
                        "4h_cci_value": last_4h_cci,
                        "1h_rsi_value": current_1h_rsi,
                        "1h_volume_ratio": volume_ratio_1h
                    }
                    return 'EVALUATE_MAIN_FORCE', briefing

    # --- 2. 후발대 투입 '허가' 여부 확인 (매 4시간) ---
    if now.hour in [1, 5, 9, 13, 17, 21] and now.minute == 0:
        logger.info(f"[{ticker}] (상황 B) 4시간 주기 도달. 후발대 투입 '허가' 조건 확인...")
        df_4h = cached_data.get('240m')
        if df_4h is None or len(df_4h) < 3:
            logger.warning(f"[{ticker}] (상황 B) 판단을 위한 4시간봉 데이터가 부족합니다.")
            return None, None

        if cci_col not in df_4h.columns:
            df_4h.ta.cci(length=cci_len, append=True, col_names=(cci_col,))

        prev_cci = df_4h[cci_col].iloc[-3]
        last_cci = df_4h[cci_col].iloc[-2]

        # 2-1. '허가' 신호 확인 (아직 허가받지 않았을 때)
        if not state.get('main_force_signal_active', False) and (prev_cci < cci_os and last_cci > cci_os):
            logger.info(f"✅ [{ticker}] (상황 B) 후발대 투입 '허가' 신호 발생! 1시간 단위 정밀 감시를 시작합니다.")
            return 'ACTIVATE_MAIN_FORCE_SIGNAL', None # main.py에 신호 활성화를 요청
        
        # 2-2. '허가 취소' 신호 확인 (이미 허가받았을 때)
        if state.get('main_force_signal_active', False) and last_cci > cci_ob:
            logger.warning(f"❌ [{ticker}] (상황 B) 가격이 과매수({cci_ob}) 구간에 진입. 후발대 투입 '허가'를 취소합니다.")
            return 'DEACTIVATE_MAIN_FORCE_SIGNAL', None # main.py에 신호 비활성화를 요청

    return None, None