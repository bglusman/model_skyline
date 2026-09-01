from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING_PAGE = ROOT / "pages" / "index.html"

REQUIRED_LINKS = {
    "models-dev-gpt5/latest.json",
    "models-dev-gpt5-evidence/latest.json",
    ("models-dev-gpt5/frontiers/price-snapshot-cost-per-solved-vs-solve-rate/table.txt"),
    ("models-dev-gpt5/frontiers/price-snapshot-cost-per-attempted-vs-solve-rate/table.txt"),
    ("models-dev-gpt5/frontiers/price-snapshot-reconstructed-token-cost-vs-solve-rate/table.txt"),
    "models-dev-gpt5/feeds/price-snapshot-cost-per-solved-vs-solve-rate.xml",
    "models-dev-gpt5/feeds/price-snapshot-cost-per-attempted-vs-solve-rate.xml",
    "models-dev-gpt5/feeds/price-snapshot-reconstructed-token-cost-vs-solve-rate.xml",
    "data/frontiers/cost-per-solved-vs-solve-rate/table.txt",
    "data/frontiers/total-cost-vs-solve-rate/table.txt",
    "data/frontiers/agent-edit-seconds-vs-solve-rate/table.txt",
    "data/latest.json",
}


class _LandingPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.hrefs: set[str] = set()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        for name, value in attrs:
            if value is None:
                continue
            if name == "href":
                self.hrefs.add(value)
            elif name == "src":
                self.sources.append(value)


def test_pages_landing_is_small_static_navigation() -> None:
    content = LANDING_PAGE.read_text(encoding="utf-8")
    assert len(content.encode()) < 16_384
    assert "Provider routes are unknown" in content
    assert "Static aliases do not enforce freshness" in content
    assert "not safe to consume as an automatic model default" in content

    parser = _LandingPageParser()
    parser.feed(content)

    assert parser.hrefs >= REQUIRED_LINKS
    assert not ({"script", "form", "iframe", "frame", "object", "embed"} & parser.tags)
    assert parser.sources == []
    assert all(not href.lower().startswith("javascript:") for href in parser.hrefs)
    assert all(
        not href.startswith(("http://", "https://"))
        or href == "https://github.com/bglusman/model_skyline"
        for href in parser.hrefs
    )
