from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta

from telegram import Bot, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from us_market_bot.calendar import session
from us_market_bot.config import Settings
from us_market_bot.database import MarketDatabase, normalize_symbol
from us_market_bot.journal import Journal
from us_market_bot.providers.alpaca import MarketDataError
from us_market_bot.report import split_report
from us_market_bot.research import ResearchService
from us_market_bot.service import BriefingService

log = logging.getLogger("us-market-bot")
LAST_US_REPORT_DATE = "last_telegram_report_date"
LAST_DOMESTIC_REPORT_DATE = "last_domestic_report_date"
LAST_DOMESTIC_ATTEMPT_DATE = "last_domestic_attempt_date"


class USMarketTelegramBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = MarketDatabase(settings.database_path)
        self.service = BriefingService(settings, self.database)
        self.journal = Journal(settings.database_path)
        self.domestic_retry_api_after: datetime | None = None
        self.us_retry_after: datetime | None = None
        self.generation_lock = asyncio.Lock()
        
        # 텔레그램 봇 인스턴스 (직접 메시지 전송용)
        self.bot_client = Bot(token=settings.telegram_bot_token)

    async def send_to_chat(self, text: str) -> None:
        """설정된 텔레그램 채팅(채널/방)으로 메시지를 전송합니다."""
        await self.bot_client.send_message(
            chat_id=self.settings.telegram_chat_id,
            text=text,
            disable_web_page_preview=True,
        )

    async def post_briefing(self) -> str:
        async with self.generation_lock:
            edition = await asyncio.to_thread(
                ResearchService(self.settings, self.database).morning
            )
            await self.send_edition(edition)
            self.database.set_state(LAST_US_REPORT_DATE, edition["trade_date"])
            return edition["body"]

    async def post_domestic_briefing(self, *, require_today: bool = False) -> str:
        async with self.generation_lock:
            edition = await asyncio.to_thread(
                ResearchService(self.settings, self.database).close
            )
            await self.send_edition(edition)
            self.database.set_state(LAST_DOMESTIC_REPORT_DATE, edition["trade_date"])
            return edition["body"]

    async def send_edition(self, edition):
        for index, chunk in enumerate(split_report(edition["body"])):
            state = self.journal.delivery_state(edition["id"], index)
            if state == "sent":
                continue
            if not self.journal.claim(edition["id"], index):
                raise RuntimeError(
                    "발송 응답 미확정 기록이 있어 자동 재전송하지 않습니다. /상태에서 확인하세요."
                )
            # HTTP 전송 전 상태를 먼저 기록하여 중복/유실 방지
            sent_msg = await self.bot_client.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=chunk,
                disable_web_page_preview=True,
            )
            self.journal.delivered(edition["id"], index, sent_msg.message_id)

    async def daily_briefing_loop(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """미국장 아침 브리핑 자동 주기 루프 (1분 간격 체크)"""
        now = datetime.now(self.settings.timezone)
        target = session("XKRX", now.date().isoformat())
        if not target or now >= target.open:
            return
        if (now.hour, now.minute) < (
            self.settings.report_hour,
            self.settings.report_minute,
        ):
            return
        if self.us_retry_after and now < self.us_retry_after:
            return
        today = now.date().isoformat()
        if self.database.get_state(LAST_US_REPORT_DATE) == today:
            return
        try:
            await self.post_briefing()
        except (
            MarketDataError,
            RuntimeError,
            ValueError,
        ) as exc:
            self.us_retry_after = now + timedelta(minutes=15)
            log.error("아침 브리핑 실패: %s", exc)

    async def domestic_briefing_loop(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """국내장 마감 브리핑 자동 주기 루프 (1분 간격 체크)"""
        now = datetime.now(self.settings.timezone)
        target = session("XKRX", now.date().isoformat())
        if not target:
            return
        scheduled = target.close + timedelta(minutes=5)
        if now < scheduled or now >= scheduled + timedelta(hours=2):
            return
        today = now.date().isoformat()
        if self.database.get_state(LAST_DOMESTIC_REPORT_DATE) == today:
            return
        if self.database.get_state(LAST_DOMESTIC_ATTEMPT_DATE) == today:
            return
        if self.domestic_retry_api_after and now < self.domestic_retry_api_after:
            return
        try:
            await self.post_domestic_briefing(require_today=True)
            self.domestic_retry_api_after = None
        except ValueError as exc:
            self.database.set_state(LAST_DOMESTIC_ATTEMPT_DATE, today)
            log.info("국내장 브리핑 생략: %s", exc)
        except (MarketDataError, RuntimeError) as exc:
            self.domestic_retry_api_after = now + timedelta(minutes=30)
            log.error("국내장 브리핑 실패(30분 뒤 재시도): %s", exc)


settings = Settings.from_env()
market_bot = USMarketTelegramBot(settings)


# --- 텔레그램 명령어 핸들러 함수들 ---

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    last_us = market_bot.database.get_state(LAST_US_REPORT_DATE) or "아직 없음"
    last_domestic = market_bot.database.get_state(LAST_DOMESTIC_REPORT_DATE) or "아직 없음"
    msg = (
        f"정상 작동 중입니다 (Telegram).\n"
        f"아침 브리핑: 한국 거래일 {market_bot.settings.report_hour:02d}:{market_bot.settings.report_minute:02d}\n"
        f"국내장 마감: 정규장 종료 5분 후(통상 15:35, {market_bot.settings.timezone.key})\n"
        f"최근 미국장 발송: {last_us}\n최근 국내장 발송: {last_domestic}\n"
        f"발송 결과 미확정: {market_bot.journal.unresolved()}개(자동 재전송 보류)\n"
        "한국 거래일에만 발송 · 특수 개장일은 실제 정규장 종료 5분 후\n"
        "지속 시세 감시 없음 · 주문 API 연결 없음"
    )
    if update.message:
        await update.message.reply_text(msg)


async def latest_briefing_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    edition = market_bot.journal.latest("morning")
    body = edition["body"] if edition else market_bot.database.latest_report()
    if body is None:
        if update.message:
            await update.message.reply_text("아직 생성된 브리핑이 없습니다.")
        return
    for chunk in split_report(body):
        if update.message:
            await update.message.reply_text(chunk, disable_web_page_preview=True)


async def latest_domestic_briefing_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    edition = market_bot.journal.latest("close")
    body = edition["body"] if edition else market_bot.database.latest_domestic_report()
    if body is None:
        if update.message:
            await update.message.reply_text("아직 생성된 국내장 브리핑이 없습니다.")
        return
    for chunk in split_report(body):
        if update.message:
            await update.message.reply_text(chunk, disable_web_page_preview=True)


async def briefing_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("미국 시장 브리핑을 생성하는 중입니다...")
    try:
        await market_bot.post_briefing()
        if update.message:
            await update.message.reply_text("브리핑을 채널에 전송했습니다.")
    except (MarketDataError, RuntimeError, ValueError) as exc:
        if update.message:
            await update.message.reply_text(f"브리핑을 만들지 못했습니다: {exc}")


async def domestic_briefing_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("국내장 마감 브리핑을 생성하는 중입니다...")
    try:
        await market_bot.post_domestic_briefing(require_today=False)
        if update.message:
            await update.message.reply_text("국내장 마감 브리핑을 채널에 전송했습니다.")
    except (MarketDataError, RuntimeError, ValueError) as exc:
        if update.message:
            await update.message.reply_text(f"국내장 브리핑을 만들지 못했습니다: {exc}")


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    symbols = market_bot.database.watchlist()
    msg = ", ".join(f"`{symbol}`" for symbol in symbols)
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown")


async def add_symbol_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        if update.message:
            await update.message.reply_text("사용법: /종목추가 [종목코드]")
        return
    try:
        symbol = normalize_symbol(context.args[0])
        added = market_bot.database.add_symbol(symbol)
    except ValueError as exc:
        if update.message:
            await update.message.reply_text(str(exc))
        return
    msg = f"`{symbol}`을 추가했습니다." if added else f"`{symbol}`은 이미 등록되어 있습니다."
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown")


async def remove_symbol_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        if update.message:
            await update.message.reply_text("사용법: /종목삭제 [종목코드]")
        return
    try:
        symbol = normalize_symbol(context.args[0])
        removed = market_bot.database.remove_symbol(symbol)
    except ValueError as exc:
        if update.message:
            await update.message.reply_text(str(exc))
        return
    msg = f"`{symbol}`을 삭제했습니다." if removed else f"`{symbol}`은 등록되어 있지 않습니다."
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown")


async def criteria_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "최근 완료된 미국 정규장의 원시 SIP 5분봉을 집계합니다.\n"
        "관심종목과 주요 업종 관찰군 안에서 등락·거래량을 확인합니다.\n"
        "가격안은 25세션 이상 자료·관측 지지/저항·ATR·호가 검증을 통과할 때만 제공합니다.\n"
        "투자조건·보유·주문·비용 미확인 시 개인화 수량 없음.\n"
        "아침 원문·가설·가격안 보존, 마감에는 체결과 구분해 복기.\n"
        "지속 감시·자동매매 없음."
    )
    if update.message:
        await update.message.reply_text(msg)


async def post_init(application: Application) -> None:
    """애플리케이션 초기화 시 데이터베이스 준비 및 주기적 작업(JobQueue) 등록"""
    market_bot.database.initialize()
    # 1분마다 주기적으로 아침/국내장 브리핑 시간 조건 체크 수행
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(market_bot.daily_briefing_loop, interval=60, first=10)
        job_queue.run_repeating(market_bot.domestic_briefing_loop, interval=60, first=15)
    log.info("Telegram 봇 초기화 및 백그라운드 스케줄러 등록 완료")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()

    missing = settings.missing(include_telegram=True)
    if args.check_config:
        if missing:
            print("missing: " + ", ".join(missing))
            return 1
        print("configuration ok")
        return 0

    if missing:
        parser.error("필수 설정이 없습니다: " + ", ".join(missing))

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # 텔레그램 Application 빌드 및 핸들러 등록
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("상태", status_command))
    application.add_handler(CommandHandler("오늘브리핑", latest_briefing_command))
    application.add_handler(CommandHandler("국내장브리핑", latest_domestic_briefing_command))
    application.add_handler(CommandHandler("지금브리핑", briefing_now_command))
    application.add_handler(CommandHandler("지금국내장", domestic_briefing_now_command))
    application.add_handler(CommandHandler("관심종목", watchlist_command))
    application.add_handler(CommandHandler("종목추가", add_symbol_command))
    application.add_handler(CommandHandler("종목삭제", remove_symbol_command))
    application.add_handler(CommandHandler("분석기준", criteria_command))

    log.info("Telegram 봇 실행 시작...")
    application.run_polling()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
