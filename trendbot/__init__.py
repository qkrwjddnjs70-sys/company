"""trendbot — 네이버 검색 데이터로 홈쇼핑 소싱 인사이트를 찾는 도구.

두 가지 기능:
  1) 키워드 추이 조회 — 입력한 키워드의 최근 3개년 월간 검색량을 연도별로 겹쳐본다.
  2) 급상승 키워드 발견 — 미리 등록해 둔 후보 키워드(카테고리 시드 + 관심 키워드)
     중에서 최근 검색량이 평소보다 튄 것을 찾아 순위로 보여준다.

네이버는 실시간 급상승 검색어 API를 2021년에 폐지했다. 그래서 (2)는 "모르는
검색어를 네이버가 알려주는" 기능이 아니라, config/trendbot.json에 등록해 둔
후보 키워드 안에서 스파이크를 계산하는 기능이다 — README 참고.
"""
from .envfile import load_dotenv
from .naver_api import NaverDataLabClient, NaverApiError, TrendPoint, TrendSeries
from .pool import PoolConfig, Category
from .searchad_api import RelatedKeyword, SearchAdClient, SearchAdError, rank_related
from .spike import SpikeResult, compute_spike, rank_spikes
from .yearly import yearly_overlay, weekly_overlay

__version__ = "0.1.0"
__all__ = [
    "load_dotenv",
    "NaverDataLabClient", "NaverApiError", "TrendPoint", "TrendSeries",
    "PoolConfig", "Category",
    "SearchAdClient", "SearchAdError", "RelatedKeyword", "rank_related",
    "SpikeResult", "compute_spike", "rank_spikes",
    "yearly_overlay", "weekly_overlay",
]
