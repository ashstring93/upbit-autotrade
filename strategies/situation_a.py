import pandas as pd
import pandas_ta as ta
from bot_utils import logger
import config

def check(ticker, task, cached_data):
    """
    신규 진입을 위한 최종 3단계 조건을 동적으로 확인합니다.
    (수정) 2, 3단계 대기 중 1단계 조건의 유효성을 재확인하여 무효화 시 초기화합니다.
    """
    # --- 설정값 및 변수 선언 ---
    strategy_conf = config.STRATEGY_CONFIG
    cci_len = strategy_conf['cci_length']
    cci_os = strategy_conf['cci_oversold']
    cci_col = f"CCI_{cci_len}"
    rsi_len = strategy_conf['SHORT_RSI_LENGTH']
    rsi_col = f"RSI_{rsi_len}"
    now = pd.Timestamp.now(tz="Asia/Seoul")
    is_4h_update_time = now.hour in [1, 5, 9, 13, 17, 21] and now.minute == 0

    # --- [핵심 수정] 1단계 조건 유효성 재확인 로직 ---
    # 2단계 또는 3단계 대기 중에 새로운 4시간봉이 완성된 경우,
    # 최초 진입 조건이 여전히 유효한지 다시 확인합니다.
    if task in ['WAITING_FOR_1H_SIGNAL', 'CHECKING_EVERY_10_MIN'] and is_4h_update_time:
        logger.info(f"[{ticker}] 새로운 4시간봉 형성. [1단계 조건] 유효성 재확인 시작...")
        df_4h = cached_data.get('240m')
        if df_4h is not None and len(df_4h) > 2:
            if cci_col not in df_4h.columns:
                df_4h.ta.cci(length=cci_len, append=True, col_names=(cci_col,))

            # 가장 최근에 완성된 4시간봉의 CCI 값을 확인
            last_4h_cci = df_4h[cci_col].iloc[-2]

            # 만약 CCI가 과매도 기준선(-100) 위로 올라왔다면, 진입 근거가 사라진 것임
            if last_4h_cci > cci_os:
                logger.warning(f"  -> [{ticker}] [조건 무효화] 4시간봉 CCI({last_4h_cci:.2f})가 과매도 구간을 벗어났습니다. 초기 단계로 복귀합니다.")
                # 임무를 초기 단계로 리셋하고 현재 사이클을 종료
                return 'WAITING_FOR_4H_SIGNAL', None
            else:
                logger.info(f"  -> [{ticker}] [조건 유효] 4시간봉 CCI({last_4h_cci:.2f})가 여전히 과매도 상태입니다. 계속 감시합니다.")
        else:
            logger.warning(f"[{ticker}] 4시간봉 데이터가 부족하여 유효성을 재확인할 수 없습니다.")


    # --- 임무 1: 4시간봉 감시 (신규 진입) ---
    if task == 'WAITING_FOR_4H_SIGNAL':
        if is_4h_update_time:
            logger.info(f"[{ticker}] 4시간 주기 도달. [1단계 조건] 확인 시작...")
            df_4h = cached_data.get('240m')
            if df_4h is None: return task, None

            if cci_col not in df_4h.columns:
                df_4h.ta.cci(length=cci_len, append=True, col_names=(cci_col,))

            if len(df_4h) > 2:
                # 4시간봉 2개 연속 과매도 조건
                if df_4h[cci_col].iloc[-3] < cci_os and df_4h[cci_col].iloc[-2] < cci_os:
                    logger.info(f"  -> [{ticker}] [1단계 조건 통과] '1시간봉 감시 모드'로 전환합니다.")
                    return 'WAITING_FOR_1H_SIGNAL', None
                else:
                    logger.info(f"  -> [{ticker}] [1단계 조건 실패]. 계속 4시간 주기로 감시합니다.")
            else:
                logger.warning(f"[{ticker}] 4시간봉 데이터가 부족하여 1단계 조건을 확인할 수 없습니다.")

        return 'WAITING_FOR_4H_SIGNAL', None

    # --- 임무 2: 1시간봉 감시 (CCI 골든크로스) ---
    elif task == 'WAITING_FOR_1H_SIGNAL':
        if now.minute == 0:
            logger.info(f"[{ticker}] 1시간 주기 도달. [2단계 조건] 확인 시작...")
            df_1h = cached_data.get('60m')
            if df_1h is None: return task, None

            if cci_col not in df_1h.columns:
                df_1h.ta.cci(length=cci_len, append=True, col_names=(cci_col,))
            sma_col = f"SMA_9_of_{cci_col}"
            df_1h[sma_col] = df_1h[cci_col].rolling(window=9).mean()

            if len(df_1h) > 9:
                # 방금 완성된 캔들(-2)에서 골든크로스가 발생했는지 확인
                if df_1h[cci_col].iloc[-3] < df_1h[sma_col].iloc[-3] and df_1h[cci_col].iloc[-2] > df_1h[sma_col].iloc[-2]:
                    logger.info(f"  -> [{ticker}] [2단계 조건 통과] 'AI 최종 판단 모드'로 전환합니다.")
                    return 'CHECKING_EVERY_10_MIN', None
                else:
                    logger.info(f"  -> [{ticker}] [2단계 조건 실패]. 계속 1시간 주기로 감시합니다.")
            else:
                logger.warning(f"[{ticker}] 1시간봉 데이터가 부족하여 2단계 조건을 확인할 수 없습니다.")

        return 'WAITING_FOR_1H_SIGNAL', None

    # --- 임무 3: AI 최종 판단 (10분 주기, 10분봉 기준) ---
    elif task == 'CHECKING_EVERY_10_MIN':
        if now.minute % 10 == 0:
            logger.info(f"[{ticker}] 10분 주기 도달. AI 최종 판단을 위한 데이터 수집...")
            df_10m = cached_data.get('10m')

            if df_10m is None or len(df_10m) < 8:
                logger.warning(f"[{ticker}] AI 판단을 위한 10분봉 데이터가 부족합니다.")
                return task, None

            if rsi_col not in df_10m.columns:
                df_10m.ta.rsi(length=rsi_len, append=True)
            current_rsi = df_10m[rsi_col].iloc[-2]

            last_candle_vol = df_10m['volume'].iloc[-2]
            prev_6_candles_mean_vol = df_10m['volume'].iloc[-8:-2].mean()
            volume_ratio = (last_candle_vol / prev_6_candles_mean_vol) if prev_6_candles_mean_vol > 0 else 1.0

            logger.info(f"  -> [{ticker}] AI에게 전달할 브리핑 데이터를 생성합니다.")
            briefing_data = {
                "volume_ratio": volume_ratio,
                "rsi_value": current_rsi
            }
            return task, briefing_data

        return 'CHECKING_EVERY_10_MIN', None

    return task, None