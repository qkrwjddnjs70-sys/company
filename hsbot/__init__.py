"""hsbot — 홈쇼핑 방송 자막을 채널 간 비교 가능한 숫자로 바꾸는 도구."""
from .models import Broadcast, Segment, load_broadcasts, save_broadcasts
from .lexicon import load_lexicon, load_combined, Lexicon, Axis
from .metrics import analyze, BroadcastMetrics
from .compare import compare, ComparisonResult

__version__ = "0.1.0"
__all__ = [
    "Broadcast", "Segment", "load_broadcasts", "save_broadcasts",
    "load_lexicon", "load_combined", "Lexicon", "Axis",
    "analyze", "BroadcastMetrics", "compare", "ComparisonResult",
]
