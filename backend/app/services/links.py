"""Video links out of a document or pasted text (Library "Import links").

A document is a zipped office file (docx, xlsx, pptx, odt: every XML part is read) or anything readable
as text (txt, csv, md, html, rtf). A link counts when it points at one video on a known site; the same
video linked twice (other tracking query, youtu.be vs watch?v=) is one link.
"""

import html
import io
import re
import zipfile
from collections import Counter
from urllib.parse import parse_qs, urlsplit

MAX_UNZIPPED = 100 * 1024**2  # across the XML parts of one document

# a URL ends at whitespace, a quote, a bracket or the start of the next URL (links pasted back to back)
URL = re.compile(r"https?://(?:(?!https?://)[^\s<>\"'\\{}|^`])+", re.I)
ATTR_URL = re.compile(r'(?:Target|href)\s*=\s*"(https?://[^"\s]+)"', re.I)
FIELD = re.compile(r"<w:instrText[^>]*>([^<]*)</w:instrText>")  # Word's other way to keep a link: HYPERLINK "url"
BLOCK_END = re.compile(r"</(?:w:p|a:p|text:p|text:h|si|c|p|div|li|td|tr|h\d)>|<(?:w:br|w:tab|text:line-break|br)\b[^>]*>", re.I)
TAG = re.compile(r"<[^>]+>")
HTML = re.compile(r"<(?:html|body|a|p|div|br)\b", re.I)

# (platform, host suffixes, the video id in "host/path?query")
SITES = [
    ("YouTube", ("youtube.com", "youtu.be"), r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]{6,})"),
    ("Instagram", ("instagram.com",), r"/(?:reels?|p|tv)/([\w-]+)"),
    ("TikTok", ("tiktok.com",), r"/video/(\d+)|^v[mt]\.tiktok\.com/([\w-]+)|/t/([\w-]+)"),
    ("X", ("x.com", "twitter.com"), r"/status/(\d+)"),
    ("Facebook", ("facebook.com", "fb.watch"), r"[?&]v=(\d+)|/(?:reel|videos)/(\d+)|^fb\.watch/([\w-]+)|/share/[rv]/([\w-]+)"),
]


def _markup(xml: str) -> tuple[str, str]:
    hidden = "\n".join(ATTR_URL.findall(xml) + FIELD.findall(xml))
    # tags go without a trace: Word splits one typed URL over several runs
    body = TAG.sub("", BLOCK_END.sub("\n", FIELD.sub("", xml)))
    return html.unescape(body), html.unescape(hidden)


def read(data: bytes) -> tuple[str, str]:
    """-> (the text a reader sees, the links kept out of sight: hyperlink targets and field codes).
    A link usually sits in both, which is why find() takes them apart."""
    if zipfile.is_zipfile(io.BytesIO(data)):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                budget, parts = MAX_UNZIPPED, []
                for m in z.infolist():
                    if m.filename.endswith((".xml", ".rels")) and m.file_size <= budget:
                        budget -= m.file_size
                        parts.append(z.read(m).decode("utf-8", "replace"))
            return _markup("\n".join(parts))
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):  # damaged or encrypted: read it as text
            pass
    text = data.decode("utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", "replace")
    return _markup(text) if HTML.search(text) else (text, "")


def clean(url: str) -> str:
    """No tracking query and no fragment; ?v= stays (on YouTube and Facebook it is the video)."""
    u = urlsplit(url)
    v = parse_qs(u.query).get("v")
    return f"{u.scheme.lower()}://{u.netloc.lower()}{u.path}" + (f"?v={v[0]}" if v else "")


def video(url: str) -> tuple[str, str] | None:
    """(platform, key) when the URL is one video on a known site; the key is the same for every way of
    linking that video."""
    u = urlsplit(url)
    host = re.sub(r"^(?:www|m|mobile)\.", "", (u.hostname or "").lower())
    for platform, hosts, pattern in SITES:
        if any(host == h or host.endswith("." + h) for h in hosts):
            m = re.search(pattern, f"{host}{u.path}" + (f"?{u.query}" if u.query else ""))
            return (platform, f"{platform}:{next(g for g in m.groups() if g)}") if m else None
    return None


def key(url: str) -> str:
    """video()'s key, or the cleaned URL for a site it doesn't know."""
    found = video(url)
    return found[1] if found else clean(url).rstrip("/")


def find(body: str, hidden: str = "") -> tuple[list[tuple[str, str, str]], int, list[str]]:
    """-> ([(platform, key, url)] in document order, how many more times those videos were linked, the
    other links). A link present in body and hidden counts once."""
    first: dict[str, tuple[str, str]] = {}
    counts, other = [], []
    for source in (body, hidden):
        n: Counter[str] = Counter()
        for raw in URL.findall(source):
            url = clean(raw.rstrip(".,;:!?)]*"))
            if (found := video(url)) is None:
                if url not in other:
                    other.append(url)
                continue
            n[found[1]] += 1
            first.setdefault(found[1], (found[0], url))
        counts.append(n)
    linked = sum(max(n[k] for n in counts) for k in first)
    return [(platform, k, url) for k, (platform, url) in first.items()], linked - len(first), other
