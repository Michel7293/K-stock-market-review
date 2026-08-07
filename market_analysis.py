# -*- coding: utf-8 -*-
"""
하루치 상한가/급등 종목 데이터를 바탕으로, 오늘 시장의 섹터/테마별 자금흐름을
분석하고 다음날 전망까지 포함한 리포트를 Claude(Sonnet + 웹서치)로 작성하는 모듈.
"""

import os
import time
import datetime
from collections import Counter, defaultdict


def collect_today_theme_stats(rows):
    """오늘 작성된 행들(20개 컬럼 리스트의 리스트)에서 테마(T열)/업종(O열) 빈도를 집계."""
    themes = [r[19] for r in rows if len(r) > 19 and r[19]]
    industries = [r[14] for r in rows if len(r) > 14 and r[14]]
    return Counter(themes), Counter(industries)


def get_recent_theme_history(worksheet, before_row, num_days=5):
    """오늘 이전(before_row 미만)의 최근 num_days 거래일치 테마 빈도를 시트에서 읽어온다."""
    all_values = worksheet.get_all_values()
    date_theme_map = defaultdict(list)

    for i, row in enumerate(all_values):
        row_number = i + 1
        if row_number >= before_row:
            break
        if len(row) < 20:
            continue
        date_val = row[0].strip()
        theme_val = row[19].strip()
        if date_val.isdigit() and theme_val:
            date_theme_map[date_val].append(theme_val)

    recent_dates = sorted(date_theme_map.keys())[-num_days:]
    return {d: Counter(date_theme_map[d]) for d in recent_dates}


def _format_counter_top(counter, top_n=8):
    if not counter:
        return "(데이터 없음)"
    return ", ".join(f"{name}({count})" for name, count in counter.most_common(top_n))


def generate_market_report(today_str, today_themes, today_industries, recent_theme_history):
    """오늘의 섹터/테마 자금흐름 분석 리포트를 Claude(Sonnet, 웹서치)로 작성한다."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 설정되어 있지 않습니다.")

    client = anthropic.Anthropic(api_key=api_key)

    today_theme_block = _format_counter_top(today_themes, top_n=15)
    today_industry_block = _format_counter_top(today_industries, top_n=15)

    recent_history_lines = []
    for date_str in sorted(recent_theme_history.keys()):
        recent_history_lines.append(f"- {date_str}: {_format_counter_top(recent_theme_history[date_str], top_n=8)}")
    recent_history_block = "\n".join(recent_history_lines) if recent_history_lines else "(최근 이력 없음)"

    today_display = datetime.datetime.strptime(today_str, "%Y%m%d").strftime("%Y년 %m월 %d일")

    prompt = f"""당신은 한국 주식시장 자금흐름을 전문적으로 분석하는 고급 애널리스트입니다.
아래 데이터를 바탕으로, 정확하고 명확한 시장분석 리포트를 작성하세요.

[오늘({today_display}) 상한가/급등 종목의 테마 분포]
{today_theme_block}

[오늘 상한가/급등 종목의 업종 분포]
{today_industry_block}

[최근 거래일별 테마 분포 이력 (참고용)]
{recent_history_block}

웹 검색을 사용해서 아래 내용을 반드시 확인하고 반영하세요:
- 오늘 한국 코스피/코스닥 전체 지수 등락, 수급 주체별(외국인/기관/개인) 매매동향
- 오늘 미국 3대 지수(S&P500, 나스닥, 다우) 및 필라델피아 반도체지수 등 주요 지수 마감 결과
- 오늘 국제 유가, 달러/원 환율, 미국 국채금리 등 거시 지표 흐름
- 위 지표들이 오늘 한국 시장의 자금흐름과 어떤 관련이 있는지

리포트는 아래 구조로, 각 섹션마다 구체적 수치와 근거를 담아 작성하세요. 뭉뚱그린 표현
대신 실제 확인한 지수/수치를 인용하고, 확인 안 된 내용은 추측하지 말고 "확인 안 됨"이라고
명시하세요:

【오늘의 자금흐름 요약】
오늘 시장 전체 분위기(지수, 수급주체)와 자금이 몰린 핵심 섹터/테마 1~3개를 명확히 짚어서 설명

【최근 흐름 대비 변화】
최근 거래일 테마 분포와 비교해서, 오늘 새롭게 부각된 테마/식어가는 테마를 구체적으로 짚기
(예: "최근 3거래일 연속 상위였던 OO테마가 오늘은 순위 밖으로 밀려나고, 대신 XX테마가 급부상")

【글로벌 증시 연계 분석】
미국/글로벌 증시 및 거시지표가 오늘 한국 시장 자금흐름에 미친 영향을 구체적으로 설명

【다음 거래일 예상 자금흐름】
위 분석을 종합해서, 다음 거래일에 자금이 몰릴 가능성이 높은 섹터/테마를 2~3개 제시하고
그 근거를 명확히 설명. 확신도가 낮으면 그렇다고 명시(예: "가능성 있으나 확정적이지 않음")

전체 분량은 800~1200자 내외로, 애널리스트 리포트처럼 간결하고 명확하게 작성하세요."""

    max_retries = 3
    response = None
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=3000,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except anthropic.APIStatusError as e:
            if attempt < max_retries - 1 and e.status_code in (429, 500, 503, 529):
                time.sleep(2 ** (attempt + 1))
                continue
            raise

    report_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()

    if not report_text:
        report_text = "[리포트 생성 실패 - 응답이 비어있음]"

    return report_text