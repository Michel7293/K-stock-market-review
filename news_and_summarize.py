# -*- coding: utf-8 -*-
"""
종목별 관련 뉴스를 가져와서 Claude API(웹서치 + 구조화된 도구 호출)로
"상승 키워드 3개 / 당일 이슈 / 테마"를 자동으로 요약하는 스크립트.
"""

import os
import time
import datetime
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

NEWS_URL = "https://finance.naver.com/item/news_news.naver"


def fetch_stock_news(code, max_articles=5):
    resp = requests.get(
        NEWS_URL,
        headers={
            **HEADERS,
            "Referer": f"https://finance.naver.com/item/main.naver?code={code}",
        },
        params={
            "code": code,
            "page": 1,
            "sm": "title_entity_id.basic",
            "clusterId": "",
        },
        timeout=10,
    )
    resp.encoding = "euc-kr"
    soup = BeautifulSoup(resp.text, "lxml")

    titles = []
    table = soup.select_one("table.type5")
    if table is None:
        links = soup.select("a[href*='news_read.naver']")
    else:
        rows = table.select("tbody tr")
        links = []
        for row in rows:
            first_td = row.select_one("td")
            if first_td is None:
                continue
            a = first_td.select_one("a")
            if a is not None:
                links.append(a)

    for link in links:
        title = link.get_text(strip=True)
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= max_articles:
            break

    return titles


def summarize_with_claude(stock_name, news_titles, model="claude-sonnet-5"):
    """뉴스 제목 + 실시간 웹서치를 바탕으로 상승 키워드 3개 / 당일 이슈 / 테마를 추출한다."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY 환경변수가 설정되어 있지 않습니다. "
            "터미널에서 set ANTHROPIC_API_KEY=sk-ant-... 로 설정 후 다시 실행하세요."
        )

    client = anthropic.Anthropic(api_key=api_key)

    news_block = "\n".join(f"- {t}" for t in news_titles) if news_titles else "(네이버 뉴스 제목 없음)"
    today_str = datetime.date.today().strftime("%Y년 %m월 %d일")

    prompt = f"""오늘({today_str}) 한국 주식시장에서 '{stock_name}' 종목이 급등했습니다.
참고용 네이버 뉴스 제목:
{news_block}

당신은 한국 자본시장 전문 애널리스트입니다. 일반적인 실적/신제품 뉴스뿐 아니라,
아래와 같은 **자본시장 실질 수급 이벤트**까지 반드시 점검하세요. 이런 이벤트는
포털 뉴스 제목에는 잘 안 나오고 전자공시(DART)나 거래소 공시에만 나오는 경우가
많으니, 웹 검색 시 "{stock_name} 공시", "{stock_name} DART", "{stock_name} 전자공시"
같은 검색어도 함께 사용해서 다음 항목들을 확인하세요:

- 주식병합/액면분할/감자
- 거래정지 해제 및 거래재개 (매매거래정지 사유 해소)
- 관리종목 지정/해제, 상장폐지 사유 해소
- 유상증자/무상증자, 전환사채(CB)·신주인수권부사채(BW) 발행 또는 전환청구
- 최대주주 변경, 경영권 분쟁, 지분 매입/매각 공시
- 자사주 매입/소각 결정
- M&A, 합병, 영업양수도, 스팩(SPAC) 합병
- 공매도 잔고 급감(숏커버링), 대차거래 상환
- 신규 수주/계약 공시, 정책 수혜(정부 정책·법안 통과)
- 실적 발표(어닝서프라이즈) 및 컨센서스 대비 상회/하회

일반적인 "실적 개선", "신규 계약" 같은 뭉뚱그린 표현보다, 위 체크리스트 중 실제로
확인된 구체적 이벤트(예: "7월 31일 무상증자 신주 상장", "거래재개 첫날 매수세 집중",
"전환사채 콜옵션 행사로 최대주주 지분 확대")를 정확히 짚어서 설명하세요.

조사가 끝나면 반드시 submit_analysis 도구를 호출해서 최종 답변을 제출하세요. 이때:
- keywords: 방금 조사한 상승 이유를 압축한 짧은 명사구 3개 (예: "무상증자", "거래재개",
  "숏커버링", "실적개선", "정책수혜" 등 위 체크리스트 용어를 최대한 활용).
  절대 null이나 빈 문자열을 넣지 말고, 이유를 못 찾았어도 "수급쏠림", "테마동조" 같은 최소한의
  추정 키워드라도 반드시 3개 채우세요.
- theme: 이 종목이 속하는 시장 테마를 한 단어로 (예: "AI반도체", "방산", "2차전지", "제약바이오").
  절대 비워두지 말고, 애매하면 업종명을 그대로 테마로 써도 됩니다.
- issue: 오늘 상승한 구체적인 이유. 위 체크리스트에서 확인된 자본시장 이벤트가 있다면
  그것을 최우선으로 명시하고, 없다면 실적/계약/정책 등 일반 이슈를, 그것도 없으면
  "뚜렷한 공시 없음 - 수급/테마 동조 추정"처럼 구체적으로 적으세요.
  "이슈 확인 필요" 같은 무의미한 답은 피하세요."""

    submit_tool = {
        "name": "submit_analysis",
        "description": "조사한 내용을 바탕으로 최종 분석 결과를 제출한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "상승 이유를 나타내는 짧은 키워드 정확히 3개",
                },
                "issue": {
                    "type": "string",
                    "description": "오늘 이 종목이 상승한 구체적인 이유",
                },
                "theme": {
                    "type": "string",
                    "description": "이 종목이 속하는 시장 테마명 (예: AI반도체, 방산, 2차전지 등)",
                },
            },
            "required": ["keywords", "issue", "theme"],
        },
    }

    max_retries = 3
    response = None
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                tools=[
                    {"type": "web_search_20250305", "name": "web_search"},
                    submit_tool,
                ],
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except anthropic.APIStatusError as e:
            if attempt < max_retries - 1 and e.status_code in (429, 500, 503, 529):
                time.sleep(2 ** (attempt + 1))
                continue
            raise

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_analysis":
            result = dict(block.input)

            raw_keywords = result.get("keywords") or []
            keywords = [k for k in raw_keywords if isinstance(k, str) and k.strip()][:3]
            while len(keywords) < 3:
                keywords.append("테마동조")
            result["keywords"] = keywords

            if not (isinstance(result.get("theme"), str) and result["theme"].strip()):
                result["theme"] = "미분류"

            if not (isinstance(result.get("issue"), str) and result["issue"].strip()):
                result["issue"] = "뚜렷한 공시 없음 - 수급/테마 동조 추정"

            return result

    fallback_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    return {
        "keywords": [],
        "issue": f"[도구 미호출] {fallback_text[:200]}" if fallback_text else "분석 실패",
        "theme": None,
    }


if __name__ == "__main__":
    test_code = "000660"
    test_name = "SK하이닉스"

    print(f"=== {test_name}({test_code}) 뉴스 크롤링 테스트 ===")
    try:
        news = fetch_stock_news(test_code, max_articles=5)
        print(f"뉴스 {len(news)}건:")
        for t in news:
            print(" -", t)
    except Exception as e:
        print(f"[에러] 뉴스 크롤링 실패: {e}")
        news = []

    print()
    print("=== Claude API 요약 테스트 ===")
    try:
        summary = summarize_with_claude(test_name, news)
        print(summary)
    except Exception as e:
        print(f"[에러] 요약 실패: {e}")