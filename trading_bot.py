import pandas as pd
import pandas_ta as ta
from decimal import Decimal
from logger_config import logger
import config

class TradingBot:
    def __init__(self, ticker, initial_state=None):
        self.ticker = ticker
        self.state = {
            "capital": Decimal('0'),
            "position_status": "NONE",
            "avg_entry_price": Decimal('0'),
            "total_position_size": Decimal('0'),
            "trailing_stop_active": False,
            "supertrend_stop_price": Decimal('0'),
            "today_date": pd.Timestamp.now(tz="Asia/Seoul").strftime('%Y-%m-%d'),
            "today_pnl": Decimal('0'),
            "trading_enabled": True,
            "entry_date": None,
            "trade_capital": Decimal('0'),
            "last_briefing": None,
            "entry_ai_reasons": [],
            "pending_order_uuid": None,
            "pending_order_type": None,
            "is_take_profit_ready": False
        }
        if initial_state: self.state.update(initial_state)
        self.hold_reasons = []
        self.current_task = 'WAITING_FOR_CONDITION1'
    
    def run_strategy(self, cached_data):
        now = pd.Timestamp.now(tz="Asia/Seoul")
        if not self.state.get('trading_enabled', True): return None, None
        if not cached_data or any(df is None for df in [cached_data.get('15m'), cached_data.get('60m'), cached_data.get('240m')]):
            logger.warning(f"[{self.ticker}] 데이터 부족으로 전략을 실행할 수 없습니다.")
            return None, None

        status = self.state['position_status']
        if status == 'NONE':
            return self._check_entry_conditions(now, cached_data)
        elif status == 'VANGUARD_IN':
            return self._check_main_force_conditions(now, cached_data)
        elif status in ['FULL_POSITION', 'PARTIAL_EXIT']:
            return self._check_exit_conditions(now, cached_data)
        return None, None

    # --- Situation A: 신규 진입 로직 ---
    def _check_entry_conditions(self, now, cached_data):
        is_4h_time = now.hour in [1, 5, 9, 13, 17, 21] and now.minute == 0
        is_1h_time = now.minute == 0
        
        # 3단계 상태 머신 로직
        if self.current_task == 'WAITING_FOR_CONDITION1' and is_4h_time:
            logger.info(f"[{self.ticker}] 4시간 정각. 조건1(4h) 확인...")
            c1 = self._a_check_condition1(cached_data)
            if not c1['passed']: return None, None
            
            c2 = self._a_check_condition2(cached_data)
            if c2['passed']:
                self.current_task = 'AI_ENTRY_MODE'
                data = self._a_prepare_ai_data(cached_data, c1, c2)
                return 'EVALUATE_VANGUARD', data
            else:
                self.current_task = 'WAITING_FOR_CONDITION2'
        
        elif self.current_task == 'WAITING_FOR_CONDITION2' and is_1h_time:
            logger.info(f"[{self.ticker}] 1시간 정각. 조건2(1h) 재확인...")
            c1 = self._a_check_condition1(cached_data)
            if not c1['passed']:
                self.current_task = 'WAITING_FOR_CONDITION1'
                return None, None
            
            c2 = self._a_check_condition2(cached_data)
            if c2['passed']:
                self.current_task = 'AI_ENTRY_MODE'
                data = self._a_prepare_ai_data(cached_data, c1, c2)
                return 'EVALUATE_VANGUARD', data

        elif self.current_task == 'AI_ENTRY_MODE':
            logger.info(f"[{self.ticker}] AI 진입 모드. AI 판단 요청...")
            c1 = self._a_check_condition1(cached_data)
            c2 = self._a_check_condition2(cached_data)
            if not c1['passed'] or not c2['passed']:
                logger.warning(f"[{self.ticker}] AI 모드 중 조건 이탈. 대기 상태로 복귀합니다.")
                self.current_task = 'WAITING_FOR_CONDITION1'
                return None, None
            
            data = self._a_prepare_ai_data(cached_data, c1, c2, is_1h_time)
            return 'EVALUATE_VANGUARD', data
            
        return None, None

    # --- Situation B: 후발대 투입 / 선발대 손절 로직 ---
    def _check_main_force_conditions(self, now, cached_data):
        df_1h = cached_data.get('60m')
        if df_1h is None or len(df_1h) < config.STRATEGY_CONFIG['bbands_length']: return None, None

        # 1. 선발대 손절 조건 (1시간봉 BB 하단 이탈)
        bb_len = config.STRATEGY_CONFIG['bbands_length']
        bb_std = config.STRATEGY_CONFIG['bbands_std']
        bbl_col = f"BBL_{bb_len}_{bb_std}"
        if bbl_col not in df_1h.columns:
            df_1h.ta.bbands(length=bb_len, std=bb_std, append=True)
        
        close_price = Decimal(str(df_1h.iloc[-2]['close']))
        bb_low = Decimal(str(df_1h.iloc[-2][bbl_col]))

        if close_price < bb_low:
            reason = f"1시간봉 종가({close_price:,.0f})가 BB하단({bb_low:,.0f}) 이탈."
            logger.warning(f"[{self.ticker}] {reason}")
            return 'SELL_VANGUARD', {'reason': reason}
            
        # 2. 후발대 투입 '허가' 조건 (4시간 롤링 평균 CCI)
        if now.minute == 0: # 정시에만 확인
            cci_len = config.STRATEGY_CONFIG['cci_length']
            cci_col = f"CCI_{cci_len}"
            if cci_col not in df_1h.columns:
                df_1h.ta.cci(length=cci_len, append=True, col_names=(cci_col,))
            
            if len(df_1h) < cci_len + 4: return None, None
                
            avg_cci_last_4h = df_1h[cci_col].iloc[-5:-1].mean()

            if avg_cci_last_4h > config.STRATEGY_CONFIG['cci_oversold']:
                trigger_reason = f"4시간 롤링 평균 CCI가 {avg_cci_last_4h:.2f}로 {config.STRATEGY_CONFIG['cci_oversold']}을 상회."
                logger.info(f"✅ [{self.ticker}] 후발대 투입 '허가' 신호 포착.")
                data = self._b_prepare_ai_data(cached_data, trigger_reason)
                self.last_briefing_data = data # AI 재호출을 위해 브리핑 데이터 저장
                return 'EVALUATE_MAIN_FORCE', data
        
        # AI Hold 후 15분 재시도 임무 수행
        if self.current_task == 'CHECKING_MAIN_FORCE_EVERY_15_MIN' and self.last_briefing_data:
            logger.info(f"[{self.ticker}] AI Hold 후 15분 경과. 후발대 투입 재시도...")
            return 'EVALUATE_MAIN_FORCE', self.last_briefing_data

        return None, None

    # --- Situation C: 익절 / 최종 손절 로직 ---
    def _check_exit_conditions(self, now, cached_data):
        df_4h = cached_data.get('240m')
        df_1h = cached_data.get('60m')

        # 방어 코드
        if df_4h is None or len(df_4h) < 20 or df_1h is None or len(df_1h) < 29:
            logger.warning(f"[{self.ticker}] 익절/손절 판단 데이터 부족")
            return None, None

        # 1. 최종 손절 조건 (4시간봉 BB 하단 이탈) - 최우선
        if now.hour in [1, 5, 9, 13, 17, 21] and now.minute == 0:
            bb_len = config.STRATEGY_CONFIG['bbands_length']
            bbl_col = f"BBL_{bb_len}_{config.STRATEGY_CONFIG['bbands_std']}"
            if bbl_col not in df_4h.columns: df_4h.ta.bbands(length=bb_len, std=config.STRATEGY_CONFIG['bbands_std'], append=True)
            if df_4h.iloc[-2]['close'] < df_4h.iloc[-2][bbl_col]:
                return 'SELL_ALL_FINAL', {'reason': f"4시간봉 종가가 BB하단 이탈."}
                
        # 2. 1차 익절 조건 (단방향 스위치)
        if not self.state.get('trailing_stop_active', False):
            # 정시에만 1시간봉 조건 확인
            if now.minute == 0:
                is_ready = self.state.get('is_take_profit_ready', False)
                cci_col = f"CCI_{config.STRATEGY_CONFIG['cci_length']}"
                
                # 아직 익절 준비 상태가 아니라면, 4시간봉 CCI를 확인하여 준비 상태로 전환
                if not is_ready:
                    if cci_col not in df_4h.columns: df_4h.ta.cci(length=config.STRATEGY_CONFIG['cci_length'], append=True, col_names=(cci_col,))
                    if df_4h[cci_col].iloc[-2] > config.STRATEGY_CONFIG['cci_overbought']:
                        logger.info(f"✅ [{self.ticker}] 4h CCI 과매수. '익절 준비' 상태로 전환합니다.")
                        self.state['is_take_profit_ready'] = True
                        is_ready = True # 즉시 아래 로직을 탈 수 있도록
                
                # 익절 준비 상태가 되면, 1시간봉 CCI < WMA 조건만 확인
                if is_ready:
                    wma_col = f"WMA_9_{cci_col}"
                    if cci_col not in df_1h.columns: df_1h.ta.cci(length=config.STRATEGY_CONFIG['cci_length'], append=True, col_names=(cci_col,))
                    if wma_col not in df_1h.columns:
                        wma_weights = pd.Series(range(1, 10))
                        df_1h[wma_col] = df_1h[cci_col].rolling(window=9).apply(lambda x: (x * wma_weights).sum() / wma_weights.sum(), raw=True)
                    
                    if df_1h[cci_col].iloc[-2] < df_1h[wma_col].iloc[-2]:
                        trigger_reason = f"4h CCI 과매수 확인 후, 1h CCI가 WMA 하향 돌파."
                        logger.info(f"✅ [{self.ticker}] 1차 익절 평가 신호 포착.")
                        data = self._c_prepare_ai_data(cached_data, trigger_reason)
                        return 'EVALUATE_TAKE_PROFIT', data
        
        # 3. 2차 익절 조건 (SuperTrend)
        else: # trailing_stop_active가 True일 때
            return self._c_check_trailing_stop(cached_data)

        return None, None

    # --- 헬퍼(Helper) 메서드들 ---
    def _calculate_volume_ratio(self, df, timeframe):
        try:
            lookback = {'4h': 6, '1h': 6, '15m': 6}.get(timeframe, 6)
            if df is None or len(df) < lookback + 2: return 1.0
            recent_volume = df['volume'].iloc[-2]
            avg_volume = df['volume'].iloc[-lookback-2:-2].mean()
            return recent_volume / avg_volume if avg_volume > 0 else 1.0
        except Exception as e:
            logger.error(f"[{self.ticker}] 볼륨 비율 계산 오류: {e}")
            return 1.0
    
    def _get_wma(self, series, length=9):
        weights = pd.Series(range(1, length + 1))
        return series.rolling(window=length).apply(lambda x: (x * weights).sum() / weights.sum(), raw=True)

    # Situation A 헬퍼
    def _a_check_condition1(self, cached_data):
        df_4h = cached_data.get('240m')
        if df_4h is None or len(df_4h) < 20:
            logger.warning(f"[{self.ticker}] 조건1 확인을 위한 4시간봉 데이터가 부족합니다.")
            return {'passed': False}
        cci_col, wma_col = f"CCI_20", f"WMA_9_CCI_20"
        if cci_col not in df_4h.columns: df_4h.ta.cci(length=20, append=True, col_names=(cci_col,))
        if wma_col not in df_4h.columns: df_4h[wma_col] = self._get_wma(df_4h[cci_col])
        last = df_4h.iloc[-2]
        cci, wma = last[cci_col], last[wma_col]
        if pd.isna(cci) or pd.isna(wma):
            logger.warning(f"[{self.ticker}] 조건1의 CCI/WMA 지표 계산 실패 (NaN).")
            return {'passed': False}
        passed = cci < -100 and wma < -100
        return {'passed': passed, 'data': {'4h_cci': cci, '4h_wma_cci': wma}}
        
    def _a_check_condition2(self, cached_data):
        df_1h = cached_data.get('60m')
        if df_1h is None or len(df_1h) < 20: return {'passed': False}
        cci_col, wma_col = f"CCI_20", f"WMA_9_CCI_20"
        if cci_col not in df_1h.columns: df_1h.ta.cci(length=20, append=True, col_names=(cci_col,))
        if wma_col not in df_1h.columns: df_1h[wma_col] = self._get_wma(df_1h[cci_col])
        last = df_1h.iloc[-2]
        cci, wma = last[cci_col], last[wma_col]
        passed = cci < -100 and cci > wma
        return {'passed': passed, 'data': {'1h_cci': cci, '1h_wma_cci': wma, 'recovery_strength': cci - wma}}

    def _a_prepare_ai_data(self, cached_data, c1_result, c2_result, is_full_check=True):
        market_data = {}
        timeframes = {'4h': '240m', '1h': '60m', '15m': '15m'}
        for tf_name, tf_key in timeframes.items():
            df = cached_data.get(tf_key)
            rsi_val, vol_ratio = 50.0, 1.0
            if df is not None and len(df) > 14:
                if 'RSI_14' not in df.columns: df.ta.rsi(length=14, append=True)
                rsi_val = df['RSI_14'].iloc[-2]
                vol_ratio = self._calculate_volume_ratio(df, tf_name)
            market_data[tf_name] = {'rsi': rsi_val, 'volume_ratio': vol_ratio}

        analysis_type = "full_verification" if is_full_check else "quick_recheck"
        data = { "analysis_type": analysis_type, "ticker": self.ticker, "market_data": {"timeframes": market_data} }
        if is_full_check:
            data.update({ "condition1_status": c1_result, "condition2_status": c2_result })
        return data
        
    # Situation B 헬퍼
    def _b_prepare_ai_data(self, cached_data, trigger_reason):
        market_data = {}
        timeframes = {'4h': '240m', '1h': '60m', '15m': '15m'}
        for tf_name, tf_key in timeframes.items():
            df = cached_data.get(tf_key)
            rsi_val, vol_ratio = 50.0, 1.0
            if df is not None and len(df) >= 15:
                if 'RSI_14' not in df.columns: df.ta.rsi(length=14, append=True)
                rsi_val = df['RSI_14'].iloc[-2]
                vol_ratio = self._calculate_volume_ratio(df, tf_name)
            market_data[tf_name] = {'rsi': rsi_val, 'volume_ratio': vol_ratio}
        return { "analysis_type": "main_force_timing_check", "ticker": self.ticker, "trigger_reason": trigger_reason, "market_data": { "timeframes": market_data } }

    # Situation C 헬퍼
    def _c_prepare_ai_data(self, cached_data, trigger_reason):
        market_data = {}
        timeframes = {'4h': '240m', '1h': '60m', '15m': '15m'}
        for tf_name, tf_key in timeframes.items():
            df = cached_data.get(tf_key)
            rsi_val, vol_ratio = 50.0, 1.0
            if df is not None and len(df) >= 15:
                if 'RSI_14' not in df.columns: df.ta.rsi(length=14, append=True)
                rsi_val = df['RSI_14'].iloc[-2]
                vol_ratio = self._calculate_volume_ratio(df, tf_name)
            market_data[tf_name] = {'rsi': rsi_val, 'volume_ratio': vol_ratio}
        
        current_price = cached_data.get('price')
        pnl = ((Decimal(str(current_price)) / self.state['avg_entry_price']) - 1) * 100 if self.state['avg_entry_price'] > 0 else Decimal('0')
        return { "analysis_type": "take_profit_timing_check", "ticker": self.ticker, "trigger_reason": trigger_reason, "current_pnl_percentage": pnl, "market_data": { "timeframes": market_data } }
        
    def _c_check_trailing_stop(self, cached_data):
        ts_conf = config.STRATEGY_CONFIG
        st_timeframe_key = ts_conf.get('SUPERTREND_TIMEFRAME', '60m')
        st_period = ts_conf.get('SUPERTREND_PERIOD', 10)
        st_multiplier = ts_conf.get('SUPERTREND_MULTIPLIER', 2.0)
        
        df_ts = cached_data.get(st_timeframe_key)
        if df_ts is None or len(df_ts) < st_period: return None, None

        df_ts.ta.supertrend(length=st_period, multiplier=st_multiplier, append=True)
        
        long_col_name = f"SUPERTl_{st_period}_{st_multiplier:.1f}"
        base_col_name = f"SUPERT_{st_period}_{st_multiplier:.1f}"
        
        supertrend_col_name = None
        if long_col_name in df_ts.columns:
            supertrend_col_name = long_col_name
        elif base_col_name in df_ts.columns:
            supertrend_col_name = base_col_name
        else:
            logger.warning(f"[{self.ticker}] SuperTrend 컬럼({long_col_name} 또는 {base_col_name})을 찾을 수 없습니다.")
            return None, None
        
        current_stop_price_raw = df_ts[supertrend_col_name].iloc[-2]
        if pd.isna(current_stop_price_raw): return None, None

        current_stop_price = Decimal(str(current_stop_price_raw))
        prev_stop_price = self.state['supertrend_stop_price']
        current_price = cached_data.get('price')

        if current_price is not None and Decimal(str(current_price)) < prev_stop_price:
            reason = f"SuperTrend Stop 발동! ({Decimal(str(current_price)):,.0f} < {prev_stop_price:,.0f})"
            return 'SELL_REMAINDER', {'reason': reason}

        if current_stop_price > prev_stop_price:
            return 'UPDATE_TRAILING_STOP_PRICE', {'stop_price': current_stop_price}

        return None, None