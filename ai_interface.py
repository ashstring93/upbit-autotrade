import json
import google.generativeai as genai
from logger_config import logger
import config
import re

# Configure the Generative AI model
try:
    genai.configure(api_key=config.GOOGLE_API_KEY)
except Exception as e:
    logger.error(f"Failed to configure Google API key: {e}")

def _parse_ai_response(ticker, response_text, function_name):
    """
    AI의 응답에서 JSON을 추출하고 파싱하는 내부 함수. (Fallback 로직 추가)
    """
    logger.info(f"🤖 [{ticker}] ({function_name}) Original AI Response:\n{response_text}")
    
    try:
        match = re.search(r'```json\s*(\{.*?\})\s*```', response_text, re.DOTALL)
        if match:
            json_text = match.group(1)
            return json.loads(json_text)
        else:
            logger.info(f"[{ticker}] 마크다운 JSON 블록을 찾지 못해 전체 텍스트 파싱을 시도합니다.")
            return json.loads(response_text.strip())
            
    except json.JSONDecodeError as e:
        logger.error(f"AI 응답 JSON 파싱에 최종적으로 실패했습니다: {e}")
        raise ValueError("AI 응답에서 유효한 JSON을 찾을 수 없습니다.")
    except Exception as e:
        logger.error(f"AI 응답 처리 중 예기치 않은 오류 발생: {e}")
        raise ValueError("AI 응답 처리 중 오류가 발생했습니다.")

def get_ai_decision(ticker, briefing, previous_reasons=None):
    """
    Asks the AI to decide on a new entry ('Buy' or 'Hold') based on structured data.
    """
    try:
        model = genai.GenerativeModel('gemini-2.5-pro')

        reason_history = "No previous 'Hold' decisions."
        if previous_reasons:
            reason_history = "\n".join([f"- {reason}" for reason in previous_reasons])

        analysis_type = briefing.get('analysis_type', 'full_verification')
        
        if analysis_type == 'full_verification':
            prompt_details = f"""
            [Technical Conditions Status]
            - Condition 1 (4-hour): {'Passed' if briefing['condition1_status']['passed'] else 'Failed'}
              - 4h CCI: {briefing['condition1_status']['data']['4h_cci']:.2f} (< -100 required)
              - 4h WMA(9) of CCI: {briefing['condition1_status']['data']['4h_wma_cci']:.2f} (< -100 required)
            
            - Condition 2 (1-hour): {'Passed' if briefing['condition2_status']['passed'] else 'Failed'}
              - 1h CCI: {briefing['condition2_status']['data']['1h_cci']:.2f} (< -100 AND > WMA required)
              - 1h WMA(9) of CCI: {briefing['condition2_status']['data']['1h_wma_cci']:.2f}
              - Recovery Strength: {briefing['condition2_status']['data']['recovery_strength']:.2f}
            """
        else: # quick_recheck
            prompt_details = "[Live Market Snapshot for Quick Re-check]"

        prompt = f"""
        You are an AI assistant for a cryptocurrency trading bot. Your task is to decide whether to make an initial 'vanguard' entry for an oversold-recovery strategy.

        **Objective**: Decide between 'Buy' or 'Hold'. You MUST respond ONLY in the specified JSON format.
        - For a 'Buy' decision, the 'percentage' must be between 0.1 (10%) and 0.5 (50%).
        - The 'reason' must be a concise summary of no more than three sentences.

        ---
        [Analysis Target]
        - Ticker: {ticker}

        {prompt_details}

        [Key Market Data]
        - 4-hour   | RSI: {briefing['market_data']['timeframes']['4h']['rsi']:.2f}, Volume Ratio: {briefing['market_data']['timeframes']['4h']['volume_ratio']:.2f}x
        - 1-hour   | RSI: {briefing['market_data']['timeframes']['1h']['rsi']:.2f}, Volume Ratio: {briefing['market_data']['timeframes']['1h']['volume_ratio']:.2f}x
        - 15-minute| RSI: {briefing['market_data']['timeframes']['15m']['rsi']:.2f}, Volume Ratio: {briefing['market_data']['timeframes']['15m']['volume_ratio']:.2f}x

        [Previous 'Hold' Reasons]
        {reason_history}
        ---

        [Analysis Instructions]
        1.  **Quality of Recovery**: Is the CCI's recovery from oversold levels genuine and supported by volume?
        2.  **Momentum Check**: Do the RSIs across timeframes support an entry? Are there signs of a short-term overheat?
        3.  **Risk Assessment**: Have previous 'Hold' reasons been resolved?

        Synthesize all information. If you determine a high-probability setup, recommend 'Buy'. If risk factors are present, recommend 'Hold'.

        **You MUST respond ONLY in the following JSON format:**
        ```json
        {{
            "decision": "Buy or Hold",
            "reason": "Your clear and concise rationale for the decision.",
            "percentage": "Investment percentage as a decimal (e.g., 0.3). Must be 0 for a 'Hold' decision."
        }}
        ```
        """

        response = model.generate_content(prompt)
        return _parse_ai_response(ticker, response.text, "get_ai_decision")

    except ValueError as e: # _parse_ai_response가 발생시키는 오류
        logger.error(f"[{ticker}] AI 응답 파싱 오류: {e}")
        return {"decision": "Hold", "reason": f"AI response parsing failed: {e}", "percentage": 0}
    except Exception as e:
        logger.error(f"[{ticker}] AI 판단 중 일반 오류 발생: {e}")
        return {"decision": "Hold", "reason": f"AI analysis failed: {e}", "percentage": 0}

def get_ai_main_force_decision(ticker, briefing, previous_reasons=None):
    """
    Asks the AI to determine the optimal timing for the 'main force' entry after a mechanical signal.
    """
    try:
        model = genai.GenerativeModel('gemini-2.5-pro')
        
        reason_history = "No previous 'Hold' decisions on this entry."
        if previous_reasons:
            reason_history = "\n".join([f"- {reason}" for reason in previous_reasons])

        prompt = f"""
        You are an AI assistant for a cryptocurrency trading bot. A mechanical signal has confirmed a trend recovery. Your task is to perform a final check and determine the **optimal entry timing** for the 'main force'.

        **Objective**: Decide between 'BUY_MAIN_FORCE' or 'Hold'. Your main goal is to avoid entering at a short-term peak. You MUST respond ONLY in the specified JSON format.
        - For a 'BUY_MAIN_FORCE' decision, recommend an investment percentage from 0.5 (50%) to 1.0 (100%).
        - The 'reason' must be a concise summary of no more than three sentences.
        
        ---
        [Analysis Target]
        - Ticker: {ticker}

        [Primary Signal (Already Met)]
        - Trigger Reason: {briefing.get('trigger_reason', 'N/A')}

        [Live Market Data for Timing Analysis]
        - 4-hour   | RSI: {briefing['market_data']['timeframes']['4h']['rsi']:.2f}, Volume Ratio: {briefing['market_data']['timeframes']['4h']['volume_ratio']:.2f}x
        - 1-hour   | RSI: {briefing['market_data']['timeframes']['1h']['rsi']:.2f}, Volume Ratio: {briefing['market_data']['timeframes']['1h']['volume_ratio']:.2f}x
        - 15-minute| RSI: {briefing['market_data']['timeframes']['15m']['rsi']:.2f}, Volume Ratio: {briefing['market_data']['timeframes']['15m']['volume_ratio']:.2f}x

        [Previous 'Hold' Reasons for This Signal]
        {reason_history}
        ---

        [Analysis Instructions]
        1.  **Check for Overheating (Primary Task)**: Analyze the 15-minute RSI. If it is high (e.g., > 70-75), it indicates a high risk of a short-term pullback. In this case, you should recommend 'Hold'.
        2.  **Confirm Overall Momentum**: If the 15-minute RSI is not overheated, check the 1-hour and 4-hour RSIs. Are they in a healthy uptrend (e.g., > 50)? Is the volume ratio across timeframes supportive?
        3.  **Synthesize**: If the short-term indicators are not over-extended AND the overall momentum is solid, recommend 'BUY_MAIN_FORCE'. Otherwise, recommend 'Hold' and state the reason (e.g., "15m RSI is overbought").

        **You MUST respond ONLY in the following JSON format:**
        ```json
        {{
            "decision": "BUY_MAIN_FORCE or Hold",
            "reason": "Your clear and concise rationale, focusing on entry timing.",
            "percentage": "Investment percentage as a decimal (e.g., 0.75). Must be 0 for 'Hold'."
        }}
        ```
        """
        response = model.generate_content(prompt)
        return _parse_ai_response(ticker, response.text, "get_ai_main_force_decision")
        
    except ValueError as e: # _parse_ai_response가 발생시키는 오류
        logger.error(f"[{ticker}] AI 응답 파싱 오류: {e}")
        return {"decision": "Hold", "reason": f"AI response parsing failed: {e}", "percentage": 0}
    except Exception as e:
        logger.error(f"[{ticker}] AI 판단 중 일반 오류 발생: {e}")
        return {"decision": "Hold", "reason": f"AI analysis failed: {e}", "percentage": 0}

def get_ai_take_profit_decision(ticker, briefing, previous_reasons=None):
    """
    Asks the AI to perform a quality check on a mechanical take-profit signal.
    """
    try:
        model = genai.GenerativeModel('gemini-2.5-pro')

        reason_history = "No previous 'Hold' decisions on this signal."
        if previous_reasons:
            reason_history = "\n".join([f"- {reason}" for reason in previous_reasons])

        prompt = f"""
        You are an AI assistant for a cryptocurrency trading bot. A mechanical signal has detected the first sign of weakening momentum in a strong uptrend. Your task is to analyze the market data and decide if this is a genuine reversal signal requiring a 'Sell' (take profit), or a minor pullback where it's better to 'Hold'.

        **Objective**: Decide between 'Sell' or 'Hold'. You MUST respond ONLY in the specified JSON format.
        - For a 'Sell' decision, recommend a sell percentage between 0.2 (20%) and 0.8 (80%).
        - The 'reason' must be a concise summary of no more than three sentences.

        ---
        [Analysis Target]
        - Ticker: {ticker}
        - Current Unrealized PnL: +{briefing.get('current_pnl_percentage', 0):.2f}%

        [Primary Signal (Already Met)]
        - Trigger Reason: {briefing.get('trigger_reason', 'N/A')}

        [Live Market Data for Quality Check]
        - 4-hour   | RSI: {briefing['market_data']['timeframes']['4h']['rsi']:.2f}, Volume Ratio: {briefing['market_data']['timeframes']['4h']['volume_ratio']:.2f}x
        - 1-hour   | RSI: {briefing['market_data']['timeframes']['1h']['rsi']:.2f}, Volume Ratio: {briefing['market_data']['timeframes']['1h']['volume_ratio']:.2f}x
        - 15-minute| RSI: {briefing['market_data']['timeframes']['15m']['rsi']:.2f}, Volume Ratio: {briefing['market_data']['timeframes']['15m']['volume_ratio']:.2f}x
        
        [Previous 'Hold' Reasons for This Signal]
        {reason_history}
        ---

        [Analysis Instructions]
        1.  **Analyze Signal Strength**: Examine the 1-hour and 15-minute data. Did the RSIs drop sharply? Is the volume ratio on the down-move significant (e.g., > 1.0)? A sharp drop on high volume is a strong confirmation to 'Sell'.
        2.  **Assess Trend Health**: Look at the 4-hour RSI. Is it still very strong (e.g., > 70), suggesting the trend might absorb this dip, or is it also showing signs of weakness?
        3.  **Synthesize**: If the short-term momentum loss is confirmed by volume and the overall trend shows signs of weakening, recommend 'Sell' to protect profits. If the dip is on low volume and the long-term trend remains robust, it might be a healthy pullback, so recommend 'Hold'.

        **You MUST respond ONLY in the following JSON format:**
        ```json
        {{
            "decision": "Sell or Hold",
            "reason": "Your clear and concise rationale for the decision.",
            "percentage": "Sell percentage as a decimal (0.3 to 1.0). Must be 0 for 'Hold'."
        }}
        ```
        """
        response = model.generate_content(prompt)
        return _parse_ai_response(ticker, response.text, "get_ai_take_profit_decision")

    except ValueError as e: # _parse_ai_response가 발생시키는 오류
        logger.error(f"[{ticker}] AI 응답 파싱 오류: {e}")
        return {"decision": "Hold", "reason": f"AI response parsing failed: {e}", "percentage": 0}
    except Exception as e:
        logger.error(f"[{ticker}] AI 판단 중 일반 오류 발생: {e}")
        return {"decision": "Hold", "reason": f"AI analysis failed: {e}", "percentage": 0}