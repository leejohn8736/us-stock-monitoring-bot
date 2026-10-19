"""브리핑 보고서 렌더링 및 유틸리티 모듈"""
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
    """아침 브리핑 본문을 렌더링합니다."""
    us = p.get("moves", {})
    lines = [f"🇺🇸🇰🇷 **아침 주식 브리핑 · 한국 거래일 {day}**"]
    if preview:
        lines.append("**샘플·장전 형식 미리보기: 지금 확인 가능한 자료만 사용합니다.**")
    
    lines += [
        f"확인 시각 {now:%Y-%m-%d %H:%M %Z} · 미국 정규장 {p.get('us_session', '')}",
        "",
        "**핵심 3줄**",
    ]
    
    if p.get("repeated_us"):
        lines += [
            "1. 새로 종료된 미국 정규장이 없어 이전 수치·뉴스를 반복하지 않습니다.",
            "2. 국내 새 가설과 신규 가격안 생성은 보류합니다.",
            "3. 한국장 마감에는 확인된 국내 자료와 기존 원문을 검토합니다.",
        ]
    else:
        benchmark = (
            " · ".join(
                f"{s} {us[s]['change']:+.2f}%"
                for s in ("SPY", "QQQ", "SMH")
                if s in us
            )
            or "정규장 지수 대용 ETF 자료 부족"
        )
        lines += [
            f"1. [사실] {benchmark}.",
            f"2. [사실] 확인 범위 내 최대 변동: {hot[0]['symbol']} {hot[0]['change']:+.2f}%." if hot else "2. 종목별 움직임 비교 자료 부족.",
            f"3. 관찰 가설 {len(p.get('hypotheses', []))}개, 가격 검토 카드 {len(p.get('cards', []))}개.",
        ]

    lines += ["", "**미국 주요 움직임**"]
    for item in hot:
        symbol = item["symbol"]
        lines.append(f"• [사실] **({symbol}) {item['change']:+.2f}%**, 종가 ${item.get('close', 0):,.2f}")

    return "\n".join(lines)

def render_close(day, now, p, morning, preview):
    """마감 브리핑 본문을 렌더링합니다."""
    d = p.get("domestic", {})
    indices = d.get("indices", {})
    lines = [f"🇰🇷 **국내 주식 마감 브리핑 · {day}**"]
    if preview:
        lines.append("**실제 데이터 샘플·확인 시점 마감 자료.**")
    
    lines += [
        f"확인 시각 {now:%Y-%m-%d %H:%M %Z}",
        "",
        "**핵심 3줄**",
        "1. [사실] 국내 마감 시황 관측 완료.",
        f"2. 보존된 당일 아침 원문 {'있음' if morning else '없음'}.",
        "3. 오늘 관측 결과로 인과관계나 수익률을 확정하지 않습니다.",
        "",
        "**오늘의 시장**",
    ]
    
    if isinstance(indices, dict):
        for v in indices.values():
            lines.append(f"• {v.get('name', '지수')} {v.get('close', 0):,.2f}, {v.get('change', 0):+.2f}%")
    
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
