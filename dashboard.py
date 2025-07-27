import streamlit as st
import pandas as pd
import json
import time
import os
from decimal import Decimal
import plotly.graph_objects as go
import pyupbit

# --- 설정 ---
STATE_FILE = "bot_state.json"
TRADE_LOG_FILE = "realtime_trade_log.csv"
CAPITAL_LOG_FILE = "capital_log.csv" # 경로 변수는 그대로 두거나 config에서 import 해도 무방

# --- 페이지 기본 설정 ---
st.set_page_config(page_title="자동매매 봇", page_icon="🤖", layout="wide", initial_sidebar_state="expanded")

# --- 데이터 로딩 함수 (캐시 사용) ---
@st.cache_data(ttl=15)
def load_data():
    """봇의 상태 및 로그 파일을 불러옵니다."""
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state_data = json.load(f, parse_float=Decimal)
    except Exception:
        state_data = {}

    try:
        trade_df = pd.read_csv(TRADE_LOG_FILE)
        if not trade_df.empty:
            trade_df['청산 시점'] = pd.to_datetime(trade_df['청산 시점'], errors='coerce')
            trade_df['진입 시점'] = pd.to_datetime(trade_df['진입 시점'], errors='coerce')
            # [수정] 날짜 변환 실패로 NaT가 된 행을 제거
            trade_df.dropna(subset=['청산 시점', '진입 시점'], inplace=True)
            trade_df['수익/손실'] = pd.to_numeric(trade_df['수익/손실'], errors='coerce')
    except Exception:
        trade_df = pd.DataFrame()

    try:
        capital_df = pd.read_csv(CAPITAL_LOG_FILE)
    except Exception:
        capital_df = pd.DataFrame(columns=['timestamp', 'capital'])
        
    return state_data, trade_df, capital_df

# --- 분석 함수 ---
def calculate_kpis(df):
    """주요 성과 지표(KPI)를 계산합니다."""
    if df.empty or '수익/손실' not in df.columns:
        return {"총 거래": 0, "승률": "0.00%", "수익 팩터": "0.00", "평균 손익": "0 원"}
    
    pnl = df['수익/손실'].dropna()
    if pnl.empty:
        return {"총 거래": 0, "승률": "0.00%", "수익 팩터": "0.00", "평균 손익": "0 원"}
    
    total_trades = len(pnl)
    winning_trades = pnl[pnl > 0]
    losing_trades = pnl[pnl <= 0]
    
    win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
    gross_profit = winning_trades.sum()
    gross_loss = abs(losing_trades.sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    avg_pnl = pnl.mean()

    return {
        "총 거래": total_trades,
        "승률": f"{win_rate:.2f}%",
        "수익 팩터": f"{profit_factor:.2f}",
        "평균 손익": f"{avg_pnl:,.0f} 원"
    }

def create_trade_chart(ticker, trade_df):
    """가격 및 매매 시점 캔들스틱 차트를 생성합니다."""
    try:
        ohlcv = pyupbit.get_ohlcv(ticker, interval="minute60", count=360) # 약 15일치
        if ohlcv is None or ohlcv.empty:
            return None
            
        fig = go.Figure(data=[go.Candlestick(x=ohlcv.index, open=ohlcv['open'], high=ohlcv['high'], low=ohlcv['low'], close=ohlcv['close'], name='가격')])
        
        if not trade_df.empty:
            buys = trade_df
            sells = trade_df[trade_df['청산 시점'].notna()]

            fig.add_trace(go.Scatter(x=buys['진입 시점'], y=buys['평균 진입 가격'], mode='markers', 
                                     marker=dict(color='green', size=10, symbol='triangle-up'), name='Entry'))
            fig.add_trace(go.Scatter(x=sells['청산 시점'], y=sells['청산 가격'], mode='markers', 
                                     marker=dict(color='red', size=10, symbol='triangle-down'), name='Exit'))

        fig.update_layout(title_text=f'{ticker} 가격 및 매매 시점', xaxis_rangeslider_visible=False)
        return fig
    except Exception:
        return None

# --- 메인 대시보드 ---
st.title("🤖 재현이의 AI 자동매매 대시보드")
bot_states, trade_df, capital_df = load_data()

# 사이드바
ticker_list = list(bot_states.keys()) if bot_states else []
options = ["종합 현황"] + ticker_list
choice = st.sidebar.selectbox("표시할 정보 선택", options)
st.sidebar.info(f"마지막 업데이트: {time.strftime('%Y-%m-%d %H:%M:%S')}")

if not bot_states:
    st.warning("봇 상태 파일(bot_state.json)을 찾을 수 없습니다. 봇을 먼저 실행해주세요.")
else:
    # --- 종합 현황 뷰 ---
    if choice == "종합 현황":
        st.header("📊 종합 현황")
        total_capital_display = 0
        if not capital_df.empty:
            total_capital_display = capital_df['capital'].iloc[-1]

        kpis = calculate_kpis(trade_df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 추정 자산", f"{total_capital_display:,.0f} 원")
        c2.metric("승률", kpis['승률'])
        c3.metric("수익 팩터", kpis['수익 팩터'])
        c4.metric("총 거래 수", kpis['총 거래'])
        
        st.subheader("📉 총자산 추이")
        if not capital_df.empty and len(capital_df) > 1:
            capital_df['timestamp'] = pd.to_datetime(capital_df['timestamp'])
            st.line_chart(capital_df.set_index('timestamp'))
        else:
            st.info("자산 기록이 부족하여 추이를 표시할 수 없습니다.")

        st.subheader("🗓️ 월별 수익률")
        if not trade_df.empty and '청산 시점' in trade_df.columns:
            monthly_pnl = trade_df.set_index('청산 시점').resample('M')['수익/손실'].sum()
            st.bar_chart(monthly_pnl)
        else:
            st.info("거래 내역이 없습니다.")

        st.subheader("🧾 전체 거래 내역")
        if not trade_df.empty and '청산 시점' in trade_df.columns:
            st.dataframe(trade_df.sort_values('청산 시점', ascending=False))
        else:
            st.dataframe(trade_df)
        
    # --- 코인별 상세 뷰 ---
    else:
        ticker = choice
        state = bot_states.get(ticker, {})
        # '티커' 컬럼이 있는 경우에만 필터링
        if '티커' in trade_df.columns:
            ticker_trades = trade_df[trade_df['티커'] == ticker]
        else:
            ticker_trades = pd.DataFrame()
        
        st.header(f"📈 {ticker} 상세 분석")

        if not state.get('trading_enabled', True):
            st.error("🚨 일일 손실 한도 초과로 오늘 거래가 중단된 코인입니다.")

        kpis = calculate_kpis(ticker_trades)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("현재 상태", state.get('position_status', 'N/A'))
        c2.metric("승률", kpis['승률'])
        c3.metric("수익 팩터", kpis['수익 팩터'])
        c4.metric("거래 수", kpis['총 거래'])

        trade_chart_fig = create_trade_chart(ticker, ticker_trades)
        if trade_chart_fig:
            st.plotly_chart(trade_chart_fig, use_container_width=True)

        st.subheader(f"💬 {ticker} AI 판단 기록")
        if not ticker_trades.empty:
            display_cols = ['청산 시점', '청산 사유', 'AI 판단 이유', '수익/손실', '수익률(%)']
            st.dataframe(ticker_trades[display_cols].sort_values('청산 시점', ascending=False))
        else:
            st.info("거래 기록이 없습니다.")

# 페이지 자동 새로고침
time.sleep(300)
st.rerun()