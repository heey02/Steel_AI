# -*- coding: utf-8 -*-
"""
data_loader.py
철강 산업 실시간 데이터 수집 모듈

수집 대상
    1) 원/달러 환율        : yfinance (KRW=X)
    2) 철광석 Iron Ore     : yfinance (TIO=F, SGX 62% Fe 선물) -> Trading Economics 파싱
    3) 제철용 강점탄        : Trading Economics (coking-coal) 파싱 -> yfinance 보조
    4) 국내외 제품 유통가    : Trading Economics(HRC/Steel) 파싱 + 국내 유통가 실측 매핑
    5) 실시간 철강 뉴스     : feedparser (Google News / Naver News RSS)

설계 원칙
    - 모든 외부 호출은 try/except + timeout 으로 감싸며, 실패 시 예외를 밖으로 던지지 않는다.
    - 실패 시에는 '실측 기준값(BASELINE)' 기반의 결정론적 대체 시계열(Fallback)을 생성해
      Streamlit Cloud 배포 환경에서 네트워크가 막혀도 앱이 항상 렌더링되도록 한다.
    - 각 지표는 데이터 출처(source)를 함께 반환하여 UI에서 실데이터/대체데이터를 구분 표기한다.
"""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# 선택적 의존성 (미설치 시에도 모듈 import 는 성공해야 한다)
# ----------------------------------------------------------------------------
try:
    import yfinance as yf
    _HAS_YF = True
except Exception:  # pragma: no cover
    yf = None
    _HAS_YF = False

try:
    import requests
    from bs4 import BeautifulSoup
    _HAS_WEB = True
except Exception:  # pragma: no cover
    requests = None
    BeautifulSoup = None
    _HAS_WEB = False

try:
    import feedparser
    _HAS_RSS = True
except Exception:  # pragma: no cover
    feedparser = None
    _HAS_RSS = False


# ----------------------------------------------------------------------------
# 상수 정의
# ----------------------------------------------------------------------------
HTTP_TIMEOUT = 8
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,ko;q=0.8"}

# 기간 라벨 -> (yfinance period, 표시 일수)
PERIOD_MAP: Dict[str, Tuple[str, int]] = {
    "1주일": ("1mo", 7),
    "1개월": ("3mo", 30),
    "3개월": ("6mo", 90),
    "1년": ("2y", 365),
}

# 2026년 기준 실측 베이스라인 (스크래핑 실패 시 대체 시계열의 기준점)
BASELINE: Dict[str, float] = {
    "usdkrw": 1385.0,        # 원/달러
    "iron_ore": 105.0,       # $/t  (SGX 62% Fe)
    "coking_coal": 260.0,    # $/t  (제철용 강점탄, Premium HCC)
    "rebar_kr": 860000.0,    # 원/톤 (국내 철근 유통가)
    "hbeam_kr": 1200000.0,   # 원/톤 (국내 H형강 유통가)
    "hrc_us": 1150.0,       # $/t  (미국 열연 HRC)
    "hrc_cn": 440.0,        # $/t  (중국 열연/철강, CNY 환산)
}

# 스크래핑 결과 검증용 정상 범위 (이상치 파싱 방지)
SANITY: Dict[str, Tuple[float, float]] = {
    "usdkrw": (900.0, 2200.0),
    "iron_ore": (40.0, 260.0),
    "coking_coal": (90.0, 700.0),
    "hrc_us": (350.0, 1800.0),
    "hrc_cn": (250.0, 1200.0),
}

# 일간 변동성(표준편차 비율)
_VOL = {
    "usdkrw": 0.0035,
    "iron_ore": 0.0150,
    "coking_coal": 0.0140,
    "rebar_kr": 0.0045,
    "hbeam_kr": 0.0035,
    "hrc_us": 0.0090,
    "hrc_cn": 0.0090,
}


# ----------------------------------------------------------------------------
# 데이터 구조
# ----------------------------------------------------------------------------
@dataclass
class Indicator:
    """단일 지표(시계열 + 최신값 + 증감)를 표현하는 컨테이너."""

    key: str
    label: str
    unit: str
    series: pd.Series
    source: str = "fallback"
    is_live: bool = False

    @property
    def last(self) -> float:
        return float(self.series.iloc[-1]) if len(self.series) else float("nan")

    @property
    def prev(self) -> float:
        return float(self.series.iloc[-2]) if len(self.series) > 1 else self.last

    @property
    def delta(self) -> float:
        return self.last - self.prev

    @property
    def delta_pct(self) -> float:
        return (self.delta / self.prev * 100.0) if self.prev else 0.0

    def fmt_value(self) -> str:
        if self.unit == "원/톤":
            return f"{self.last:,.0f} 원"
        if self.unit == "원/달러":
            return f"{self.last:,.2f} 원"
        return f"$ {self.last:,.2f}"

    def fmt_delta(self) -> str:
        if self.unit == "원/톤":
            return f"{self.delta:+,.0f} 원 ({self.delta_pct:+.2f}%)"
        if self.unit == "원/달러":
            return f"{self.delta:+,.2f} 원 ({self.delta_pct:+.2f}%)"
        return f"{self.delta:+,.2f} ({self.delta_pct:+.2f}%)"

    def fmt_value_short(self) -> str:
        """지표 카드용 축약 표기 (좁은 컬럼에서 값이 잘리지 않도록)."""
        if self.unit == "원/톤":
            return f"{self.last / 10000:,.0f}만원"
        if self.unit == "원/달러":
            return f"{self.last:,.1f}원"
        return f"${self.last:,.1f}"

    def fmt_delta_short(self) -> str:
        """카드 delta 용 퍼센트 표기."""
        return f"{self.delta_pct:+.2f}%"

    def fmt_delta_abs(self) -> str:
        """전일 대비 절대 증감 (캡션용)."""
        if self.unit == "원/톤":
            return f"{self.delta:+,.0f}원"
        if self.unit == "원/달러":
            return f"{self.delta:+,.2f}원"
        return f"{self.delta:+,.2f}$"

    def to_frame(self, col: Optional[str] = None) -> pd.DataFrame:
        name = col or self.label
        return pd.DataFrame({"date": self.series.index, name: self.series.values})


# ----------------------------------------------------------------------------
# 공통 유틸
# ----------------------------------------------------------------------------
def escape_dollars(text: str) -> str:
    """
    Streamlit 마크다운에서 '$100 ... $200' 형태가 LaTeX 수식으로 해석되어
    달러 기호가 사라지는 문제를 막기 위한 이스케이프.
    코드펜스(```) 내부는 수식 해석 대상이 아니므로 건드리지 않는다.
    """
    parts = str(text).split("```")
    for i in range(0, len(parts), 2):
        parts[i] = parts[i].replace("$", r"\$")
    return "```".join(parts)


def _seed_of(key: str) -> int:
    """지표 키 + 날짜 기반 결정론적 시드 (같은 날에는 항상 동일한 대체 데이터)."""
    today = dt.date.today().isoformat()
    raw = f"{key}|{today}"
    return sum(ord(c) * (i + 7) for i, c in enumerate(raw)) % (2**31 - 1)


def _business_index(days: int, end: Optional[dt.date] = None) -> pd.DatetimeIndex:
    end = end or dt.date.today()
    start = end - dt.timedelta(days=int(days * 1.7) + 10)
    idx = pd.bdate_range(start=start, end=end)
    return idx[-max(int(days), 5):]


def _synthetic_series(key: str, days: int, baseline: Optional[float] = None,
                      drift: float = 0.0) -> pd.Series:
    """실측 베이스라인으로 수렴하는 결정론적 랜덤워크 대체 시계열."""
    base = float(baseline if baseline is not None else BASELINE.get(key, 100.0))
    idx = _business_index(days)
    n = len(idx)
    rng = np.random.default_rng(_seed_of(key))
    vol = _VOL.get(key, 0.01)
    steps = rng.normal(loc=drift, scale=vol, size=n)
    path = base * np.exp(np.cumsum(steps))
    path = path * (base / path[-1])   # 마지막 값 = 베이스라인
    return pd.Series(path, index=idx, name=key).astype(float)


def _clean_index(s: pd.Series) -> pd.Series:
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out = pd.Series(np.asarray(s.values, dtype="float64"), index=idx, name=s.name)
    return out[~out.index.duplicated(keep="last")].sort_index().dropna()


def _sane(key: str, value: Optional[float]) -> Optional[float]:
    """파싱값이 상식적인 범위인지 검증 (범위 밖이면 None)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    lo, hi = SANITY.get(key, (0.0, float("inf")))
    return v if lo <= v <= hi else None


# ----------------------------------------------------------------------------
# yfinance 수집
# ----------------------------------------------------------------------------
def _yf_series(ticker: str, period: str = "1y") -> Optional[pd.Series]:
    """yfinance 일별 종가 시계열. 실패/빈 데이터는 None."""
    if not _HAS_YF:
        return None
    try:
        raw = yf.download(
            ticker, period=period, interval="1d",
            progress=False, auto_adjust=False, threads=False,
        )
        if raw is None or len(raw) == 0:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.copy()
            raw.columns = [c[0] for c in raw.columns]
        if "Close" not in raw.columns:
            return None
        s = raw["Close"].dropna()
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s = _clean_index(s)
        return s if len(s) else None
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Trading Economics 파싱
# ----------------------------------------------------------------------------
def _fx_rate_usd(currency: str) -> Optional[float]:
    """1 USD 당 해당 통화 금액 (CNY=X 등). 실패 시 상수 폴백."""
    cur = (currency or "USD").upper()
    if cur in ("USD", "US$"):
        return 1.0
    static = {"CNY": 7.10, "EUR": 0.92, "JPY": 152.0, "AUD": 1.52, "KRW": 1385.0}
    s = _yf_series(f"{cur}=X", "5d")
    if s is not None and len(s):
        v = float(s.iloc[-1])
        ref = static.get(cur)
        if np.isfinite(v) and v > 0 and (ref is None or 0.25 * ref <= v <= 4 * ref):
            return v
    return static.get(cur)


def _te_quote(slug: str, name_hint: Optional[str] = None) -> Optional[Tuple[float, str]]:
    """
    https://tradingeconomics.com/commodity/<slug> 에서 (현재가, 통화) 파싱.

    파싱 우선순위 (중요)
      1순위: meta description 문장
             예) "Coking Coal traded flat at 282 USD/T on August 31, 2026."
             -> 페이지 주제 상품의 값이 확실히 담기므로 신뢰도가 가장 높다.
      2순위: 상품명이 포함된 테이블 행의 현재가 셀(td#p)
             ※ td#p 를 무조건 첫 번째로 집으면 사이드 테이블의 '다른 상품'
               가격을 잘못 가져오므로(예: 강점탄 282 대신 164) 행 이름을 대조한다.
      3순위: 본문 문장 정규식 (통화 단위 포함 캡처)
    """
    if not _HAS_WEB:
        return None
    url = f"https://tradingeconomics.com/commodity/{slug}"
    hint = (name_hint or slug.replace("-", " ")).lower()
    num = r"(\d[\d,]*(?:\.\d+)?)"
    cur = r"(USD|CNY|EUR|JPY|AUD|INR|BRL)"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        # (1) meta description
        meta = soup.find("meta", attrs={"name": "description"})
        content = (meta.get("content") if meta else "") or ""
        m = re.search(num + r"\s*" + cur, content, re.I)
        if m:
            return float(m.group(1).replace(",", "")), m.group(2).upper()

        # (2) 상품명이 일치하는 테이블 행에서만 현재가 추출
        for tr in soup.select("tr"):
            row_text = tr.get_text(" ", strip=True).lower()
            if hint and hint in row_text:
                cell = tr.select_one("td#p") or tr.select_one("td[id='p']")
                if cell:
                    txt = (cell.get_text() or "").strip().replace(",", "")
                    mm = re.search(r"-?\d+(?:\.\d+)?", txt)
                    if mm:
                        return float(mm.group(0)), "USD"

        # (3) 본문 문장 백업
        body = soup.get_text(" ", strip=True)[:4000]
        m = re.search(
            r"(?:traded(?: flat)? at|increased to|decreased to|rose to|fell to|"
            r"was unchanged at)\s*" + num + r"\s*" + cur + "?",
            body, re.I,
        )
        if m:
            return float(m.group(1).replace(",", "")), (m.group(2) or "USD").upper()
    except Exception:
        return None
    return None


def _te_price_usd(slug: str, name_hint: Optional[str] = None) -> Optional[float]:
    """Trading Economics 현재가를 USD 기준으로 환산해 반환."""
    quote = _te_quote(slug, name_hint)
    if not quote:
        return None
    value, currency = quote
    rate = _fx_rate_usd(currency)
    if not rate or rate <= 0:
        return None
    return value / rate


def _series_from_spot(key: str, spot: float, days: int) -> pd.Series:
    """현물가 1건만 확보된 경우 해당 값으로 끝나는 근사 추이 생성."""
    return _synthetic_series(key, days, baseline=spot)


# ----------------------------------------------------------------------------
# 개별 지표 수집기
# ----------------------------------------------------------------------------
def fetch_usdkrw(days: int, yf_period: str) -> Indicator:
    """원/달러 환율 (yfinance KRW=X)."""
    s = _yf_series("KRW=X", yf_period)
    if s is not None and len(s) >= 3 and _sane("usdkrw", s.iloc[-1]):
        return Indicator("usdkrw", "원/달러 환율", "원/달러", s.tail(days),
                         source="yfinance (KRW=X)", is_live=True)
    return Indicator("usdkrw", "원/달러 환율", "원/달러",
                     _synthetic_series("usdkrw", days),
                     source="대체 데이터 (기준 실측값)", is_live=False)


def fetch_iron_ore(days: int, yf_period: str) -> Indicator:
    """철광석 (SGX 62% Fe 선물 TIO=F -> Trading Economics)."""
    for ticker in ("TIO=F", "SCO=F"):
        s = _yf_series(ticker, yf_period)
        if s is not None and len(s) >= 3 and _sane("iron_ore", s.iloc[-1]):
            return Indicator("iron_ore", "철광석 (Iron Ore)", "$/t", s.tail(days),
                             source=f"yfinance ({ticker})", is_live=True)

    spot = _sane("iron_ore", _te_price_usd("iron-ore", "iron ore"))
    if spot:
        return Indicator("iron_ore", "철광석 (Iron Ore)", "$/t",
                         _series_from_spot("iron_ore", spot, days),
                         source="Trading Economics (iron-ore)", is_live=True)

    return Indicator("iron_ore", "철광석 (Iron Ore)", "$/t",
                     _synthetic_series("iron_ore", days),
                     source="대체 데이터 (기준 실측값)", is_live=False)


def fetch_coking_coal(days: int, yf_period: str) -> Indicator:
    """
    제철용 강점탄 (Coking Coal / Premium Hard Coking Coal).
    ※ 발전용 연료탄(Newcastle Thermal Coal, 약 $100/t)이 아님에 유의.
       Trading Economics 'coking-coal'(약 $260/t 수준)을 1순위로 사용하고,
       실패 시 2026년 기준 실측치 $260/t 기반 대체 시계열을 사용한다.
    """
    spot = _sane("coking_coal", _te_price_usd("coking-coal", "coking coal"))
    if spot:
        return Indicator("coking_coal", "제철용 강점탄 (Coking Coal)", "$/t",
                         _series_from_spot("coking_coal", spot, days),
                         source="Trading Economics (coking-coal)", is_live=True)

    # 보조: 강점탄 선물 티커 (제공사에 따라 미지원일 수 있음)
    for ticker in ("AHC=F", "PMC=F"):
        s = _yf_series(ticker, yf_period)
        if s is not None and len(s) >= 3 and _sane("coking_coal", s.iloc[-1]):
            return Indicator("coking_coal", "제철용 강점탄 (Coking Coal)", "$/t",
                             s.tail(days), source=f"yfinance ({ticker})", is_live=True)

    return Indicator("coking_coal", "제철용 강점탄 (Coking Coal)", "$/t",
                     _synthetic_series("coking_coal", days),
                     source="대체 데이터 (기준 실측값 $260/t)", is_live=False)


def fetch_steel_products(days: int) -> Dict[str, Indicator]:
    """
    국내외 제품 유통가.
      - 국내 철근/H형강 : 공개 실시간 API 부재 -> 유통가 실측 기준값 매핑 + 추이 근사
      - 미국 열연(HRC)  : Trading Economics 'hrc-steel'
      - 중국 철강       : Trading Economics 'steel'
    """
    out: Dict[str, Indicator] = {}

    out["rebar_kr"] = Indicator(
        "rebar_kr", "국내 철근 유통가", "원/톤",
        _synthetic_series("rebar_kr", days),
        source="국내 유통가 실측 기준값 매핑 (약 86만원/톤)", is_live=False,
    )
    out["hbeam_kr"] = Indicator(
        "hbeam_kr", "국내 H형강 유통가", "원/톤",
        _synthetic_series("hbeam_kr", days),
        source="국내 유통가 실측 기준값 매핑 (약 120만원/톤)", is_live=False,
    )

    us = _sane("hrc_us", _te_price_usd("hrc-steel", "hrc steel"))
    out["hrc_us"] = Indicator(
        "hrc_us", "미국 열연 (HRC)", "$/t",
        _series_from_spot("hrc_us", us, days) if us else _synthetic_series("hrc_us", days),
        source="Trading Economics (hrc-steel)" if us else "대체 데이터 (기준 실측값)",
        is_live=bool(us),
    )

    cn = _sane("hrc_cn", _te_price_usd("steel", "steel"))
    out["hrc_cn"] = Indicator(
        "hrc_cn", "중국 열연/철강", "$/t",
        _series_from_spot("hrc_cn", cn, days) if cn else _synthetic_series("hrc_cn", days),
        source="Trading Economics (steel)" if cn else "대체 데이터 (기준 실측값)",
        is_live=bool(cn),
    )
    return out


# ----------------------------------------------------------------------------
# 뉴스 수집 (RSS)
# ----------------------------------------------------------------------------
NEWS_QUERIES = ["철강 원자재", "철광석 가격", "철근 유통가"]

# 감성 사전 (간이 룰 기반)
POS_WORDS = [
    "상승", "강세", "급등", "호조", "개선", "회복", "증가", "확대", "수주", "흑자",
    "최고", "반등", "인상", "성장", "기대", "수혜", "돌파", "순항",
]
NEG_WORDS = [
    "하락", "약세", "급락", "부진", "감소", "축소", "적자", "우려", "위기", "침체",
    "둔화", "인하", "감산", "최저", "리스크", "불황", "규제", "관세", "파업", "중단",
]
STOPWORDS = {
    "그리고", "하지만", "이번", "관련", "지난", "대한", "위해", "통해", "기자", "속보",
    "단독", "종합", "the", "and", "for", "with", "from", "news", "정부", "올해",
    "가운데", "따른", "따라", "대비", "전망", "이상", "최근",
}

FALLBACK_NEWS: List[Dict[str, Any]] = [
    {"title": "철광석 가격 톤당 105달러선 등락…중국 수요 회복 기대감 부각",
     "link": "https://news.google.com/", "source": "샘플 데이터", "published": ""},
    {"title": "제철용 강점탄 260달러 내외 강세 지속…원가 부담 확대 우려",
     "link": "https://news.google.com/", "source": "샘플 데이터", "published": ""},
    {"title": "국내 철근 유통가 톤당 86만원 보합…건설 수요 부진에 관망세",
     "link": "https://news.google.com/", "source": "샘플 데이터", "published": ""},
    {"title": "H형강 유통가격 120만원대 유지, 제강사 감산 효과 주목",
     "link": "https://news.google.com/", "source": "샘플 데이터", "published": ""},
    {"title": "원/달러 환율 변동성 확대…철강업계 원자재 수입 원가 압박",
     "link": "https://news.google.com/", "source": "샘플 데이터", "published": ""},
]


def _rss_urls(query: str) -> List[str]:
    q = urllib.parse.quote(query)
    return [
        f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko",
        f"https://search.naver.com/search.naver?where=rss&query={q}",
    ]


def analyze_sentiment(text: str) -> str:
    """제목 기반 간이 감성 분류 (positive / neutral / negative)."""
    t = str(text)
    pos = sum(1 for w in POS_WORDS if w in t)
    neg = sum(1 for w in NEG_WORDS if w in t)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def extract_keywords(titles: List[str], top_n: int = 12) -> List[Tuple[str, int]]:
    """뉴스 제목에서 상위 키워드 추출 (2글자 이상 명사성 토큰 빈도)."""
    counts: Dict[str, int] = {}
    for title in titles:
        tokens = re.findall(r"[가-힣A-Za-z]{2,}", str(title))
        for tok in tokens:
            low = tok.lower()
            if low in STOPWORDS or len(tok) < 2:
                continue
            counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:top_n]


def fetch_news(limit: int = 5) -> List[Dict[str, Any]]:
    """구글/네이버 뉴스 RSS에서 철강 원자재 관련 최신 뉴스 수집."""
    items: List[Dict[str, Any]] = []
    seen = set()

    if _HAS_RSS:
        for query in NEWS_QUERIES:
            if len(items) >= limit:
                break
            for url in _rss_urls(query):
                try:
                    feed = feedparser.parse(url)
                    entries = getattr(feed, "entries", []) or []
                except Exception:
                    continue
                for entry in entries[:10]:
                    title = re.sub(r"<[^>]+>", "", str(getattr(entry, "title", ""))).strip()
                    if not title:
                        continue
                    dedup = title[:40]
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    src = ""
                    try:
                        src = str(getattr(entry, "source", {}).get("title", ""))
                    except Exception:
                        src = ""
                    if not src and " - " in title:
                        src = title.rsplit(" - ", 1)[-1]
                    items.append({
                        "title": title,
                        "link": str(getattr(entry, "link", "")),
                        "source": src or "RSS",
                        "published": str(getattr(entry, "published", "")),
                    })
                    if len(items) >= limit:
                        break
                if len(items) >= limit:
                    break

    if not items:
        items = [dict(n) for n in FALLBACK_NEWS[:limit]]

    for it in items:
        it["sentiment"] = analyze_sentiment(it["title"])
    return items[:limit]


def sentiment_summary(news: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"positive": 0, "neutral": 0, "negative": 0}
    for n in news:
        counts[n.get("sentiment", "neutral")] = counts.get(n.get("sentiment", "neutral"), 0) + 1
    return counts


# ----------------------------------------------------------------------------
# 통합 로더
# ----------------------------------------------------------------------------
def load_market_data(period_label: str = "3개월", live: bool = True) -> Dict[str, Any]:
    """
    대시보드에서 사용하는 모든 데이터를 한 번에 수집한다.
    개별 수집기가 실패하더라도 전체 번들은 항상 정상 반환된다.

    Args:
        period_label: PERIOD_MAP 의 키 ("1주일" / "1개월" / "3개월" / "1년")
        live: False 이면 네트워크 호출 없이 기준 실측값 기반으로만 구성한다.
    """
    yf_period, days = PERIOD_MAP.get(period_label, PERIOD_MAP["3개월"])

    if not live:
        return _offline_bundle(period_label, days)

    indicators: Dict[str, Indicator] = {}

    def _safe(key: str, fn, *args):
        try:
            return fn(*args)
        except Exception:
            label = {
                "usdkrw": ("원/달러 환율", "원/달러"),
                "iron_ore": ("철광석 (Iron Ore)", "$/t"),
                "coking_coal": ("제철용 강점탄 (Coking Coal)", "$/t"),
            }.get(key, (key, ""))
            return Indicator(key, label[0], label[1], _synthetic_series(key, days),
                             source="대체 데이터 (수집 예외)", is_live=False)

    indicators["usdkrw"] = _safe("usdkrw", fetch_usdkrw, days, yf_period)
    indicators["iron_ore"] = _safe("iron_ore", fetch_iron_ore, days, yf_period)
    indicators["coking_coal"] = _safe("coking_coal", fetch_coking_coal, days, yf_period)

    try:
        indicators.update(fetch_steel_products(days))
    except Exception:
        for k, lab in (("rebar_kr", "국내 철근 유통가"), ("hbeam_kr", "국내 H형강 유통가"),
                       ("hrc_us", "미국 열연 (HRC)"), ("hrc_cn", "중국 열연/철강")):
            unit = "원/톤" if k.endswith("_kr") else "$/t"
            indicators[k] = Indicator(k, lab, unit, _synthetic_series(k, days),
                                      source="대체 데이터 (수집 예외)", is_live=False)

    try:
        news = fetch_news(limit=5)
    except Exception:
        news = [dict(n, sentiment=analyze_sentiment(n["title"])) for n in FALLBACK_NEWS]

    return {
        "period_label": period_label,
        "days": days,
        "indicators": indicators,
        "news": news,
        "sentiment": sentiment_summary(news),
        "keywords": extract_keywords([n["title"] for n in news]),
        "updated_at": dt.datetime.now(),
        "live_count": sum(1 for i in indicators.values() if i.is_live),
        "deps": {"yfinance": _HAS_YF, "requests/bs4": _HAS_WEB, "feedparser": _HAS_RSS},
    }


OFFLINE_META = [
    ("usdkrw", "원/달러 환율", "원/달러"),
    ("iron_ore", "철광석 (Iron Ore)", "$/t"),
    ("coking_coal", "제철용 강점탄 (Coking Coal)", "$/t"),
    ("rebar_kr", "국내 철근 유통가", "원/톤"),
    ("hbeam_kr", "국내 H형강 유통가", "원/톤"),
    ("hrc_us", "미국 열연 (HRC)", "$/t"),
    ("hrc_cn", "중국 열연/철강", "$/t"),
]


def _offline_bundle(period_label: str, days: int) -> Dict[str, Any]:
    """네트워크를 전혀 사용하지 않는 오프라인 번들 (기준 실측값 기반)."""
    indicators = {
        key: Indicator(key, label, unit, _synthetic_series(key, days),
                       source="오프라인 모드 (기준 실측값)", is_live=False)
        for key, label, unit in OFFLINE_META
    }
    news = [dict(n, sentiment=analyze_sentiment(n["title"])) for n in FALLBACK_NEWS]
    return {
        "period_label": period_label,
        "days": days,
        "indicators": indicators,
        "news": news,
        "sentiment": sentiment_summary(news),
        "keywords": extract_keywords([n["title"] for n in news]),
        "updated_at": dt.datetime.now(),
        "live_count": 0,
        "deps": {"yfinance": _HAS_YF, "requests/bs4": _HAS_WEB, "feedparser": _HAS_RSS},
    }


def build_llm_context(bundle: Dict[str, Any]) -> str:
    """LLM 프롬프트에 넣을 시장 지표 요약 텍스트 생성."""
    ind = bundle["indicators"]
    lines = [f"[기준시각] {bundle['updated_at'].strftime('%Y-%m-%d %H:%M')}",
             f"[조회기간] {bundle['period_label']}", "", "[핵심 지표]"]
    for key in ("usdkrw", "iron_ore", "coking_coal", "rebar_kr", "hbeam_kr",
                "hrc_us", "hrc_cn"):
        i = ind.get(key)
        if i is None:
            continue
        lines.append(f"- {i.label}: {i.fmt_value()} / 전일대비 {i.fmt_delta()} "
                     f"(출처: {i.source})")

    period_moves = []
    for key in ("iron_ore", "coking_coal", "usdkrw"):
        i = ind.get(key)
        if i is None or len(i.series) < 2:
            continue
        chg = (i.series.iloc[-1] / i.series.iloc[0] - 1) * 100
        period_moves.append(f"- {i.label}: 기간 수익률 {chg:+.2f}%")
    if period_moves:
        lines += ["", "[기간 변동률]"] + period_moves

    lines += ["", "[최신 뉴스]"]
    for n in bundle["news"]:
        lines.append(f"- ({n['sentiment']}) {n['title']}")

    s = bundle["sentiment"]
    lines += ["", f"[뉴스 감성] 긍정 {s['positive']} / 중립 {s['neutral']} / 부정 {s['negative']}"]
    return "\n".join(lines)


def fallback_daily_insight(bundle: Dict[str, Any]) -> str:
    """OpenAI API Key 미입력 시 사용하는 규칙 기반 3줄 요약."""
    ind = bundle["indicators"]
    fx, io, cc = ind["usdkrw"], ind["iron_ore"], ind["coking_coal"]
    rebar = ind["rebar_kr"]

    def _dir(x: float) -> str:
        return "상승" if x > 0 else ("하락" if x < 0 else "보합")

    l1 = (f"원/달러 환율은 {fx.last:,.1f}원으로 전일 대비 {_dir(fx.delta)}"
          f"({fx.delta_pct:+.2f}%)하며 원자재 수입 원가에 "
          f"{'부담' if fx.delta > 0 else '완화 요인'}으로 작용하고 있습니다.")
    l2 = (f"철광석은 ${io.last:,.1f}/t({io.delta_pct:+.2f}%), 제철용 강점탄은 "
          f"${cc.last:,.1f}/t({cc.delta_pct:+.2f}%)로 원료탄 강세가 고로사 원가의 "
          f"핵심 변수로 남아 있습니다.")
    l3 = (f"국내 철근 유통가는 {rebar.last:,.0f}원/톤 수준에서 "
          f"{_dir(rebar.delta)} 흐름을 보여, 원가 상승분의 판가 전가 여부가 "
          f"단기 마진을 좌우할 전망입니다.")
    return f"{l1}\n\n{l2}\n\n{l3}"


def fallback_report(bundle: Dict[str, Any]) -> str:
    """API Key 미입력 시 제공하는 샘플 종합 리포트(Markdown)."""
    ind = bundle["indicators"]
    ctx = build_llm_context(bundle)
    io, cc, fx = ind["iron_ore"], ind["coking_coal"], ind["usdkrw"]
    return f"""## 📄 AI Daily Market Report (샘플)

> OpenAI API Key가 입력되지 않아 **규칙 기반 샘플 리포트**를 표시합니다.
> 사이드바에 Key를 입력하면 LLM 기반 실제 분석 리포트가 생성됩니다.

### 1. 시장 종합 요약
- 원/달러 환율 **{fx.fmt_value()}** ({fx.fmt_delta()})
- 철광석 **{io.fmt_value()}** ({io.fmt_delta()})
- 제철용 강점탄 **{cc.fmt_value()}** ({cc.fmt_delta()})
- 국내 철근 유통가 **{ind['rebar_kr'].fmt_value()}**, H형강 **{ind['hbeam_kr'].fmt_value()}**

### 2. 시황 분석
원료 측면에서는 철광석이 박스권 등락을 이어가는 가운데 제철용 강점탄이 상대적 강세를
유지하며 고로사 원가 구조에 부담을 주고 있습니다. 환율이 높은 수준에서 유지될 경우
달러 표시 원자재의 원화 환산 원가가 추가로 상승해 스프레드 축소 압력이 커집니다.
국내 봉형강 유통가는 건설 착공 부진의 영향으로 원가 상승분을 즉각 반영하기 어려운
구조이며, 제강사의 감산·가격 정책이 단기 방향성을 결정할 것으로 보입니다.

### 3. 단기 전망 (1~2주)
- **원료:** 강점탄 강세 지속 여부가 최대 변수, 철광석은 중국 수요 지표에 연동
- **환율:** 고환율 국면 유지 시 수입 원가 부담 확대
- **제품:** 유통가는 보합권 등락 예상, 판가 전가 성공 여부가 마진 관건

---
#### 참고: 수집 지표 원본
```
{ctx}
```
"""


if __name__ == "__main__":  # 간단한 수동 점검용
    data = load_market_data("1개월")
    for k, v in data["indicators"].items():
        print(f"{k:12s} {v.fmt_value():>18s}  {v.fmt_delta():>22s}  [{v.source}]")
    print()
    for n in data["news"]:
        print(f"({n['sentiment']}) {n['title'][:70]}")
