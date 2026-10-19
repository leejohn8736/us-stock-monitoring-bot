"""GitHub Actions 등을 통해 정해진 시간에 브리핑을 단발성으로 실행하고 텔레그램으로 전송하는 스크립트"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime

from telegram import Bot

from us_market_bot.config import Settings
from us_market_bot.database import MarketDatabase
from us_market_bot.journal import Journal
from us_market_bot.providers.alpaca import MarketDataError
from us_market_bot.report import split_report
from us_market_bot.research import ResearchService

log = logging.getLogger("us-market-bot-runner")


async def post_single_briefing(kind: str) -> None:
    settings = Settings.from_env()
    database = MarketDatabase(settings.database_path)
    journal = Journal(settings.database_path)
    database.initialize()

    bot_client = Bot(token=settings.telegram_bot_token)
    research = ResearchService(settings, database)

    log.info("브리핑 생성 시작 (종류: %s)", kind)
    if kind == "morning":
        edition = await asyncio.to_thread(research.morning)
    elif kind == "close":
        edition = await asyncio.to_thread(research.close)
    else:
        raise ValueError(f"지원하지 않는 브리핑 종류입니다: {kind}")

    for index, chunk in enumerate(split_report(edition["body"])):
        state = journal.delivery_state(edition["id"], index)
        if state == "sent":
            continue
        if not journal.claim(edition["id"], index):
            raise RuntimeError("발송 응답 미확정 기록이 있습니다.")
        
        sent_msg = await bot_client.send_message(
            chat_id=settings.telegram_chat_id,
            text=chunk,
            disable_web_page_preview=True,
        )
        journal.delivered(edition["id"], index, sent_msg.message_id)
    
    log.info("브리핑 텔레그램 전송 완료 (%s, 날짜: %s)", kind, edition["trade_date"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["morning", "close"], required=True)
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(post_single_briefing(args.kind))
    except (MarketDataError, RuntimeError, ValueError) as exc:
        log.error("브리핑 발송 실패: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
