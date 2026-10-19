"""가격 카드 생성 및 배분 관리"""
from __future__ import annotations

RULE_SOURCES = []

def make_card(symbol, name, sector, history, *, market, session_date, valid_session, feed, profile, now):
    """가격 검토 카드를 생성합니다."""
    if not history or len(history) < 2:
        return None, "가격 히스토리 부족"
    
    latest = history[-1]
    prev = history[-2]
    
    card = {
        "id": f"{session_date}:{symbol}",
        "symbol": symbol,
        "name": name,
        "sector": sector,
        "close": latest["close"],
        "change": 100 * (latest["close"] / prev["close"] - 1),
    }
    return card, None

def allocate(cards, profile, now):
    """카드를 투자 성향에 맞게 배분합니다."""
    pass

def review_card(card, bars):
    """가격 카드를 검토합니다."""
    return {"id": card["id"], "status": "판단 유보", "decision": "대기", "reason": "자동 검토 대기 중"}
