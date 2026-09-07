# hsbot — 홈쇼핑 방송 화법 비교 분석기

같은 상품을 여러 홈쇼핑사가 **어떻게 팔았는지** 자막(스크립트)에서 뽑아 숫자로 비교한다.

```
자막 수집 ──► 정규화(Broadcast/Segment) ──► 소구축 정량화 ──► 채널 간 비교 ──► HTML 리포트
 DataHub API                                  16개 축              지수·편차·차별표현
 붙여넣기 텍스트
```

## 빠른 시작 (API 키 없이 동작 확인)

```bash
./tools/run_demo.sh        # 합성 데이터로 전 과정 실행 → out/report.html
python3 -m unittest discover -s tests
```

> `fixtures/synthetic/` 는 **합성(가짜) 자막**이다. 실제 방송 데이터가 아니며,
> 파이프라인이 채널별 화법 차이를 실제로 검출하는지 확인하기 위한 검증용이다.

## 실제 데이터 연결

### 1) F12 개발자도구로 스펙 역추적 (문서 없이 시작할 때)

공식 API 문서가 없어도, 브라우저가 실제로 보내는 요청을 관찰하면 연동할 수 있다.
**쿠키만으로는 부족하다.** 인증(쿠키)과 스펙(엔드포인트+응답 구조) 둘 다 필요하고,
스펙은 Network 탭에만 있다.

```
1. 로그인 상태로 자막 탭이 있는 방송 페이지를 연다
2. F12 → Network → Fetch/XHR 필터 → Preserve log 체크 → 새로고침 후 자막 탭 클릭
3. 우클릭 → "Save all as HAR with content" 로 저장
4. python3 -m hsbot devtools --har page.har --product-key gsshop_1101476773
```

`devtools`가 하는 일:

| 단계 | 내용 |
|---|---|
| 자막 응답 탐지 | HAR의 모든 JSON 응답을 훑어 "시각 필드 + 한글 본문 배열"을 점수화. 배너·광고 응답은 걸러짐 |
| 스키마 추론 | 배열 경로, 본문/시작/종료/화자 필드명을 자동 판별 (화자 컬럼이 본문으로 오인되지 않게 분리) |
| 메타 추론 | 상품명·가격·채널명 경로를 자막 배열 바깥에서 탐색 |
| 설정 생성 | `config/datahub.json` 초안 작성. 경로·쿼리를 `{product_key}` `{start_datetime}` 등으로 템플릿화 |
| 쿠키 분리 | 쿠키를 **설정 파일에 절대 쓰지 않고** `.secrets/datahub.cookie` (0600, gitignore)로 분리 |

```bash
export HSMOA_DATAHUB_COOKIE="$(cat .secrets/datahub.cookie)"
python3 -m hsbot fetch --targets config/targets.json --out data/broadcasts.json
```

Copy as cURL만 있는 경우 `--curl req.txt` 도 되지만, 응답 본문이 없어 **필드 추론은 안 된다**
(엔드포인트·쿠키만 파악).

쿠키 사용 시 유의점:

- **쿠키는 비밀번호와 같다.** 저장소·설정 파일·채팅에 붙여넣지 말 것. `.secrets/`와 `*.har`은 gitignore 처리돼 있다.
- **만료된다.** 401/403이 나면 세션이 끊긴 것이다. F12에서 다시 복사하면 된다(오류 메시지가 안내한다).
- **본인 계정 범위에서만.** 자동 수집은 서비스 이용약관 확인 후 진행하고, `rate_limit_sec`(기본 1초)을 낮추지 말 것.

### 2) DataHub 공식 API (문서를 받은 경우)

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

### 3) 붙여넣기 (API·쿠키 둘 다 막혔을 때)

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
  cli.py           url / devtools / fetch / paste / analyze
  sources/
    datahub.py     DataHub API 어댑터 (설정 주도, API키·쿠키 인증)
    devtools.py    HAR/cURL 역추적, 스키마 추론, 설정 생성
    paste.py       붙여넣기 텍스트 파서
    localjson.py   정규화 JSON 재로딩
tools/             합성 데이터 생성기, 데모 스크립트
tests/             48개 회귀 테스트
```
