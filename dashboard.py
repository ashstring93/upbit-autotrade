import streamlit as st
import pandas as pd
import sqlite3
from decimal import Decimal
import plotly.graph_objects as go
import pyupbit
import config # DB 파일 경로 참조를 위해 추가

# --- 페이지 기본 설정 ---
st.set_page_config(page_title="AI 자동매매 봇 대시보드", page_icon="🤖", layout="wide")

# --- [수정] 데이터 로딩 함수 (DB에서 직접 로드) ---
@st.cache_data(ttl=60) # 60초마다 데이터 캐시 만료
def load_data_from_db():
    """봇의 상태 및 로그를 SQLite DB에서 직접 불러옵니다."""
    try:
        conn = sqlite3.connect(config.DB_FILE, uri=True, check_same_thread=False)
        
        # 1. 봇 상태 로드
        states_df = pd.read_sql_query("SELECT * FROM bot_states", conn)
        # Ticker를 인덱스로 설정하여 딕셔너리처럼 사용
        state_data = states_df.set_index('ticker').to_dict('index')
        
        # 2. 거래 내역 로드
        trade_df = pd.read_sql_query("SELECT * FROM trade_log", conn)
        if not trade_df.empty:
            # 숫자형/날짜형으로 타입 변환
            for col in ['pnl', 'pnl_percentage', 'avg_entry_price', 'exit_price', 'quantity', 'total_fee']:
                trade_df[col] = pd.to_numeric(trade_df[col], errors='coerce')
            for col in ['entry_time', 'exit_time']:
                trade_df[col] = pd.to_datetime(trade_df[col], errors='coerce')
            trade_df.dropna(subset=['exit_time'], inplace=True)

        # 3. 자산 현황 로드
        capital_df = pd.read_sql_query("SELECT * FROM capital_log ORDER BY timestamp ASC", conn)
        if not capital_df.empty:
            capital_df['timestamp'] = pd.to_datetime(capital_df['timestamp'])
            capital_df['total_equity'] = pd.to_numeric(capital_df['total_equity'])

        conn.close()
        
    except Exception as e:
        st.error(f"데이터베이스 로딩 실패: {e}")
        # 오류 발생 시 빈 데이터프레임 반환
        return {}, pd.DataFrame(), pd.DataFrame()

    return state_data, trade_df, capital_df

# --- 분석 함수 ---
def calculate_kpis(df):
    """주요 성과 지표(KPI)를 계산합니다."""
    if df.empty or 'pnl' not in df.columns:
        return {"총 거래": 0, "승률": "0.00%", "수익 팩터": "0.00", "평균 손익": "0 원"}
    
    pnl = df['pnl'].dropna()
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
        ohlcv = pyupbit.get_ohlcv(ticker, interval="minute60", count=360)
        if ohlcv is None or ohlcv.empty:
            st.warning(f"{ticker}의 OHLCV 데이터를 불러올 수 없습니다.")
            return None
            
        fig = go.Figure(data=[go.Candlestick(x=ohlcv.index, open=ohlcv['open'], high=ohlcv['high'], low=ohlcv['low'], close=ohlcv['close'], name='가격')])
        
        if not trade_df.empty:
            ticker_trades = trade_df[trade_df['ticker'] == ticker]
            buys = ticker_trades[ticker_trades['entry_time'].notna()]
            sells = ticker_trades[ticker_trades['exit_time'].notna()]

            fig.add_trace(go.Scatter(x=buys['entry_time'], y=buys['avg_entry_price'], mode='markers', 
                                     marker=dict(color='green', size=10, symbol='triangle-up'), name='Entry'))
            fig.add_trace(go.Scatter(x=sells['exit_time'], y=sells['exit_price'], mode='markers', 
                                     marker=dict(color='red', size=10, symbol='triangle-down'), name='Exit'))

        fig.update_layout(title_text=f'{ticker} 가격 및 매매 시점 (1시간봉)', xaxis_rangeslider_visible=False)
        return fig
    except Exception as e:
        st.error(f"차트 생성 중 오류: {e}")
        return None

# --- 메인 대시보드 ---
st.title("🤖 AI 자동매매 봇 대시보드")

if st.button('새로고침'):
    st.cache_data.clear()

bot_states, trade_df, capital_df = load_data_from_db()

# 사이드바
ticker_list = list(bot_states.keys()) if bot_states else []
options = ["종합 현황"] + sorted(ticker_list)
choice = st.sidebar.selectbox("표시할 정보 선택", options)
st.sidebar.info(f"마지막 데이터 로드: {pd.Timestamp.now(tz='Asia/Seoul').strftime('%Y-%m-%d %H:%M:%S')}")

if not bot_states:
    st.warning("봇 상태 데이터(`trading_bot.db`)를 찾을 수 없거나 데이터가 없습니다. 봇을 먼저 실행해주세요.")
else:
    # --- 종합 현황 뷰 ---
    if choice == "종합 현황":
        st.header("📊 종합 현황")
        total_capital_display = 0
        if not capital_df.empty:
            total_capital_display = capital_df['total_equity'].iloc[-1]

        kpis = calculate_kpis(trade_df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 추정 자산", f"{total_capital_display:,.0f} 원")
        c2.metric("승률", kpis['승률'])
        c3.metric("수익 팩터", kpis['수익 팩터'])
        c4.metric("총 거래 수", kpis['총 거래'])
        
        st.subheader("📉 총자산 추이")
        if not capital_df.empty and len(capital_df) > 1:
            st.line_chart(capital_df.set_index('timestamp')['total_equity'])
        else:
            st.info("자산 기록이 부족하여 추이를 표시할 수 없습니다.")

        st.subheader("🗓️ 월별 수익")
        if not trade_df.empty:
            monthly_pnl = trade_df.set_index('exit_time').resample('M')['pnl'].sum()
            st.bar_chart(monthly_pnl)
        else:
            st.info("거래 내역이 없습니다.")

        st.subheader("🧾 전체 거래 내역")
        st.dataframe(trade_df.sort_values('exit_time', ascending=False))
        
    # --- 코인별 상세 뷰 ---
    else:
        ticker = choice
        state = bot_states.get(ticker, {})
        ticker_trades = trade_df[trade_df['ticker'] == ticker]
        
        st.header(f"📈 {ticker} 상세 분석")

        if not state.get('trading_enabled', True):
            st.error("🚨 일일 손실 한도 초과로 오늘 거래가 중단된 코인입니다.")

        kpis = calculate_kpis(ticker_trades)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("현재 상태", state.get('position_status', 'N/A'))
        c2.metric("할당 자본", f"{Decimal(state.get('capital', 0)):,.0f} 원")
        c3.metric("승률 (해당 코인)", kpis['승률'])
        c4.metric("거래 수 (해당 코인)", kpis['총 거래'])

        trade_chart_fig = create_trade_chart(ticker, trade_df)
        if trade_chart_fig:
            st.plotly_chart(trade_chart_fig, use_container_width=True)

        st.subheader(f"🧾 {ticker} 거래 내역")
        display_cols = ['exit_time', 'exit_reason', 'entry_ai_reason', 'pnl', 'pnl_percentage']
        st.dataframe(ticker_trades[display_cols].sort_values('exit_time', ascending=False))