# 🏗️ Steel Market Intelligence

**철강 산업 실시간 데이터 & AI 인사이트 대시보드**

철강 업계의 핵심 원자재(철광석 · 제철용 강점탄), 원/달러 환율, 국내외 제품 유통가를
100% 무료 공개 데이터로 실시간 수집하고, OpenAI LLM 기반의 **Daily Insight**와
**종합 시황 리포트**를 제공하는 Streamlit 대시보드입니다.

---

## 1. 기획 의도

철강사의 원가는 *철광석 + 제철용 강점탄 + 환율* 세 변수에 의해 대부분 결정되지만,
이 지표들은 서로 다른 사이트에 흩어져 있고 유료 단말(플랫츠·SBB 등) 없이는
한눈에 보기 어렵습니다. 본 프로젝트는 다음을 목표로 합니다.

- **무료·무인증 데이터만으로** 원가-판가 스프레드를 한 화면에서 모니터링
- 숫자 나열이 아닌 **LLM 기반 3줄 요약**으로 의사결정 시간 단축
- **API Key 없이도 완전히 동작**하는 견고한 Fallback 구조 (배포 시 무중단)

---

## 2. 주요 기능

| 구분 | 내용 |
|---|---|
| Top Metric | 원/달러 환율, 철광석, 제철용 강점탄, 국내 철근·H형강 유통가 (전일 대비 증감) |
| Section 1 | 💡 Today's Daily Insight — LLM 3줄 시황 요약 (Key 없으면 규칙 기반 샘플) |
| Section 2 | 📈 Plotly 차트 3탭 (원자재·환율 이중축 / 봉형강 유통가 / 뉴스 감성·키워드) |
| Section 3 | 📄 AI 종합 시황 보고서 생성 (Markdown 출력 + 다운로드) |
| 부가 | 원본 시계열 테이블 · CSV 다운로드, 데이터 소스 실시간/대체 상태 표시 |

---

## 3. 데이터 소스

| 지표 | 1순위 소스 | 대체(Fallback) |
|---|---|---|
| 원/달러 환율 | `yfinance` — `KRW=X` | 기준 실측값 1,385원 기반 추이 |
| 철광석 ($/t) | `yfinance` — `TIO=F` (SGX 62% Fe) | Trading Economics `iron-ore` → 기준값 |
| **제철용 강점탄 ($/t)** | Trading Economics `coking-coal` | `AHC=F` → 기준 실측값 **$260/t** |
| 미국 열연 HRC | Trading Economics `hrc-steel` | 기준 실측값 |
| 중국 철강 | Trading Economics `steel` (CNY → USD 환산) | 기준 실측값 |
| **국내 철근 / H형강 유통가** | 철강 전문지 RSS 요약문의 주간 유통시세 문장 파싱 | 실측 기준값 매핑 (86만 / 120만 원/톤) |
| 뉴스 | `feedparser` — 구글/네이버 뉴스 RSS | 내장 샘플 뉴스 5건 |

> 📌 **국내 유통가 수집 방식**
> 국내 철근·H형강 유통가는 공개 API가 없고 전문지의 가격 DB는 유료 회원 전용입니다.
> 대신 **철강금속신문·스틸데일리·페로타임즈의 전체기사 RSS 요약문**에 주간 유통시세가
> 문장으로 실린다는 점을 이용합니다.
>
> ```
> "9월 첫째 주 국산 철근 유통시세(SD400, 10mm)는 톤당 84만~85만원으로 전주 대비 보합 출발했다."
> "9월 첫째 주 국산 중소형 H형강 유통시세는 톤당 118만~119만원으로 전주 대비 약보합 출발했다."
> ```
>
> 이 문장에서 가격을 추출해 `84만~85만원 → 845,000원/톤`(구간 중간값)으로 환산합니다.
> - **유통시세 → 유통가격 → 고시가격** 순으로 우선순위를 두어 채택합니다.
> - `80만 원 후반대` 같은 모호 표현, 정상 범위를 벗어난 값은 버립니다.
> - 최근 21일 이내 기사만 사용하며, 채택된 **근거 문장·매체·날짜·기사 링크를 UI에 그대로 표시**해
>   사용자가 직접 검증할 수 있게 했습니다.
> - 추출 실패 시 자동으로 기준값 매핑으로 전환됩니다(🟡 표시).
>
> ⚠️ **주의:** 최신값은 실측이지만 **기간 추이 곡선은 근사치**입니다(과거 시세 아카이브가 무료로
> 공개되지 않기 때문). 원자재·환율 차트와 달리 봉형강 추이선은 참고용으로만 보십시오.

> ⚠️ **강점탄 파싱 주의점**
> Trading Economics 페이지의 `td#p` 셀을 무조건 첫 번째로 집으면 사이드 테이블의
> **다른 상품 가격**($164 등)을 잘못 가져옵니다. 본 구현은 상품명이 명시된
> `meta description`("Coking Coal traded flat at 282 USD/T")을 1순위로 파싱하고,
> 테이블은 **행 이름을 대조**한 뒤에만 사용합니다. 또한 발전용 연료탄(Thermal Coal,
> 약 $100/t)이 아닌 **제철용 강점탄** 지표만 사용합니다.

---

## 4. 설치 및 실행

```bash
git clone <this-repo>
cd Final
python -m venv .venv && .venv\Scripts\activate    # Windows
# source .venv/bin/activate                        # macOS / Linux

pip install -r requirements.txt
streamlit run app.py
```

브라우저에서 `http://localhost:8501` 로 접속합니다.

### OpenAI API Key 설정 (선택)

Key가 없어도 앱은 정상 동작하며, AI 섹션만 규칙 기반 샘플로 대체됩니다.
적용 우선순위는 **`st.secrets` → 사이드바 입력창** 입니다.

```bash
cp .streamlit/secrets.toml.template .streamlit/secrets.toml
# secrets.toml 을 열어 OPENAI_API_KEY 값을 입력
```

또는 실행 후 사이드바의 `API Key (선택 입력)` 칸에 직접 입력합니다
(입력값은 세션 메모리에만 존재하며 파일로 저장되지 않습니다).

---

## 5. Streamlit Cloud 배포

1. 본 저장소를 GitHub에 푸시합니다. **`.streamlit/secrets.toml` 은 절대 커밋하지 마세요**
   (`.gitignore` 에 이미 등록되어 있습니다).
2. [share.streamlit.io](https://share.streamlit.io) → **New app** → 저장소/브랜치 선택,
   Main file path 에 `app.py` 지정.
3. **Advanced settings → Secrets** 에 아래를 붙여넣습니다 (선택).

   ```toml
   OPENAI_API_KEY = "sk-..."
   # OPENAI_MODEL = "gpt-5.6-sol"
   ```

4. Deploy 클릭. 배포 환경에서 외부 사이트 접근이 차단되더라도
   기준 실측값 기반 대체 데이터로 **오류 없이 렌더링**됩니다.

---

## 6. 프로젝트 구조

```
Final/
├── app.py                          # Streamlit 메인 대시보드 (UI · 차트 · LLM 호출)
├── data_loader.py                  # 데이터 수집/스크래핑 · Fallback 로직
├── requirements.txt                # 의존 패키지
├── README.md
├── .gitignore                      # secrets.toml / .env 커밋 방지
└── .streamlit/
    ├── config.toml                 # 테마 설정
    └── secrets.toml.template       # 배포 환경 변수 템플릿
```

---

## 7. 안정성 설계 (Fallback 정책)

- 모든 네트워크 호출에 **timeout 8초 + try/except** 적용, 예외를 상위로 던지지 않음
- 스크래핑 결과에 **정상 범위(SANITY) 검증**을 적용해 이상치 파싱을 차단
- 실패 시 **날짜 시드 기반 결정론적 대체 시계열** 생성 (같은 날엔 항상 동일한 값)
- 선택 의존성(`yfinance` · `feedparser`) 미설치 시에도 **import 단계에서 죽지 않음**
- LLM 호출 실패(모델 미지원·쿼터 초과·네트워크)는 자동으로 대체 모델 재시도 후
  규칙 기반 샘플 리포트로 전환
- 사이드바에서 각 지표의 🟢 실시간 / 🟡 대체 상태를 항상 확인 가능

---

## 8. 참고 및 면책

- 본 대시보드의 수치는 공개 웹 데이터 기반의 참고용이며, **투자 판단의 근거로 사용할 수 없습니다.**
- 국내 봉형강 유통가는 공개 실시간 API가 없어 시장 실측 기준값에 추이를 매핑한 값입니다.
  실제 거래 기준가는 유통 시황지를 확인하십시오.
- 스크래핑 대상 사이트의 구조 변경 시 해당 지표는 자동으로 대체 데이터로 전환됩니다.
