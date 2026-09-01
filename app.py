# -*- coding: utf-8 -*-
"""
app.py
🏗️ Steel Market Intelligence — 철강 산업 실시간 데이터 & AI 인사이트 대시보드

실행:  streamlit run app.py
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import streamlit as st

import plotly.graph_objects as go
from plotly.subplots import make_subplots

import data_loader as dl

# ----------------------------------------------------------------------------
# 페이지 기본 설정
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="Steel Market Intelligence",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# LLM 모델: PRD 지정 모델을 기본값으로 두되, 계정에서 사용 불가한 경우
# 아래 후보 모델로 자동 재시도하여 앱이 절대 죽지 않도록 한다.
DEFAULT_MODEL = "gpt-5.6-sol"
FALLBACK_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]

COLORS = {
    "iron": "#E8743B",
    "coal": "#4C4C4C",
    "fx": "#1F77B4",
    "rebar": "#2CA02C",
    "hbeam": "#9467BD",
    "pos": "#2E9E5B",
    "neu": "#9AA0A6",
    "neg": "#D64545",
}

CUSTOM_CSS = """
<style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem;}
    div[data-testid="stMetric"] {
        background: rgba(128,128,128,0.07);
        border: 1px solid rgba(128,128,128,0.20);
        border-radius: 12px;
        padding: 14px 16px 10px 16px;
    }
    div[data-testid="stMetricLabel"] p {font-size: 0.86rem; font-weight: 600;}
    .src-tag {font-size: 0.72rem; opacity: 0.65; margin: -6px 0 12px 4px;}
    .kw-tag {
        display:inline-block; padding:5px 12px; margin:4px 6px 4px 0;
        border-radius:999px; background:rgba(31,119,180,0.12);
        border:1px solid rgba(31,119,180,0.35); font-size:0.84rem;
    }
    .insight-box {
        border-left: 5px solid #E8743B; border-radius: 8px;
        background: rgba(232,116,59,0.07); padding: 16px 20px; line-height:1.75;
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ----------------------------------------------------------------------------
# 데이터 로딩 (캐시)
# ----------------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def load_data(period_label: str, cache_buster: int = 0) -> Dict[str, Any]:
    """15분 캐시. cache_buster 값이 바뀌면 강제로 재수집한다."""
    return dl.load_market_data(period_label)


def get_api_key(sidebar_value: str) -> str:
    """st.secrets 최우선 → 사이드바 입력값 순으로 API Key 확인."""
    try:
        key = st.secrets.get("OPENAI_API_KEY", "")
        if key:
            return str(key).strip()
    except Exception:
        pass
    return (sidebar_value or "").strip()


# ----------------------------------------------------------------------------
# OpenAI 호출 (실패해도 예외를 밖으로 던지지 않음)
# ----------------------------------------------------------------------------
def call_llm(api_key: str, model: str, system: str, user: str,
             max_tokens: int = 900) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    OpenAI 1.x SDK 호출.
      성공: (text, None, 실제로 응답한 모델명)
      실패: (None, error_message, None)
    지정 모델을 쓸 수 없으면 후보 모델로 순차 재시도하므로,
    화면에는 '요청한 모델'이 아니라 '실제 응답한 모델'을 표기해야 한다.
    """
    if not api_key:
        return None, "API Key 미입력", None
    try:
        from openai import OpenAI
    except Exception as exc:
        return None, f"openai 패키지를 불러올 수 없습니다: {exc}", None

    try:
        client = OpenAI(api_key=api_key)
    except Exception as exc:
        return None, f"OpenAI 클라이언트 초기화 실패: {exc}", None

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    candidates = [model] + [m for m in FALLBACK_MODELS if m != model]
    last_error = ""
    for name in candidates:
        for kwargs in ({"temperature": 0.4, "max_tokens": max_tokens},
                       {"max_completion_tokens": max_tokens},
                       {}):
            try:
                resp = client.chat.completions.create(
                    model=name, messages=messages, **kwargs
                )
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    used = getattr(resp, "model", None) or name
                    return text, None, used
                last_error = "빈 응답"
            except Exception as exc:
                last_error = str(exc)
                # 파라미터 문제면 다음 kwargs 조합으로, 모델 문제면 다음 모델로
                if not any(t in last_error.lower()
                           for t in ("temperature", "max_tokens", "unsupported",
                                     "unrecognized", "parameter")):
                    break
    return None, last_error or "알 수 없는 오류", None


# ----------------------------------------------------------------------------
# 사이드바
# ----------------------------------------------------------------------------
def render_sidebar() -> Dict[str, Any]:
    sb = st.sidebar
    sb.title("🏗️ Steel Market Intelligence")
    sb.caption(f"최신 업데이트: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    sb.divider()

    sb.subheader("⚙️ 데이터 수집 제어")
    if sb.button("🔄 데이터 새로고침", width="stretch", type="primary"):
        st.cache_data.clear()
        st.session_state["cache_buster"] = st.session_state.get("cache_buster", 0) + 1
        st.session_state.pop("daily_insight", None)
        st.session_state.pop("market_report", None)
        st.toast("실시간 데이터를 다시 수집했습니다.", icon="🔄")
        st.rerun()

    auto_live = sb.toggle("실시간 웹 수집 사용", value=True,
                          help="끄면 네트워크 호출 없이 기준 실측값 기반으로만 표시합니다.")

    sb.divider()
    sb.subheader("🔐 OpenAI API Key")
    key_input = sb.text_input(
        "API Key (선택 입력)", type="password", placeholder="sk-...",
        help="st.secrets['OPENAI_API_KEY'] 가 있으면 그 값을 우선 사용합니다. "
             "입력값은 세션 메모리에만 존재하며 저장/전송되지 않습니다.",
    )
    api_key = get_api_key(key_input)
    if api_key:
        source = "st.secrets" if _key_from_secrets() else "사이드바 입력"
        sb.success(f"API Key 적용됨 ({source})", icon="✅")
    else:
        sb.info("Key 미입력 — 샘플 인사이트가 표시됩니다.", icon="ℹ️")

    model = sb.text_input("LLM 모델", value=_default_model(),
                          help="해당 모델을 사용할 수 없으면 자동으로 대체 모델을 시도합니다.")

    sb.divider()
    sb.subheader("📅 데이터 조회 기간")
    period = sb.radio("기간 선택", list(dl.PERIOD_MAP.keys()), index=2,
                      horizontal=False, label_visibility="collapsed")

    return {"api_key": api_key, "model": model.strip() or DEFAULT_MODEL,
            "period": period, "auto_live": auto_live, "sb": sb}


def _default_model() -> str:
    """secrets 에 OPENAI_MODEL 이 있으면 그 값을, 없으면 PRD 지정 기본 모델을 사용."""
    try:
        return str(st.secrets.get("OPENAI_MODEL", "") or DEFAULT_MODEL)
    except Exception:
        return DEFAULT_MODEL


def _key_from_secrets() -> bool:
    try:
        return bool(st.secrets.get("OPENAI_API_KEY", ""))
    except Exception:
        return False


def render_source_status(sb, bundle: Dict[str, Any]) -> None:
    sb.divider()
    sb.subheader("📡 데이터 소스 상태")
    total = len(bundle["indicators"])
    sb.caption(f"실시간 수집 {bundle['live_count']} / {total} 지표")
    for ind in bundle["indicators"].values():
        icon = "🟢" if ind.is_live else "🟡"
        sb.caption(f"{icon} {ind.label} — {ind.source}")
    missing = [k for k, ok in bundle["deps"].items() if not ok]
    if missing:
        sb.warning("미설치 패키지: " + ", ".join(missing)
                   + "\n\n`pip install -r requirements.txt` 실행을 권장합니다.", icon="⚠️")


# ----------------------------------------------------------------------------
# 상단 핵심 지표 카드
# ----------------------------------------------------------------------------
def render_metrics(bundle: Dict[str, Any]) -> None:
    ind = bundle["indicators"]
    keys = ["usdkrw", "iron_ore", "coking_coal", "rebar_kr", "hbeam_kr"]
    cols = st.columns(5, gap="small")
    for col, key in zip(cols, keys):
        item = ind.get(key)
        if item is None:
            continue
        with col:
            st.metric(label=item.label, value=item.fmt_value_short(),
                      delta=item.fmt_delta_short(),
                      help=f"{item.fmt_value()} · 전일 대비 {item.fmt_delta()} "
                           f"· 출처: {item.source}")
            tag = "🟢 실시간" if item.is_live else "🟡 기준값"
            st.markdown(
                f"<div class='src-tag'>{tag} · 전일 대비 {item.fmt_delta_abs()}</div>",
                unsafe_allow_html=True,
            )


# ----------------------------------------------------------------------------
# Section 1 : Daily Insight
# ----------------------------------------------------------------------------
INSIGHT_SYSTEM = (
    "당신은 한국 철강 산업을 담당하는 20년 경력의 원자재 시황 애널리스트입니다. "
    "제공된 수치만 근거로 사용하고 수치를 지어내지 마십시오. "
    "반드시 한국어로, 정확히 3줄 이내(각 줄 100자 내외)로만 답하십시오. "
    "각 줄은 '- ' 로 시작하는 한 문장이며, 머리말·맺음말·표는 쓰지 마십시오."
)


def render_daily_insight(bundle: Dict[str, Any], api_key: str, model: str) -> None:
    st.subheader("💡 Today's Daily Insight")
    st.caption("환율 · 철광석 · 제철용 강점탄 지표 기반 오늘의 시황 3줄 요약")

    if not api_key:
        st.info(dl.escape_dollars(dl.fallback_daily_insight(bundle)), icon="💡")
        st.caption("ℹ️ OpenAI API Key 미입력 상태 — 규칙 기반 샘플 인사이트를 표시합니다.")
        return

    cache_key = f"{bundle['period_label']}|{bundle['updated_at']:%Y%m%d%H%M}|{model}"
    if st.session_state.get("insight_key") != cache_key:
        with st.spinner("AI가 오늘의 시황을 요약하는 중입니다..."):
            text, err, used = call_llm(
                api_key, model, INSIGHT_SYSTEM,
                "다음 철강 시장 데이터를 바탕으로 오늘의 시황을 3줄 이내로 요약해 주세요.\n\n"
                + dl.build_llm_context(bundle),
                max_tokens=400,
            )
        st.session_state["insight_key"] = cache_key
        st.session_state["daily_insight"] = text
        st.session_state["insight_error"] = err
        st.session_state["insight_model"] = used

    text = st.session_state.get("daily_insight")
    err = st.session_state.get("insight_error")

    if text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:3]
        bullets = [ln.lstrip("-•* ").strip() for ln in lines]
        body = dl.escape_dollars("<br>".join(f"• {b}" for b in bullets if b))
        st.markdown(f"<div class='insight-box'>{body}</div>", unsafe_allow_html=True)
        used = st.session_state.get("insight_model") or model
        note = "" if used == model else f" (요청: {model})"
        st.caption(f"🤖 생성 모델: {used}{note} · {bundle['updated_at']:%Y-%m-%d %H:%M} 기준")
    else:
        st.info(dl.escape_dollars(dl.fallback_daily_insight(bundle)), icon="💡")
        st.caption(f"⚠️ LLM 호출 실패로 샘플 인사이트를 표시합니다. ({err})")


# ----------------------------------------------------------------------------
# Section 2 : 시각화
# ----------------------------------------------------------------------------
def _base_layout(fig: go.Figure, title: str, height: int = 470) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0.01, font=dict(size=17)),
        height=height,
        margin=dict(l=10, r=10, t=60, b=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1),
        template="plotly_white",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.18)")
    return fig


def chart_raw_materials(bundle: Dict[str, Any]) -> go.Figure:
    """철광석/강점탄(좌축) + 원달러 환율(우축) 이중 Y축 라인 차트."""
    ind = bundle["indicators"]
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    for key, color in (("iron_ore", COLORS["iron"]), ("coking_coal", COLORS["coal"])):
        item = ind[key]
        fig.add_trace(
            go.Scatter(x=item.series.index, y=item.series.values, name=item.label,
                       mode="lines", line=dict(color=color, width=2.4),
                       hovertemplate="%{y:,.2f} $/t<extra>" + item.label + "</extra>"),
            secondary_y=False,
        )

    fx = ind["usdkrw"]
    fig.add_trace(
        go.Scatter(x=fx.series.index, y=fx.series.values, name=fx.label,
                   mode="lines", line=dict(color=COLORS["fx"], width=2.2, dash="dot"),
                   hovertemplate="%{y:,.2f} 원<extra>원/달러</extra>"),
        secondary_y=True,
    )

    fig.update_yaxes(title_text="원자재 가격 ($/t)", secondary_y=False)
    fig.update_yaxes(title_text="원/달러 환율", secondary_y=True, showgrid=False)
    return _base_layout(fig, f"원자재 가격 & 원/달러 환율 추이 ({bundle['period_label']})")


def chart_domestic(bundle: Dict[str, Any]) -> go.Figure:
    """국내 철근 vs H형강 유통가 비교 (스케일 차이로 이중축 사용)."""
    ind = bundle["indicators"]
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    rebar, hbeam = ind["rebar_kr"], ind["hbeam_kr"]
    fig.add_trace(
        go.Scatter(x=rebar.series.index, y=rebar.series.values, name=rebar.label,
                   mode="lines", line=dict(color=COLORS["rebar"], width=2.6),
                   hovertemplate="%{y:,.0f} 원/톤<extra>철근</extra>"),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(x=hbeam.series.index, y=hbeam.series.values, name=hbeam.label,
                   mode="lines", line=dict(color=COLORS["hbeam"], width=2.6),
                   hovertemplate="%{y:,.0f} 원/톤<extra>H형강</extra>"),
        secondary_y=True,
    )
    fig.update_yaxes(title_text="철근 (원/톤)", secondary_y=False, tickformat=",.0f")
    fig.update_yaxes(title_text="H형강 (원/톤)", secondary_y=True,
                     tickformat=",.0f", showgrid=False)
    return _base_layout(fig, f"국내 봉형강 유통가 추이 ({bundle['period_label']})")


def chart_sentiment(bundle: Dict[str, Any]) -> go.Figure:
    s = bundle["sentiment"]
    pairs = [("긍정", s.get("positive", 0), COLORS["pos"]),
             ("중립", s.get("neutral", 0), COLORS["neu"]),
             ("부정", s.get("negative", 0), COLORS["neg"])]
    total = sum(v for _, v, _ in pairs)
    # 0건 항목은 라벨이 겹쳐 보이므로 파이에서 제외 (전부 0이면 그대로 표시)
    shown = [p for p in pairs if p[1] > 0] or pairs
    labels = [p[0] for p in shown]
    values = [p[1] for p in shown]
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.55, sort=False,
        marker=dict(colors=[p[2] for p in shown],
                    line=dict(color="rgba(255,255,255,0.6)", width=2)),
        textinfo="label+percent",
        hovertemplate="%{label}: %{value}건 (%{percent})<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="뉴스 감성 분포", x=0.01, font=dict(size=17)),
        height=380, margin=dict(l=10, r=10, t=60, b=10),
        showlegend=True, template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        annotations=[dict(text=f"{total}건", x=0.5, y=0.5,
                          font=dict(size=20), showarrow=False)],
    )
    return fig


def render_charts(bundle: Dict[str, Any]) -> None:
    st.subheader("📈 동적 시각화 차트")
    tab1, tab2, tab3 = st.tabs(
        ["원자재 & 환율 추이", "국내 봉형강 유통가", "뉴스 감성 분석 & 키워드"]
    )

    with tab1:
        st.plotly_chart(chart_raw_materials(bundle), width="stretch")
        ind = bundle["indicators"]
        c1, c2, c3 = st.columns(3)
        for col, key in zip((c1, c2, c3), ("iron_ore", "coking_coal", "usdkrw")):
            item = ind[key]
            chg = ((item.series.iloc[-1] / item.series.iloc[0] - 1) * 100
                   if len(item.series) > 1 else 0.0)
            col.metric(f"{item.label} · 기간 변동", item.fmt_value(), f"{chg:+.2f}%")
        with st.expander("미국/중국 제품 가격 함께 보기"):
            us, cn = ind["hrc_us"], ind["hrc_cn"]
            cc1, cc2 = st.columns(2)
            cc1.metric(us.label, us.fmt_value(), us.fmt_delta())
            cc2.metric(cn.label, cn.fmt_value(), cn.fmt_delta())
            comp = pd.DataFrame({
                "미국 열연 ($/t)": us.series,
                "중국 철강 ($/t)": cn.series,
            })
            st.line_chart(comp, height=260)

    with tab2:
        st.plotly_chart(chart_domestic(bundle), width="stretch")
        ind = bundle["indicators"]
        rebar, hbeam = ind["rebar_kr"], ind["hbeam_kr"]
        spread = hbeam.last - rebar.last
        c1, c2, c3 = st.columns(3)
        c1.metric(rebar.label, rebar.fmt_value(), rebar.fmt_delta())
        c2.metric(hbeam.label, hbeam.fmt_value(), hbeam.fmt_delta())
        c3.metric("H형강 - 철근 스프레드", f"{spread:,.0f} 원", f"{spread / rebar.last * 100:.1f}%")

        render_kr_price_evidence([rebar, hbeam])

    with tab3:
        left, right = st.columns([1, 1.25], gap="large")
        with left:
            st.plotly_chart(chart_sentiment(bundle), width="stretch")
        with right:
            st.markdown("##### 🏷️ 주요 키워드")
            kws = bundle["keywords"]
            if kws:
                tags = "".join(
                    f"<span class='kw-tag'>{word} <b>{cnt}</b></span>"
                    for word, cnt in kws
                )
                st.markdown(tags, unsafe_allow_html=True)
            else:
                st.caption("추출된 키워드가 없습니다.")

            st.markdown("##### 📰 실시간 철강 뉴스 Top 5")
            emoji = {"positive": "🟢", "neutral": "⚪", "negative": "🔴"}
            for n in bundle["news"]:
                mark = emoji.get(n.get("sentiment", "neutral"), "⚪")
                title = n["title"]
                link = n.get("link") or ""
                meta = " · ".join(x for x in (n.get("source", ""), n.get("published", "")) if x)
                if link:
                    st.markdown(dl.escape_dollars(f"{mark} [{title}]({link})"))
                else:
                    st.markdown(dl.escape_dollars(f"{mark} {title}"))
                if meta:
                    st.caption(meta)


def render_kr_price_evidence(items) -> None:
    """국내 유통가의 실시간 추출 근거(기사 문장·출처·링크)를 표시."""
    st.markdown("##### 🧾 유통시세 산출 근거")
    for item in items:
        ev = item.evidence
        if not ev:
            st.caption(f"🟡 **{item.label}** — {item.source}. "
                       "이번 수집에서는 전문지 기사에서 시세 문장을 찾지 못해 "
                       "기준값으로 표시합니다.")
            continue
        when = ev.get("date")
        stamp = f"{when:%Y-%m-%d}" if when else "날짜 미상"
        link = ev.get("link") or ""
        head = f"🟢 **{item.label}** {item.fmt_value()} · {ev['publisher']} {ev['kind']} · {stamp}"
        st.markdown(f"{head}  ·  [기사 원문]({link})" if link else head)
        st.caption(f"“{ev['sentence']}”")

    st.caption("※ 국내 봉형강은 공개 가격 API가 없어, 철강 전문지가 보도한 주간 유통시세 "
               "문장을 파싱해 최신값을 잡습니다. 최신값은 실측이지만 기간 추이 곡선은 "
               "근사치이며, 실제 매매 기준가는 유통 시황지를 확인하세요.")


# ----------------------------------------------------------------------------
# Section 3 : AI Daily Market Report
# ----------------------------------------------------------------------------
REPORT_SYSTEM = (
    "당신은 철강·원자재 리서치 하우스의 수석 애널리스트입니다. "
    "제공된 데이터에 근거하여 한국어 Markdown 리포트를 작성하십시오. "
    "없는 수치를 만들어내지 말고, 제공된 지표만 인용하십시오. "
    "구성은 반드시 다음 3개 섹션을 '## ' 헤딩으로 포함합니다: "
    "'## 1. 시장 종합 요약'(불릿 4~6개), "
    "'## 2. 시황 분석'(원료·환율·제품 관점 3개 문단), "
    "'## 3. 단기 전망'(1~2주 전망 불릿 3~4개 + 리스크 요인 1줄). "
    "전체 900자 내외로 간결하게 작성하십시오."
)


def render_report(bundle: Dict[str, Any], api_key: str, model: str) -> None:
    st.subheader("📄 AI Daily Market Report 생성")
    st.caption("수집된 전 지표와 뉴스를 종합해 시장 요약 · 시황 분석 · 단기 전망 리포트를 생성합니다.")

    col_btn, col_info = st.columns([1, 3])
    clicked = col_btn.button("📄 AI 종합 시황 보고서 생성",
                             width="stretch", type="primary")
    if not api_key:
        col_info.caption("ℹ️ API Key 미입력 시 규칙 기반 샘플 리포트가 생성됩니다.")

    if clicked:
        if api_key:
            with st.spinner("AI가 종합 시황 보고서를 작성하는 중입니다... (약 10~20초)"):
                text, err, used = call_llm(
                    api_key, model, REPORT_SYSTEM,
                    "다음 철강 시장 데이터를 바탕으로 오늘자 종합 시황 리포트를 작성해 주세요.\n\n"
                    + dl.build_llm_context(bundle),
                    max_tokens=1600,
                )
            if text:
                st.session_state["market_report"] = text
                st.session_state["report_error"] = None
                st.session_state["report_model"] = used
            else:
                st.session_state["market_report"] = dl.fallback_report(bundle)
                st.session_state["report_error"] = err
        else:
            st.session_state["market_report"] = dl.fallback_report(bundle)
            st.session_state["report_error"] = "API Key 미입력"

    report = st.session_state.get("market_report")
    if report:
        err = st.session_state.get("report_error")
        if err:
            st.warning(f"LLM 리포트를 생성하지 못해 샘플 리포트를 표시합니다. ({err})", icon="⚠️")
        with st.container(border=True):
            st.markdown(dl.escape_dollars(report))
        used = st.session_state.get("report_model")
        if used and not err:
            st.caption(f"🤖 생성 모델: {used} · {bundle['updated_at']:%Y-%m-%d %H:%M} 기준")
        stamp = bundle["updated_at"].strftime("%Y%m%d_%H%M")
        st.download_button(
            "⬇️ 리포트 Markdown 다운로드",
            data=report.encode("utf-8"),
            file_name=f"steel_market_report_{stamp}.md",
            mime="text/markdown",
        )


# ----------------------------------------------------------------------------
# 원본 데이터 테이블
# ----------------------------------------------------------------------------
def render_raw_table(bundle: Dict[str, Any]) -> None:
    with st.expander("🗂️ 원본 시계열 데이터 보기 / CSV 다운로드"):
        frames = {}
        for item in bundle["indicators"].values():
            frames[f"{item.label} ({item.unit})"] = item.series
        df = pd.DataFrame(frames)
        df.index.name = "일자"
        st.dataframe(df.sort_index(ascending=False).round(2),
                     width="stretch", height=320)
        st.download_button(
            "⬇️ CSV 다운로드",
            data=df.to_csv().encode("utf-8-sig"),
            file_name=f"steel_market_{bundle['updated_at']:%Y%m%d_%H%M}.csv",
            mime="text/csv",
        )


# ----------------------------------------------------------------------------
# 메인
# ----------------------------------------------------------------------------
def main() -> None:
    cfg = render_sidebar()

    st.title("🏗️ 철강 산업 실시간 데이터 & AI 인사이트 대시보드")
    st.caption(
        "원자재(철광석·제철용 강점탄) · 원/달러 환율 · 국내외 제품 유통가를 실시간 수집하고, "
        "LLM 기반 Daily Insight 와 종합 시황 리포트를 제공합니다."
    )

    with st.spinner("실시간 시장 데이터를 수집하는 중입니다..."):
        try:
            if cfg["auto_live"]:
                bundle = load_data(cfg["period"],
                                   st.session_state.get("cache_buster", 0))
            else:
                bundle = dl.load_market_data(cfg["period"], live=False)
        except Exception as exc:  # 최후의 안전장치
            st.error(f"데이터 수집 중 오류가 발생해 기준 실측값으로 표시합니다. ({exc})")
            bundle = dl.load_market_data(cfg["period"], live=False)

    render_source_status(cfg["sb"], bundle)

    st.caption(f"🕒 데이터 기준 시각 : {bundle['updated_at']:%Y-%m-%d %H:%M:%S} · "
               f"조회 기간 : {bundle['period_label']} · "
               f"실시간 수집 {bundle['live_count']}/{len(bundle['indicators'])} 지표")

    render_metrics(bundle)
    st.divider()

    render_daily_insight(bundle, cfg["api_key"], cfg["model"])
    st.divider()

    render_charts(bundle)
    st.divider()

    render_report(bundle, cfg["api_key"], cfg["model"])
    st.divider()

    render_raw_table(bundle)

    st.caption(
        "ⓘ 본 대시보드는 공개 웹 데이터(yfinance · Trading Economics · 뉴스 RSS)를 기반으로 하며, "
        "투자 판단의 근거로 사용할 수 없습니다. 네트워크 장애 시 기준 실측값 기반 대체 데이터가 표시됩니다."
    )


if __name__ == "__main__":
    main()
