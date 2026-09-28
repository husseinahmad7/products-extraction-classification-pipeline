"""Check the static Pages artifact for broken local links and duplicate anchors."""

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.duplicates: set[str] = set()
        self.urls: list[str] = []
        self.has_language = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html" and values.get("lang"):
            self.has_language = True
        anchor = values.get("id")
        if anchor:
            if anchor in self.ids:
                self.duplicates.add(anchor)
            self.ids.add(anchor)
        if tag in {"a", "link", "script", "img"}:
            url = values.get("href") or values.get("src")
            if url:
                self.urls.append(url)


def main() -> None:
    site = Path(__file__).resolve().parents[2] / "site"
    document = Document()
    document.feed((site / "index.html").read_text(encoding="utf-8"))
    failures = [f"duplicate anchor: {anchor}" for anchor in sorted(document.duplicates)]
    if not document.has_language:
        failures.append("root html element needs a language")
    for url in document.urls:
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            continue
        if parsed.path and not (site / unquote(parsed.path)).is_file():
            failures.append(f"missing local file: {url}")
        if parsed.fragment and not parsed.path and unquote(parsed.fragment) not in document.ids:
            failures.append(f"missing anchor: {url}")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Validated {len(document.ids)} anchors and {len(document.urls)} links.")


if __name__ == "__main__":
    main()
