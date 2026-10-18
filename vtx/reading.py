"""Safe article HTML and readable clipboard text from the same Markdown source."""
from html.parser import HTMLParser

from markdown_it import MarkdownIt


def render_markdown(markdown: str) -> str:
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    return renderer.render(markdown)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.lists = []
        self.in_pre = False

    def line(self):
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("ul", "ol"):
            self.lists.append(int(attrs.get("start", "1")) if tag == "ol" else None)
        if tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "tr", "li"):
            self.line()
        if tag == "li":
            number = self.lists[-1] if self.lists else None
            self.parts.append(f"{number}. " if number is not None else "• ")
            if number is not None:
                self.lists[-1] += 1
        if tag == "br":
            self.parts.append("\n")
        if tag == "pre":
            self.in_pre = True
        if tag == "img":
            self.parts.append(attrs.get("alt", ""))

    def handle_endtag(self, tag):
        if tag in ("ul", "ol"):
            self.lists.pop()
        if tag in ("td", "th"):
            self.parts.append("\t")
        if tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "tr", "li"):
            self.line()
        if tag == "pre":
            self.in_pre = False

    def handle_data(self, data):
        if self.in_pre or data.strip():
            self.parts.append(data)


def plain_text(markdown: str) -> str:
    parser = _Text()
    parser.feed(render_markdown(markdown))
    return "\n".join(line.rstrip() for line in "".join(parser.parts).splitlines()).strip()
