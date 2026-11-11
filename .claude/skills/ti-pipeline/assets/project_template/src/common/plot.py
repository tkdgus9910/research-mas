"""그림 공통 설정 — 한글 폰트·마이너스 기호·기본 스타일. report 스테이지 첫 줄에서 setup_matplotlib()."""
from __future__ import annotations

import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

_KOREAN_FONTS = ["NanumGothic", "NanumBarunGothic", "Malgun Gothic", "AppleGothic", "Noto Sans CJK KR", "Noto Sans KR"]


def setup_matplotlib(dpi: int = 200) -> str | None:
    """설치된 한글 폰트를 찾아 적용. 없으면 None 을 돌려주고 글리프 경고만 끈다 (그림은 나온다)."""
    installed = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((f for f in _KOREAN_FONTS if f in installed), None)
    if chosen:
        plt.rcParams["font.family"] = chosen
    else:
        warnings.filterwarnings("ignore", message=".*Glyph.*missing from font.*")
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = dpi
    plt.rcParams["savefig.dpi"] = dpi
    plt.rcParams["savefig.bbox"] = "tight"
    return chosen
