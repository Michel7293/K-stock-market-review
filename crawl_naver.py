# -*- coding: utf-8 -*-
"""
네이버금융에서 '상한가' 및 '15%~29.4% 급등' 종목을 크롤링하는 스크립트.
"""

import re
import time
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

UPPER_LIMIT_URL = "https://finance.naver.com/sise/sise_upper.naver"
TOP_TRADING_VALUE_URL = "https://finance.naver.com/sise/sise_quant.naver"
RATE_RANK_URL = "https://finance.naver.com/sise/sise_rise.naver"
ITEM_DETAIL_URL = "https://finance.naver.com/item/main.naver?code={code}"


def _clean_number(text):
    if text is None:
        return None
    text = text.replace(",", "").replace("%", "").strip()
    text = text.replace("\u2212", "-")
    try:
        return float(text)
    except ValueError:
        return None


def _extract_code_from_link(a_tag):
    if not a_tag or not a_tag.get("href"):
        return None
    m = re.search(r"code=(\d{6}|\w{6})", a_tag["href"])
    return m.group(1) if m else None


_ETF_ETN_BRAND_PREFIXES = [
    "TIGER", "KODEX", "KBSTAR", "SOL", "ACE", "HANARO", "1Q", "PLUS",
    "RISE", "마이다스", "파워", "히어로즈", "타임폴리오", "네비게이터",
    "코세프", "WOORI", "우리", "신한", "베셀", "FOCUS", "iSelect", "TIMEFOLIO",
]
_ETF_ETN_KEYWORDS = [
    "레버리지", "인버스", "합성", "ETN", "국채", "채권", "금현물",
    "선물", "TR", "액티브",
]


def is_etf_or_etn(name):
    if not name:
        return False
    for prefix in _ETF_ETN_BRAND_PREFIXES:
        if name.upper().startswith(prefix.upper()):
            return True
    for kw in _ETF_ETN_KEYWORDS:
        if kw in name:
            return True
    return False


def fetch_upper_limit_stocks():
    """상한가(29.5%+) 종목 리스트를 가져온다 (코스피 + 코스닥 모두)."""
    resp = requests.get(UPPER_LIMIT_URL, headers=HEADERS, timeout=10)
    resp.encoding = "euc-kr"
    soup = BeautifulSoup(resp.text, "lxml")

    tables = soup.select("table.type_5")
    if not tables:
        raise RuntimeError("table.type_5를 찾지 못했습니다. 네이버 페이지 구조가 바뀌었을 수 있어요.")

    results = []
    for table in tables:
        caption = table.select_one("caption")
        market = caption.get_text(strip=True) if caption else None

        for row in table.select("tr"):
            tds = row.select("td")
            if len(tds) < 12:
                continue

            no_text = tds[0].get_text(strip=True)
            if not no_text.isdigit():
                continue

            link = tds[3].select_one("a")
            if link is None:
                continue

            code = _extract_code_from_link(link)
            name = link.get_text(strip=True)
            if is_etf_or_etn(name):
                continue

            change_amount_raw = tds[5].get_text(strip=True)
            change_amount_match = re.search(r"[\d,]+", change_amount_raw)
            change_amount = _clean_number(change_amount_match.group()) if change_amount_match else None

            results.append({
                "market": market,
                "rank": int(no_text),
                "continuous_days": _clean_number(tds[1].get_text(strip=True)),
                "cumulative": _clean_number(tds[2].get_text(strip=True)),
                "code": code,
                "name": name,
                "price": _clean_number(tds[4].get_text(strip=True)),
                "change_amount": change_amount,
                "change_rate": _clean_number(tds[6].get_text(strip=True)),
                "volume": _clean_number(tds[7].get_text(strip=True)),
                "open": _clean_number(tds[8].get_text(strip=True)),
                "high": _clean_number(tds[9].get_text(strip=True)),
                "low": _clean_number(tds[10].get_text(strip=True)),
                "per": _clean_number(tds[11].get_text(strip=True)),
            })

    return results


def fetch_top_trading_value_stocks(min_rate=15.0, max_rate=29.4, max_pages=4):
    """거래대금 상위 페이지에서 min_rate~max_rate 사이로 오른 개별 종목만 골라온다."""
    results = []
    seen_codes = set()
    for sosok in (0, 1):
        for page in range(1, max_pages + 1):
            resp = requests.get(
                TOP_TRADING_VALUE_URL,
                headers=HEADERS,
                params={"sosok": sosok, "page": page},
                timeout=10,
            )
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.select_one("table.type_2")
            if table is None:
                break

            rows = table.select("tr")
            found_any_row = False
            for row in rows:
                link = row.select_one("a.tltle")
                if link is None:
                    continue
                found_any_row = True

                code = _extract_code_from_link(link)
                if code in seen_codes:
                    continue

                name = link.get_text(strip=True)
                if is_etf_or_etn(name):
                    continue

                cell_texts = [c.get_text(strip=True) for c in row.select("td")]
                change_rate = None
                for text in cell_texts:
                    if "%" in text:
                        change_rate = _clean_number(text)
                        break

                if change_rate is None:
                    continue

                if min_rate <= change_rate <= max_rate:
                    results.append({
                        "market": "코스피" if sosok == 0 else "코스닥",
                        "code": code,
                        "name": name,
                        "change_rate": change_rate,
                        "raw_cells": cell_texts,
                    })
                    seen_codes.add(code)

            if not found_any_row:
                break

            time.sleep(0.3)

    results.sort(key=lambda s: s["change_rate"], reverse=True)
    return results


def fetch_rate_rank_stocks(min_rate=15.0, max_rate=29.4, max_pages=6):
    """등락률(상승률) 상위 페이지에서 min_rate~max_rate 사이 개별 종목을 가져온다.

    거래대금상위 목록에서는 초대형주에 밀려 안 잡히는 중형주 급등 종목을 보완하기 위한 용도.
    """
    results = []
    seen_codes = set()
    for sosok in (0, 1):
        for page in range(1, max_pages + 1):
            resp = requests.get(
                RATE_RANK_URL,
                headers=HEADERS,
                params={"sosok": sosok, "page": page},
                timeout=10,
            )
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.select_one("table.type_2")
            if table is None:
                break

            rows = table.select("tr")
            page_min_rate_seen = None
            for row in rows:
                link = row.select_one("a.tltle")
                if link is None:
                    continue

                code = _extract_code_from_link(link)
                name = link.get_text(strip=True)

                cell_texts = [c.get_text(strip=True) for c in row.select("td")]
                change_rate = None
                for text in cell_texts:
                    if "%" in text:
                        change_rate = _clean_number(text)
                        break

                if change_rate is None:
                    continue
                page_min_rate_seen = change_rate

                if is_etf_or_etn(name):
                    continue
                if code in seen_codes:
                    continue

                if min_rate <= change_rate <= max_rate:
                    results.append({
                        "market": "코스피" if sosok == 0 else "코스닥",
                        "code": code,
                        "name": name,
                        "change_rate": change_rate,
                    })
                    seen_codes.add(code)

            if page_min_rate_seen is not None and page_min_rate_seen < min_rate:
                break

            time.sleep(0.3)

    results.sort(key=lambda s: s["change_rate"], reverse=True)
    return results


def fetch_notable_gainers(min_rate=15.0, max_rate=29.4):
    """거래대금상위 + 등락률상위 두 소스를 합쳐서 15~29.4% 구간 종목을 최대한 놓치지 않고 가져온다."""
    from_trading_value = fetch_top_trading_value_stocks(min_rate=min_rate, max_rate=max_rate)
    from_rate_rank = fetch_rate_rank_stocks(min_rate=min_rate, max_rate=max_rate)

    merged = {s["code"]: s for s in from_trading_value}
    for s in from_rate_rank:
        merged.setdefault(s["code"], s)

    results = list(merged.values())
    results.sort(key=lambda s: s["change_rate"], reverse=True)
    return results


def fetch_stock_detail(code):
    """개별 종목 상세 페이지에서 업종/시가총액/상장주식수/외국인비율/거래대금/거래량을 가져온다."""
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(ITEM_DETAIL_URL.format(code=code), headers=HEADERS, timeout=10)
            break
        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    detail = {
        "code": code,
        "industry": None,
        "market_cap_eok": None,
        "listed_shares": None,
        "foreign_ratio": None,
        "trading_value_million": None,
        "price": None,
        "volume": None,
    }

    industry_link = soup.select_one("h4.h_sub.sub_tit7 em a")
    if industry_link:
        detail["industry"] = industry_link.get_text(strip=True)

    blind_dl = soup.select_one("dl.blind")
    if blind_dl:
        for dd in blind_dl.select("dd"):
            text = dd.get_text(strip=True)
            if text.startswith("거래대금"):
                m = re.search(r"[\d,]+", text)
                if m:
                    detail["trading_value_million"] = _clean_number(m.group())
            elif text.startswith("거래량"):
                m = re.search(r"[\d,]+", text)
                if m:
                    detail["volume"] = _clean_number(m.group())
            elif text.startswith("현재가"):
                m = re.search(r"[\d,]+", text)
                if m:
                    detail["price"] = _clean_number(m.group())

    for table in soup.select("table"):
        caption = table.select_one("caption")
        if caption and caption.get_text(strip=True) == "동종업종비교":
            for row in table.select("tr"):
                th = row.select_one("th")
                tds = row.select("td")
                if not th or not tds:
                    continue
                label = th.get_text(strip=True)
                first_val = _clean_number(tds[0].get_text(strip=True))
                if label == "시가총액(억)":
                    detail["market_cap_eok"] = first_val
                elif label == "외국인비율(%)":
                    detail["foreign_ratio"] = first_val
            break

    for table in soup.select("table"):
        caption = table.select_one("caption")
        if caption and caption.get_text(strip=True) == "시가총액":
            for row in table.select("tr"):
                th = row.select_one("th")
                if th and th.get_text(strip=True) == "상장주식수":
                    em = row.select_one("td em")
                    if em:
                        detail["listed_shares"] = _clean_number(em.get_text(strip=True))
            break

    return detail


if __name__ == "__main__":
    print("=== 상한가 종목 크롤링 테스트 ===")
    try:
        upper = fetch_upper_limit_stocks()
        print(f"상한가 종목 수: {len(upper)}")
        for s in upper[:5]:
            print(s)
    except Exception as e:
        print(f"[에러] 상한가 크롤링 실패: {e}")

    print()
    print("=== 15~29.4% 급등 종목 크롤링 테스트 (거래대금+등락률 통합) ===")
    try:
        gainers = fetch_notable_gainers(min_rate=15.0, max_rate=29.4)
        print(f"종목 수: {len(gainers)}")
        for s in gainers[:10]:
            print(s)
    except Exception as e:
        print(f"[에러] 급등 종목 크롤링 실패: {e}")

    print()
    print("=== 종목 상세정보 크롤링 테스트 (SK하이닉스 000660) ===")
    try:
        detail = fetch_stock_detail("000660")
        print(detail)
    except Exception as e:
        print(f"[에러] 상세정보 크롤링 실패: {e}")
