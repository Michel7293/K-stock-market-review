# -*- coding: utf-8 -*-
"""
전체 자동화 파이프라인:
1. 네이버금융에서 상한가 + 15~29.4% 급등(거래대금 상위) 종목 크롤링
2. 종목별 상세정보(업종/시가총액/상장주식수/외국인비율/거래대금) 크롤링
3. 종목별 뉴스 크롤링 + Claude API(웹서치 포함)로 키워드/이슈/테마 요약
4. 구글시트에 새 행으로 추가
5. 주말/한국 공휴일에는 자동으로 건너뜀
"""

import os
import time
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import holidays
import gspread
from google.oauth2.service_account import Credentials

from crawl_naver import (
    fetch_upper_limit_stocks,
    fetch_top_trading_value_stocks,
    fetch_stock_detail,
)
from news_and_summarize import fetch_stock_news, summarize_with_claude

# ===== 아래 4개는 본인 환경에 맞게 수정하세요 =====
SPREADSHEET_ID = "1Ge-z86q2nsXmx5pCpda_Tkkkjm3AgeNDvI5k4PCPECc"
SHEET_NAME = "K Stock2"
SERVICE_ACCOUNT_FILE = "service_account.json"
START_ROW = 1005
# ================================================


def get_worksheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID)
    return sheet.worksheet(SHEET_NAME)


def _to_thousands(value):
    """원 단위 숫자를 천 단위로 변환 (상장주식수(천) 컬럼용)."""
    if value is None:
        return None
    return value / 1000


def _pad_keywords(keywords):
    """키워드가 3개 미만이면 빈칸으로 채워서 항상 3칸을 반환."""
    keywords = list(keywords)[:3]
    while len(keywords) < 3:
        keywords.append(None)
    return keywords


def build_row_for_upper_limit(stock, today_str, enrichment, summary, row_number):
    """상한가 종목 1개를 시트의 한 행(리스트)으로 변환. A~T 20개 컬럼 순서."""
    return [
        today_str,
        stock["rank"],
        stock["continuous_days"],
        stock["cumulative"],
        stock["name"],
        stock["price"],
        f'{stock["change_rate"]}%',
        enrichment.get("trading_value_million"),
        enrichment.get("market_cap_eok"),
        f"=(H{row_number}*0.01)/I{row_number}",
        stock["volume"],
        _to_thousands(enrichment.get("listed_shares")),
        f"=(K{row_number}*0.001)/L{row_number}",
        enrichment.get("foreign_ratio"),
        enrichment.get("industry"),
        *_pad_keywords(summary.get("keywords", [])),
        summary.get("issue"),
        summary.get("theme"),
    ]


def build_row_for_top_value(stock, today_str, enrichment, summary, row_number):
    """15~29.4% 급등(거래대금 상위) 종목 1개를 시트의 한 행으로 변환."""
    return [
        today_str,
        "-",
        "-",
        "-",
        stock["name"],
        enrichment.get("price"),
        f'{stock["change_rate"]}%',
        enrichment.get("trading_value_million"),
        enrichment.get("market_cap_eok"),
        f"=(H{row_number}*0.01)/I{row_number}",
        enrichment.get("volume"),
        _to_thousands(enrichment.get("listed_shares")),
        f"=(K{row_number}*0.001)/L{row_number}",
        enrichment.get("foreign_ratio"),
        enrichment.get("industry"),
        *_pad_keywords(summary.get("keywords", [])),
        summary.get("issue"),
        summary.get("theme"),
    ]


def process_stock(stock, today_str, is_upper_limit, row_number):
    """종목 하나를 상세정보 + 뉴스요약까지 다 처리해서 시트 행으로 만든다."""
    code = stock["code"]
    name = stock["name"]
    print(f"  처리 중: {name} ({code})")

    try:
        enrichment = fetch_stock_detail(code)
    except Exception as e:
        print(f"    [경고] 상세정보 크롤링 실패: {e}")
        enrichment = {}

    try:
        news = fetch_stock_news(code, max_articles=5)
    except Exception as e:
        print(f"    [경고] 뉴스 크롤링 실패: {e}")
        news = []

    try:
        summary = summarize_with_claude(name, news)
    except Exception as e:
        print(f"    [경고] Claude 요약 실패({type(e).__name__}): {e}")
        summary = {"keywords": [], "issue": f"[분석 실패:{type(e).__name__}] {e}", "theme": None}

    if is_upper_limit:
        row = build_row_for_upper_limit(stock, today_str, enrichment, summary, row_number)
    else:
        row = build_row_for_top_value(stock, today_str, enrichment, summary, row_number)

    time.sleep(0.5)
    return row


def is_trading_day(date):
    """주말 또는 한국 공휴일이면 False (휴장일로 간주)."""
    if date.weekday() >= 5:  # 5=토, 6=일
        return False
    kr_holidays = holidays.KR(years=date.year)
    if date in kr_holidays:
        return False
    return True


def main():
    today = datetime.date.today()
    today_str = today.strftime("%Y%m%d")

    if not is_trading_day(today):
        print(f"{today_str}는 주말 또는 공휴일이라 실행하지 않습니다.")
        return

    print("=== 1. 상한가 종목 크롤링 ===")
    upper_stocks = fetch_upper_limit_stocks()
    print(f"{len(upper_stocks)}개 종목 발견")

    print("\n=== 2. 15~29.4% 급등(거래대금 상위) 종목 크롤링 ===")
    top_value_stocks = fetch_top_trading_value_stocks(min_rate=15.0, max_rate=29.4)
    print(f"{len(top_value_stocks)}개 종목 발견")

    print("\n=== 3. 종목별 상세정보 + 뉴스요약 처리 (병렬 처리) ===")
    all_stocks = [(s, True) for s in upper_stocks] + [(s, False) for s in top_value_stocks]

    tasks = []
    current_row = START_ROW
    for stock, is_upper in all_stocks:
        tasks.append((stock, is_upper, current_row))
        current_row += 1

    results = [None] * len(tasks)
    MAX_WORKERS = 1  # 순차 처리 (안정성 우선, 느리지만 동시 요청으로 인한 오류 없음)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_index = {
            executor.submit(process_stock, stock, today_str, is_upper, row_number): i
            for i, (stock, is_upper, row_number) in enumerate(tasks)
        }
        done_count = 0
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            stock, is_upper, row_number = tasks[i]
            try:
                results[i] = future.result()
            except Exception as e:
                print(f"  [경고] 처리 실패({stock.get('name')}): {e}")
                empty_summary = {"keywords": [], "issue": f"[처리 실패] {e}", "theme": None}
                if is_upper:
                    results[i] = build_row_for_upper_limit(stock, today_str, {}, empty_summary, row_number)
                else:
                    results[i] = build_row_for_top_value(stock, today_str, {}, empty_summary, row_number)
            done_count += 1
            print(f"  진행: {done_count}/{len(tasks)}")

    rows = results

    print(f"\n총 {len(rows)}개 행 준비 완료")

    print("\n=== 4. 구글시트에 기록 ===")
    worksheet = get_worksheet()
    end_row = START_ROW + len(rows) - 1

    clear_end_row = START_ROW + 300
    worksheet.batch_clear([f"A{START_ROW}:T{clear_end_row}"])

    cell_range = f"A{START_ROW}:T{end_row}"
    worksheet.update(range_name=cell_range, values=rows, value_input_option="USER_ENTERED")

    percent_format = {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}
    worksheet.format(f"J{START_ROW}:J{end_row}", percent_format)
    worksheet.format(f"M{START_ROW}:M{end_row}", percent_format)

    print(f"완료! {START_ROW}행부터 {end_row}행까지 기록했어요. 시트를 확인해보세요.")


if __name__ == "__main__":
    main()