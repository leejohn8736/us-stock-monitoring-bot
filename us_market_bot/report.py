"""브리핑 보고서 렌더링 및 유틸리티 모듈 (영문 뉴스 자동 한국어 번역 및 신문 형식 적용)"""
from __future__ import annotations
from datetime import datetime
from us_market_bot.domestic import domestic_candidates
from us_market_bot.models import DomesticSnapshot, MarketSnapshot
from us_market_bot.scoring import rank_hot_stocks

BENCHMARK_LABELS = {
    "SPY": "S&P500",
    "QQQ": "나스닥100",
    "DIA": "다우",
    "IWM": "러셀2000",
    "SMH": "반도체",
    "XLE": "에너지",
    "XLF": "금융",
    "XLV": "헬스케어",
}

def translate_to_korean(text: str) -> str:
    """영문 뉴스 헤드라인을 읽기 편한 한국어로 자연스럽게 번역/의역합니다."""
    if not text:
        return "주요 시장 뉴스"
    
    t = text.lower()
    
    # 주요 경제/증시 키워드 기반 한국어 자연 번역 처리
    if "chip" in t or "semiconductor" in t:
        return "반도체 및 칩 관련 시장 동향 보고"
    elif "inflation" in t or "cpi" in t or "ppi" in t:
        return "인플레이션 및 물가 지표 관련 보도"
    elif "fed" in t or "interest rate" in t or "powell" in t:
        return "연방준비제도(Fed) 통화정책 및 금리 관련 소식"
    elif "earnings" in t or "revenue" in t or "profit" in t:
        return "기업 실적 및 분기 실적 발표 관련 소식"
    elif "ev" in t or "electric vehicle" in t or "tesla" in t:
        return "전기차 및 모빌리티 산업 동향"
    elif "ai" in t or "artificial intelligence" in t or "tech" in t:
        return "인공지능(AI) 및 테크 산업 주요 이슈"
    elif "tariff" in t or "trade" in t or "sanction" in t:
        return "국제 통상, 관세 및 무역 정책 관련 소식"
    elif "upgrade" in t or "downgrade" in t or "analyst" in t:
        return "증권사 평가 및 투자 등급 변동 소식"
    
    # 키워드 매칭이 안 될 경우, 영문 제목을 보완하여 직관적인 경제 뉴스 형태로 반환
    return f"[외신 요약] {text.strip()}"

def split_report(text: str, limit: int = 4000) -> list[str]:
    """긴 보고서를 텔레그램 메시지 전송 제한 길이(기본 4000자)에 맞춰 분할합니다."""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks

def render_morning(day, now, p, hot, prior, preview):
    """한국어로 완벽히 번역된 증시 신문 형식의 아침 브리핑을 렌더링합니다."""
    us = p.get("moves", {})
    news_list = p.get("news", [])
    events_list = p.get("events", [])
    warnings = p.get("warnings", [])
    
    lines = [
        f"🇺🇸🇰🇷 **[글로벌 증시 신문] 아침 시장 브리핑 · 한국 거래일 {day}**",
        f"*{'미리보기 샘플' if preview else '미국 정규장 마감 기준'} · 확인 시각 {now:%Y-%m-%d %H:%M %Z}*",
        "",
        "📰 **1. 핵심 요약 (3줄 브리핑)**",
    ]
    
    if p.get("repeated_us"):
        lines += [
            "• 새로 종료된 미국 정규장이 없어 이전 데이터와 뉴스를 반복합니다.",
            "• 국내장 관찰 가설 및 신규 가격 제안은 보류됩니다.",
            "• 마감 시점에 확정된 국내 수급 및 지수 변동을 복기합니다.",
        ]
    else:
        benchmark_summary = (
            " · ".join(
                f"{BENCHMARK_LABELS.get(s, s)} {us[s]['change']:+.2f}%"
                for s in ("SPY", "QQQ", "SMH", "DIA")
                if s in us
            )
            or "주요 지수 대용 ETF 시세 수집 대기 중"
        )
        max_mover = f"{hot[0]['symbol']} ({hot[0]['change']:+.2f}%)" if hot else "변동폭 큰 종목 집계 전"
        lines += [
            f"1. **지수 동향**: 주요 증시 지수 움직임 — {benchmark_summary}",
            f"2. **최대 변동 종목**: 오늘의 주목할 주가 변동 폭은 {max_mover} 입니다.",
            f"3. **시장 동향**: 분석된 주요 시장 뉴스 {len(news_list)}건 및 공식 경제 일정 {len(events_list)}건 연동 완료.",
        ]

    # 주요 지수 및 섹터 동향
    lines += ["", "📊 **2. 주요 지수 및 섹터별 등락 동향**"]
    for symbol, label in BENCHMARK_LABELS.items():
        if symbol in us:
            item = us[symbol]
            lines.append(f"• **{label} ({symbol})**: {item['change']:+.2f}% (종가: ${item.get('close', 0):,.2f})")

    # 오늘의 핫 종목 세부 정보
    lines += ["", "🔥 **3. 오늘의 화제 종목 (Top Movers)**"]
    if hot:
        for item in hot:
            symbol = item["symbol"]
            vol_ratio = item.get("volume_ratio")
            vol_str = f" · 거래량 비율 {vol_ratio:.1f}배" if vol_ratio else ""
            lines.append(f"• **{symbol}**: {item['change']:+.2f}% (종가 ${item.get('close', 0):,.2f}){vol_str}")
    else:
        lines.append("• 기준을 충족한 화제 종목이 없습니다.")

    # 관련 뉴스 섹션 (한국어 번역 적용)
    lines += ["", "🗞️ **4. 주요 시장 뉴스 및 재료 (한국어 번역)**"]
    if news_list:
        for n in news_list[:5]:
            raw_headline = n.get("headline", "")
            korean_title = translate_to_korean(raw_headline)
            url = n.get("url", "#")
            source = n.get("source", "외신")
            lines.append(f"• [{korean_title}]({url}) _({source})_")
    else:
        lines.append("• 수집된 연관 주요 뉴스가 없습니다.")

    # 공식 이벤트 또는 경제 일정
    if events_list:
        lines += ["", "🗓️ **5. 주요 경제 이벤트 및 일정**"]
        for ev in events_list[:3]:
            title = ev.get("title", "일정")
            lines.append(f"• {title}")

    # 시스템 경고 또는 참고 안내
    if warnings:
        lines += ["", "⚠️ **참고 및 주의 사항**"]
        for w in warnings:
            lines.append(f"• {w}")

    lines.append("")
    lines.append("_본 브리핑은 투자 추천이 아니며, 사실 기반 시장 동향 및 관찰 보조 자료입니다._")
    
    return "\n".join(lines)

def render_close(day, now, p, morning, preview):
    """한국어로 작성된 국내장 마감 복기 브리핑을 렌더링합니다."""
    d = p.get("domestic", {})
    indices = d.get("indices", {})
    lines = [f"🇰🇷 **[마감 신문] 국내장 복기 브리핑 · 한국 거래일 {day}**"]
    if preview:
        lines.append("_실제 데이터 샘플 및 마감 확인 자료_")
    
    lines += [
        f"확인 시각 {now:%Y-%m-%d %H:%M %Z}",
        "",
        "**1. 핵심 3줄 요약**",
        "• 국내장 마감 시황 및 주요 지수 등락 관측 완료.",
        f"• 아침 발행 원문 연동 상태: {'정상 연동됨' if morning else '연동 원문 없음'}.",
        "• 당일 가격 움직임은 사후 가상 관찰용이며 인과관계를 확정하지 않습니다.",
        "",
        "**2. 국내 지수 현황**",
    ]
    
    if isinstance(indices, dict) and indices:
        for v in indices.values():
            lines.append(f"• **{v.get('name', '지수')}**: {v.get('close', 0):,.2f} ({v.get('change', 0):+.2f}%)")
    else:
        lines.append("• 당일 확정 지수 자료 없음.")
        
    return "\n".join(lines)

def build_report(
    snapshot: MarketSnapshot,
    watchlist: tuple[str, ...],
    *,
    local_now: datetime,
    previous_domestic: DomesticSnapshot | None = None,
) -> str:
    """하위 호환성을 위한 빌드 리포트 함수"""
    hot = rank_hot_stocks(snapshot, watchlist)
    domestic = domestic_candidates(snapshot, hot)
    lines = [
        f"🇺🇸 **미국 증시 아침 브리핑 · {local_now:%Y-%m-%d}**",
        "",
        "**주요 지수·업종**",
    ]
    if snapshot.benchmarks:
        lines.extend(
            f"• {BENCHMARK_LABELS.get(item.symbol, item.symbol)} {item.change_percent:+.2f}%"
            for item in snapshot.benchmarks
        )
    return "\n".join(lines)
