"""`.env` 파일 로딩 (stdlib만 사용, python-dotenv 없이).

이미 설정된 환경변수는 덮어쓰지 않는다 — 쉘에서 직접 export 한 값이 항상 우선한다.
"""
from __future__ import annotations

import os

DEFAULT_PATH = ".env"


def load_dotenv(path: str = DEFAULT_PATH) -> int:
    """`KEY=VALUE` 줄을 읽어 os.environ에 채운다. 몇 줄을 채웠는지 반환한다.

    `#`으로 시작하는 줄과 빈 줄은 무시한다. 값을 감싼 따옴표(`"..."`, `'...'`)는 벗긴다.
    """
    if not os.path.exists(path):
        return 0
    loaded = 0
    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded += 1
    return loaded
