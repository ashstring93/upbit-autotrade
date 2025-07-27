import json
import google.generativeai as genai
from bot_utils import logger
import config
import re

def get_ai_decision(ticker, briefing, previous_reasons=None):
    """
    AI에게 신규 진입 최종 판단을 요청하는 함수.
    """
    try:
        genai.configure(api_key=config.GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-pro')

        # Hold 이유가 리스트일 경우, 번호 매겨서 문자열로 변환
        reason_history = "None"
        if previous_reasons and isinstance(previous_reasons, list):
            reason_history = "\n".join([f"{i+1}. {reason}" for i, reason in enumerate(previous_reasons)])

        prompt = f"""
        You are an AI assistant for cryptocurrency trading strategies.
        A long-term (4h) and mid-term (1h) buying opportunity has been identified.
        Your task is to make the final decision based on the following real-time, short-term data from the 10-minute chart.

        Decide between 'Buy' or 'Hold'. Respond in JSON format.
        - A 'Buy' decision should have a percentage between 0.1 (10%) and 0.5 (50%).
        - Summarize your reasoning in English in no more than three sentences.

        [Analysis Target]
        - Coin: {ticker}

        [Primary Signals]
        - A 4-hour oversold condition and a 1-hour trend reversal signal have been confirmed.

        [Real-time Information (10-minute chart)]
        - 10-minute RSI: {briefing.get('rsi_value', 0):.2f}
        - 10-minute Volume Ratio (vs. previous 60 mins): {briefing.get('volume_ratio', 0):.2f}x

        [Previous 'Hold' Decision Records]
        {reason_history}

        [Instructions]
        Synthesize all information. If the 10-minute data confirms healthy momentum for an entry, recommend 'Buy'. If not, recommend 'Hold'.
        Respond ONLY in the following JSON format.
        ```json
        {{
            "decision": "Buy or Hold",
            "reason": "Your reason for the decision.",
            "percentage": "Investment percentage as a decimal (e.g., 0.3), or 0 for a 'Hold' decision."
        }}
        ```
        """

        response = model.generate_content(prompt)
        logger.info(f"🤖 [{ticker}] AI 신규 진입 판단 원본 답변: {response.text}")
        match = re.search(r'```json\s*(\{.*?\})\s*```', response.text, re.DOTALL)
        if match:
            json_text = match.group(1)
            ai_output = json.loads(json_text)
        else:
            raise ValueError("AI 응답에서 JSON 형식을 찾을 수 없습니다.")
        return ai_output

    except Exception as e:
        logger.error(f"AI 신규 진입 판단 중 오류 발생: {e}")
        return {"decision": "Hold", "reason": "An error occurred while processing the AI response.", "percentage": 0}


def get_ai_main_force_decision(ticker, briefing, previous_reasons=None):
    """
    AI에게 후발대 투입 비중(%)을 결정하도록 요청하는 함수.
    """
    try:
        genai.configure(api_key=config.GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-pro')
        
        # Hold 이유가 리스트일 경우, 번호 매겨서 문자열로 변환
        reason_history = "None"
        if previous_reasons and isinstance(previous_reasons, list):
            reason_history = "\n".join([f"{i+1}. {reason}" for i, reason in enumerate(previous_reasons)])

        prompt = f"""
        You are an AI assistant for cryptocurrency trading strategies.
        After an initial 'vanguard' entry based on a 1-hour signal, a stronger confirmation signal has appeared on the 4-hour chart.
        Your task is to decide whether to deploy the 'main force' now or to 'Hold'.

        Based on the data below, assess the reliability of the current upward trend.
        Decide between 'BUY_MAIN_FORCE' or 'Hold', and respond in JSON format.
        - For a 'BUY_MAIN_FORCE' decision, recommend an investment percentage for the remaining capital, from 50% to 100% (0.5 to 1.0).
        - Summarize your reasoning in English in no more than three sentences.

        [Analysis Target]
        - Coin: {ticker}

        [Confirmation Signal]
        - 4-hour CCI: Just broke out of the oversold zone (current value: {briefing.get('4h_cci_value', 0):.2f}). This is a strong mid-term confirmation.

        [Real-time Information (1-hour timeframe)]
        - 1-hour RSI: {briefing.get('1h_rsi_value', 0):.2f}
        - 1-hour Volume Ratio (vs. previous 6 hours): {briefing.get('1h_volume_ratio', 0):.2f}x

        [Previous Decision Record]
        - Reason for previous 'Hold': [Previous 'Hold' Decision Records]\n{reason_history}

        [Instructions]
        Synthesize all the information. If the 1-hour data confirms a healthy, non-overheated trend, recommend 'BUY_MAIN_FORCE'.
        If it looks risky or lacks strength, recommend 'Hold'.
        Respond in the following JSON format.
        ```json
        {{
            "decision": "BUY_MAIN_FORCE or Hold",
            "reason": "Your reason for the decision.",
            "percentage": "Investment percentage for 'BUY_MAIN_FORCE' (e.g., 0.75)."
        }}
        ```
        """
        response = model.generate_content(prompt)
        logger.info(f"🤖 [{ticker}] AI 후발대 투입 판단 원본 답변: {response.text}")
        match = re.search(r'```json\s*(\{.*?\})\s*```', response.text, re.DOTALL)
        if match:
            json_text = match.group(1)
            ai_output = json.loads(json_text)
        else:
            raise ValueError("AI 응답에서 JSON 형식을 찾을 수 없습니다.")
        return ai_output
        
    except Exception as e:
        logger.error(f"AI 후발대 판단 중 오류 발생: {e}")
        return {"decision": "Hold", "reason": "AI 응답 처리 중 오류 발생.", "percentage": 0}
        
def get_ai_take_profit_decision(ticker, briefing, previous_reasons=None):
    """
    AI에게 익절 여부와 비중을 결정하도록 요청하는 함수.
    """
    try:
        genai.configure(api_key=config.GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-pro')

        # Hold 이유가 리스트일 경우, 번호 매겨서 문자열로 변환
        reason_history = "None"
        if previous_reasons and isinstance(previous_reasons, list):
            reason_history = "\n".join([f"{i+1}. {reason}" for i, reason in enumerate(previous_reasons)])

        prompt = f"""
        You are an AI assistant for cryptocurrency trading strategies.
        A position is in profit, and a mid-term (4-hour chart) take-profit signal has been detected based on the CCI indicator.
        Your task is to decide whether to 'Sell' or 'Hold' based on the real-time momentum from the 1-hour chart.

        - 'Sell': Recommend a sell percentage between 0.1 (10%) and 1.0 (100%).
        - 'Hold': Decide to keep the full position (percentage should be 0).
        - Summarize your reasoning in English in no more than three sentences.

        [Analysis Target]
        - Coin: {ticker}
        - Current Unrealized PnL: +{briefing.get('pnl_percentage', 0):.2f}%

        [Primary Sell Signal (4-hour timeframe)]
        - Trigger Reason: {briefing['trigger_reason']}

        [Real-time Momentum Data (1-hour timeframe)]
        - 1-hour RSI: {briefing.get('1h_rsi_value', 0):.2f}
        - 1-hour Volume Ratio (vs. previous 6 hours): {briefing.get('1h_volume_ratio', 0):.2f}x

        [Previous Decision Record]
        - Reason for previous 'Hold': [Previous 'Hold' Decision Records]\n{reason_history}

        [Instructions]
        Synthesize all information. If the 1-hour data confirms a genuine trend reversal or weakness, recommend 'Sell' with an appropriate percentage.
        If you believe this is a temporary dip with potential for more upside, recommend 'Hold'.
        Respond ONLY in the following JSON format.
        ```json
        {{
            "decision": "Sell or Hold",
            "reason": "Your reason for the decision.",
            "percentage": "Percentage to sell (decimal, 0.1 to 1.0), or 0 for 'Hold'."
        }}
        ```
        """
        response = model.generate_content(prompt)
        logger.info(f"🤖 [{ticker}] AI 익절 판단 원본 답변: {response.text}")
        match = re.search(r'```json\s*(\{.*?\})\s*```', response.text, re.DOTALL)
        if match:
            json_text = match.group(1)
            ai_output = json.loads(json_text)
        else:
            raise ValueError("AI 응답에서 JSON 형식을 찾을 수 없습니다.")
        return ai_output

    except Exception as e:
        logger.error(f"AI 익절 판단 중 오류 발생: {e}")
        return {"decision": "Hold", "reason": "An error occurred while processing the AI response.", "percentage": 0}