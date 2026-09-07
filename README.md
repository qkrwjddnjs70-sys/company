# hsbot — 홈쇼핑 방송 화법 비교 분석기

같은 상품을 여러 홈쇼핑사가 **어떻게 팔았는지** 자막(스크립트)에서 뽑아 숫자로 비교한다.

```
키워드 검색 ──► 모델별 묶기 ──► 자막 수집 ──► 정량화 ──► 채널 간 비교 ──► HTML 리포트
  "로보락"      같은 제품끼리    DataHub API   16개 축    지수·편차·차별표현
              (제품 차이 배제)  붙여넣기 텍스트
```

```bash
python3 -m hsbot collect "로보락" --since 2026-08-01 --html out/report.html
```

한 줄이면 검색 → 모델별 묶기 → 자막 수집 → 비교 → 리포트까지 끝난다.
다만 **최초 1회는 브라우저에서 스펙을 떠 와야 한다**(아래 참조). 공식 API 문서가
없는 서비스라 엔드포인트·필드명을 코드가 스스로 알 방법이 없기 때문이다.

## 빠른 시작 (API 키 없이 동작 확인)

```bash
./tools/run_demo.sh        # 합성 데이터로 전 과정 실행 → out/report.html
python3 -m unittest discover -s tests
```

> `fixtures/synthetic/` 는 **합성(가짜) 자막**이다. 실제 방송 데이터가 아니며,
> 파이프라인이 채널별 화법 차이를 실제로 검출하는지 확인하기 위한 검증용이다.

## 실제 데이터 연결

### 0) 한 번만 하는 준비 — 브라우저에서 스펙 뜨기

키워드 검색 자동화의 유일한 전제조건이다. **딱 한 번만** 하면 그 뒤로는 명령 한 줄이다.

```
1. 로그인 상태로 사이트에서 "로보락" 을 검색한다
2. 검색 결과에서 방송 하나를 열고 자막 탭까지 눌러본다   ← 검색+자막 둘 다 관측하려고
3. F12 → Network → Fetch/XHR 필터 → Preserve log 체크
4. 우클릭 → "Save all as HAR with content" 로 저장
5. python3 -m hsbot devtools --har page.har --product-key gsshop_1101476773
```

**왜 이 단계가 필요한가.** 공식 API 문서가 없는 서비스라 엔드포인트 주소와 응답
필드명을 코드가 알 방법이 없다. 그리고 **쿠키만으로는 부족하다** — 인증(쿠키)과
스펙(주소+응답 구조)은 별개이고, 스펙은 Network 탭에만 있다.

`devtools`가 HAR에서 하는 일:

| 단계 | 내용 |
|---|---|
| **검색 응답 탐지** | 모든 JSON 응답에서 "상품키 + 상품명 + 절대시각 + 가격" 조합을 점수화해 방송 목록을 찾아냄 |
| **자막 응답 탐지** | "시각 필드 + 한글 본문 배열"을 점수화. 상품 목록과 헷갈리지 않게 서로를 감점 처리 |
| 스키마 추론 | 배열 경로와 필드명을 자동 판별. **필드명이 아니라 값의 생김새로** 판정하므로 `goodsNm` `prdKey` `onairStart` 같은 낯선 줄임말도 잡아냄 |
| 검색어 템플릿화 | 관측된 검색어를 `{keyword}`, 페이지·개수를 `{page}` `{size}` 로 치환 → 다른 키워드에도 재사용 |
| 메타 추론 | 상품명·가격·채널명 경로를 자막 배열 바깥에서 탐색 |
| 설정 생성 | `config/datahub.json` 초안 작성 (`endpoints.search` + `endpoints.subtitle`) |
| 쿠키 분리 | 쿠키를 **설정 파일에 절대 쓰지 않고** `.secrets/datahub.cookie` (0600, gitignore)로 분리 |

### 1) 검색 기반 자동 수집 (평상시 쓰는 경로)

```bash
export HSMOA_DATAHUB_COOKIE="$(cat .secrets/datahub.cookie)"

# 어떤 방송이 잡히는지 먼저 확인 (수집은 안 함)
python3 -m hsbot search "로보락" --since 2026-08-01 --require-keyword

# 검색 → 수집 → 분석 → 리포트까지 한 번에
python3 -m hsbot collect "로보락" --since 2026-08-01 --html out/report.html
```

`search` 출력 예:

```
■ '로보락' 검색 — 원본 11건
  필터 통과 9건

  [모델별 묶음]  ※ 채널 2곳 이상이어야 비교가 성립합니다
   ✓ S9 MAX ULTRA           방송  6건 / 채널 4곳  CJ온스타일, GS SHOP, 롯데홈쇼핑, 현대홈쇼핑
   ✓ Q REVO PRO             방송  2건 / 채널 2곳  CJ온스타일, 롯데홈쇼핑
   · 로보락 전용 먼지봉투   방송  1건 / 채널 1곳  NS홈쇼핑
```

**왜 모델별로 묶는가.** "로보락"으로 검색하면 S9 MAX Ultra, Q Revo Pro, 먼지봉투,
심지어 다른 브랜드까지 섞여 나온다. 이걸 한 표에 올리면 *채널별 화법 차이*가 아니라
*제품 차이*를 재게 된다. 그래서 모델 단위로 묶고, **2개 채널 이상 겹치는 모델만**
비교 대상으로 삼는다. 겹치는 채널이 가장 많은 모델이 자동 선택된다(`--model` 로 직접 지정 가능).

선별 옵션:

| 옵션 | 용도 |
|---|---|
| `--require KW...` | 상품명에 이 단어가 **모두** 있어야 통과 (`--require S9 Ultra`) |
| `--require-keyword` | 상품명에 검색어가 실제로 든 것만 — 엉뚱한 브랜드 제거 |
| `--exclude KW...` | 이 단어가 있으면 제외 (`--exclude 먼지봉투 필터`) |
| `--channel CH...` | 특정 채널만 (코드·이름 부분일치) |
| `--since` / `--until` | 방송 시작시각 기준 기간 |
| `--min-minutes` | 편성 길이 하한 — 짧은 삽입 방송 제외 |
| `--model` / `--all-models` | 비교할 모델 직접 지정 / 모델 구분 없이 전부(권장 안 함) |
| `--pages` `--size` | 검색 페이지 수와 페이지당 개수 |

### 2) HAR만으로 (쿠키·네트워크 없이)

HAR에는 응답 본문이 통째로 들어 있어서, 브라우저에서 한 번 검색해두면
그 결과를 **요청 0회로** 그대로 쓸 수 있다.

```bash
# 검색 결과를 HAR에서 직접 읽기
python3 -m hsbot search "로보락" --har search.har --out-targets config/targets.json

# 자막도 HAR에서 직접 추출
python3 -m hsbot devtools --har page.har --extract data/gsshop.json --channel-name "GS SHOP"
python3 -m hsbot analyze --input "data/*.json" \
        --lexicon core_ko product_robot_vacuum --html out/report.html
```

다만 **자막은 방송 1건당 HAR 1개**가 필요하다. 방송 수만큼 반복 수집하려면
결국 쿠키로 API를 호출하는 (1)번 경로를 써야 한다.

Copy as cURL만 있는 경우 `--curl req.txt` 도 되지만, 응답 본문이 없어 **필드 추론은 안 된다**
(엔드포인트·쿠키만 파악).

쿠키 사용 시 유의점:

- **쿠키는 비밀번호와 같다.** 저장소·설정 파일·채팅에 붙여넣지 말 것. `.secrets/`와 `*.har`은 gitignore 처리돼 있다.
- **만료된다.** 401/403이 나면 세션이 끊긴 것이다. F12에서 다시 복사하면 된다(오류 메시지가 안내한다).
- **본인 계정 범위에서만.** 자동 수집은 서비스 이용약관 확인 후 진행하고, `rate_limit_sec`(기본 1초)을 낮추지 말 것.

### 3) DataHub 공식 API (문서를 받은 경우)

경로와 필드명은 코드가 아니라 설정에 있다. 문서를 받으면 JSON만 고치면 된다.

```bash
cp config/datahub.example.json config/datahub.json
cp config/targets.example.json config/targets.json
# config/datahub.json 의 endpoints / mapping 을 실제 스펙에 맞게 수정
export HSMOA_DATAHUB_API_KEY="발급받은키"

python3 -m hsbot url "https://datahub.hsmoa.com/product/gsshop_1101476773?..."   # 파라미터 확인
python3 -m hsbot fetch --targets config/targets.json --out data/broadcasts.json
python3 -m hsbot analyze --input data/broadcasts.json \
        --lexicon core_ko product_robot_vacuum --html out/report.html
```

`mapping.*_paths` 는 **후보 경로 목록**이다. 순서대로 시도하므로 확실치 않으면 여러 개 적어두면 된다.
경로를 하나도 못 찾으면 조용히 0줄로 넘어가지 않고 오류로 알린다(빈 배열과 구분).

### 4) 붙여넣기 (API·쿠키 둘 다 막혔을 때)

DataHub 자막 탭 내용을 텍스트 파일로 저장한 뒤:

```bash
python3 -m hsbot paste --file sub.txt \
  --channel gsshop --channel-name "GS SHOP" \
  --product "로보락 S9 MAX Ultra" \
  --start "2026-09-04T20:38:00+09:00" --end "2026-09-04T21:38:00+09:00" \
  --out data/gsshop.json
```

지원 형식: `00:05:12 본문` / `[00:05:12 - 00:05:15] 본문` / `2026-09-04 20:43:12 본문` /
타임스탬프 없는 줄(편성 시간에 균등 배분) / `쇼호스트: 본문`(화자 분리)

## 무엇을 재는가

| 분류 | 지표 | 의미 |
|---|---|---|
| 발화량 | 자/분, 줄/분, 줄당 글자 | 편성 길이와 무관한 발화 밀도 |
| 화법 | 어휘 다양도(TTR), 반복지수 | 같은 말을 반복하는 정도 |
| 소구축 | 축별 분당 빈도, 지수(평균=100), 커버리지 | 무엇으로 설득했나 |
| 구성 | 첫 언급 시점, 피크 구간, 5분 단위 타임라인 | 언제 꺼냈나 |
| 숫자 | 가격/할인율/할부 언급, 최저·최고 금액 | 숫자 소구의 강도 |
| 차별 | Dirichlet 로그오즈비 z | 그 채널만의 표현 |

**소구축 16개** — 공통 9축(`core_ko`): 가격·할인 / 혜택·구성 / 긴급·희소 / 주문 유도 /
신뢰·권위 / 시연·실증 / 감성·생활 / 비교·차별 / 안심·리스크해소.
제품 7축(`product_robot_vacuum`): 흡입력 / 물걸레·세척 / 도크·자동화 / 주행·인식 /
폼팩터·소음 / 앱·연동 / 배터리·용량.

다른 카테고리를 다루려면 `hsbot/lexicons/` 에 JSON 하나만 추가하고 `--lexicon` 에 이름을 넘기면 된다.

## 설계 원칙

1. **분당 정규화** — 편성 길이가 다르면 총량 비교는 왜곡된다. 모든 강도 지표는 per-minute.
2. **축 중복 허용** — "지금 이 시간 최저가"는 가격·긴급 양쪽에 카운트된다. 실제 화법이 복합적이다.
3. **강도와 구성의 분리** — "얼마나 말했나"와 "언제 말했나"는 다른 질문이다.
4. **상대 비교 우선** — 절대 빈도가 아니라 평균 대비 지수(100 기준)와 편차로 읽는다.
5. **의존성 0** — 표준 라이브러리만 사용. 사내 어디서든 그대로 돈다.

## 알려진 한계

- **키워드 사전 기반**이라 문맥·반어·부정을 모른다. "비싸지 않습니다"도 가격 축에 잡힌다.
- **형태소 분석기 미사용**. 조사 제거는 규칙 근사다(`최저가` 같은 도메인어는 보호 목록으로 방어).
  축 매칭은 원문 부분일치라 이 근사의 영향을 받지 않고, 상위 키워드·차별 표현에만 영향을 준다.
- **모델 묶기도 근사다**. 상품명에서 모델 코드를 규칙으로 뽑아 묶는다
  (`"[단독] 로보락 S9 MAX Ultra 정품"` → `S9 MAX ULTRA`). 채널마다 표기가 크게
  다르면 같은 제품이 갈릴 수 있으니, `search` 출력의 묶음을 눈으로 확인하고
  어긋나면 `--model`·`--require` 로 직접 지정하는 편이 확실하다.
- **자막 품질에 종속**. 음성인식 자막이면 오인식이 그대로 지표가 된다.
- **인과 해석 금지**. 이 도구는 "어떻게 팔았나"를 재지 "그래서 잘 팔렸나"를 증명하지 않는다.
  매출·주문 데이터와 결합해야 의미가 생긴다.

## 구조

```
hsbot/
  models.py        정규화 스키마 (Broadcast / Segment)
  textutil.py      한국어 토크나이저, 금액·할인율·할부 추출
  lexicon.py       소구축 사전 로딩·매칭
  lexicons/*.json  사전 (공통 / 제품별)
  metrics.py       방송 1건 정량화
  compare.py       채널 간 비교, 차별 표현(로그오즈비)
  report.py        다크모드 HTML 리포트
  discover.py      검색 결과 선별: 모델별 묶기, 기간·채널 필터, 중복 제거
  cli.py           url / search / collect / devtools / fetch / paste / analyze
  sources/
    datahub.py     DataHub API 어댑터 (검색·자막, 설정 주도, API키·쿠키 인증)
    devtools.py    HAR/cURL 역추적, 검색·자막 스키마 추론, 설정 생성
    paste.py       붙여넣기 텍스트 파서
    localjson.py   정규화 JSON 재로딩
tools/             합성 자막·검색HAR 생성기, 데모 스크립트
tests/             112개 회귀 테스트 (전부 네트워크 없이 실행)
```
