"""주가 변동성 및 거래량을 바탕으로 주목할 만한 종목을 순위 매기는 모듈"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from us_market_bot.models import MarketSnapshot


def rank_hot_stocks(snapshot: MarketSnapshot, limit: int = 5) -> list:
    """마켓 스냅샷 데이터를 기반으로 상승률과 거래량이 높은 종목을 골라 순위를 매깁니다."""
    # 상승 종목(gainers) 중에서 변동성과 거래량이 큰 종목들을 정렬/추출
    scored_items = []
    
    for gainer in snapshot.gainers:
        scored_items.append({
            "symbol": gainer.symbol,
            "price": gainer.price,
            "change_percent": gainer.change_percent,
            "score": gainer.change_percent,  # 단순 상승률 기준 예시 점수
        })
    
    # 상승률 높은 순으로 정렬
    scored_items.sort(key=lambda x: x["score"], reverse=True)
    return scored_items[:limit]
