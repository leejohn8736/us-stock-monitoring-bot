"""Evidence-bound morning newspaper and immutable end-of-day review."""
from datetime import datetime, timedelta
import os

from us_market_bot.calendar import session, latest_closed, next_session
from us_market_bot.journal import Journal
from us_market_bot.prices import make_card, allocate, review_card, RULE_SOURCES
from us_market_bot.report import render_morning, render_close
from us_market_bot.profile import load_profile
from us_market_bot.research_data import ResearchData, EQUITIES, KST, NY
from us_market_bot.providers.alpaca import MarketDataError

LABELS = {
    "SPY": "S&P500 ETF",
    "QQQ": "나스닥100 ETF",
    "DIA": "다우 ETF",
    "IWM": "미국 중소형주 ETF",
    "SMH": "반도체 ETF",
    "XLE": "에너지 ETF",
    "XLF": "금융 ETF",
    "XLV": "헬스케어 ETF",
}

def moves(history, closed):
    """미국 종목의 가격 변동률과 거래량 비율을 계산합니다."""
    result = {}
    for symbol, bars in history.items():
        if (
            len(bars) < 2
            or bars[-1]["date"] != closed.date
            or not all(b["complete"] for b in bars[-2:])
        ):
            continue
        b = bars[-1]
        result[symbol] = {
            **b,
            "symbol": symbol,
            "change": 100 * (b["close"] / bars[-2]["close"] - 1),
            "volume_ratio": b["volume"]
            / (sum(x["volume"] for x in bars[-21:-1]) / len(bars[-21:-1]))
            if len(bars) > 1
            else None,
        }
    return result

def event_path(title):
    text = title.lower()
    for words, explanation in [
        (
            ("earnings", "revenue", "guidance", "profit", "quarter"),
            "실적·전망 보도 → 예상 현금흐름·기업가치 재평가 경로",
        ),
        (
            ("fda", "trial", "approval"),
            "임상·허가 보도 → 개발 성공 가능성·향후 매출 기대 변화 경로",
        ),
        (
            ("tariff", "sanction", "export", "policy"),
            "정책·통상 보도 → 판매 가능 시장·원가·마진 변화 경로",
        ),
        (
            ("upgrade", "downgrade", "price target", "analyst"),
            "증권사 평가 보도 → 단기 기대·투자심리 변화 경로(기업 실적 사실과 구분)",
        ),
        (
            ("contract", "deal", "order", "partnership"),
            "계약·협력 보도 → 매출 기대 변화 경로(실제 규모·수익성 추가 확인 필요)",
        ),
    ]:
        if any(word in text for word in words):
            return (
                "[봇 추론] "
                + explanation
                + ". 헤드라인 분류이며 원문 전체/인과 검증 미완료."
            )
    return "사건 유형과 가격 전이 경로를 확인할 근거 부족: 원인 추정 보류."

def event_price_order(event, item, opening):
    at = datetime.fromisoformat(event["at"])
    if at < datetime.fromisoformat(opening):
        return "보도 시각은 정규장 개장 전입니다. 개장 전 이미 반영됐거나 다른 사건과 겹쳤을 가능성이 있습니다."
    bars = item.get("intraday", [])
    before = [
        b for b in bars if datetime.fromisoformat(b["at"]) + timedelta(minutes=5) <= at
    ]
    after = [
        b
        for b in bars
        if at + timedelta(minutes=15)
        <= datetime.fromisoformat(b["at"]) + timedelta(minutes=5)
        <= at + timedelta(minutes=25)
    ]
    if not before or not after:
        return "보도 전후 비교 가능한 정규장 봉이 부족해 반응 시점 판단 유보."
    change = (after[0]["c"] / before[-1]["c"] - 1) * 100
    return f"[관찰] 보도 전 완료 봉 {before[-1]['at']} → 이후 봉 {after[0]['at']}: {change:+.2f}%. 같은 시간대 다른 사건은 통제하지 못했으므로 인과 확정 아님."

class ResearchService:
    def __init__(self, settings, database, data=None):
        self.settings = settings
        self.database = database
        database.initialize()
        self.journal = Journal(database.path)
        self.data = data or ResearchData(settings)
        self.profile = load_profile(
            os.environ.get("INVESTMENT_PROFILE_PATH", "investment_profile.json")
        )

    def morning(self, now=None, *, preview=False):
        now = (now or datetime.now(KST)).astimezone(KST)
        day = now.date().isoformat()
        target = next_session("XKRX", now) if preview else session("XKRX", day)
        if target is None:
            raise ValueError("한국 휴장일: 아침 가설을 생성하지 않습니다.")

        day = target.date
        existing = self.journal.get(f"morning:{day}")
        if existing and not preview:
            return existing

        closed = latest_closed("XNYS", now)
        previous = self.journal.latest("morning")
        repeated = bool(
            previous
            and previous["payload"].get("moves")
            and previous["payload"].get("us_session") == closed.date
        )
        universe = tuple(
            sorted(set(self.database.watchlist()) | set(EQUITIES) | set(LABELS))
        )
        history = {}
        us = {}
        events = []
        news = []
        cards = []
        withheld = []

        if not repeated:
            try:
                history = self.data.regular_history(universe, closed, count=26)
                us = moves(history, closed)
            except (MarketDataError, ValueError) as exc:
                self.data.warnings.append(
                    f"미국 정규장 시세 수집 실패: {type(exc).__name__}; 가격 제안 보류"
                )
            previous_us = latest_closed("XNYS", closed.open)
            news = self.data.news(universe, previous_us.close, closed.close)
            if now > closed.close:
                news += self.data.news(universe, closed.close, now)
            events = self.data.official_events(previous_us.close, now)

        hot = sorted(
            (p for s, p in us.items() if s not in LABELS),
            key=lambda p: abs(p["change"]),
            reverse=True,
        )[:5]

        if not repeated:
            valid = next_session("XNYS", now)
            current_us = session("XNYS", str(now.astimezone(NY).date()))
            trading_now = bool(current_us and current_us.open <= now < current_us.close)
            for p in hot:
                symbol = p["symbol"]
                if trading_now:
                    withheld.append(
                        f"{symbol}: 미국 정규장 진행 중; 이전 종가 기반 새 가격안 보류"
                    )
                    continue
                if symbol not in EQUITIES:
                    withheld.append(f"{symbol}: 현금 보통주 여부 미확인")
                    continue
                name, sector = EQUITIES[symbol]
                card, reason = make_card(
                    symbol,
                    name,
                    sector,
                    history[symbol],
                    market="US",
                    session_date=closed.date,
                    valid_session=valid,
                    feed="sip",
                    profile=self.profile,
                    now=now,
                )
                if card and len(cards) < 3:
                    cards.append(card)
                elif reason:
                    withheld.append(f"{symbol}: {reason}")

        allocate(cards, self.profile, now)
        payload = {
            "us_session": closed.date,
            "us_open": closed.open,
            "us_close": closed.close,
            "repeated_us": repeated,
            "moves": us,
            "hot": hot,
            "news": [n.__dict__ if hasattr(n, "__dict__") else n for n in news],
            "events": events,
            "hypotheses": [],
            "cards": cards,
            "withheld": withheld,
            "warnings": self.data.warnings,
        }
        body = render_morning(day, now, payload, hot, previous, preview)
        return self.journal.save("morning", day, now, body, payload, preview=preview)

    def close(self, now=None, *, preview=False):
        now = (now or datetime.now(KST)).astimezone(KST)
        day = now.date().isoformat()
        target = session("XKRX", day)
        if target is None:
            raise ValueError("한국 휴장일: 마감 복기를 생성하지 않습니다.")

        morning_edition = self.journal.get(f"morning:{day}")
        morning_payload = morning_edition["payload"] if morning_edition else {}

        snapshot = self.data.domestic_snapshot() if not preview else None
        payload = {
            "morning": morning_payload,
            "domestic": snapshot.__dict__ if snapshot and hasattr(snapshot, "__dict__") else {},
            "reviews": [],
        }
        body = f"🇰🇷 **국내장 마감 복기 · 한국 거래일 {day}**\n\n아침에 세운 가설과 가격안을 검토합니다."
        return self.journal.save("close", day, now, body, payload, preview=preview)
