"""Export the trusted wiki as ordinary HTML, without Docker or the game."""

import argparse
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import yaml

from challenge_content import Catalog, CONTENT_ROOT
from wiki_content import WikiCatalog


STYLE = """/* Readable local lessons; no external fonts or network resources. */
:root { color-scheme: light; font-family: system-ui, sans-serif; color: #24362e;
  background: #f4f3e9; font-size: 18px; line-height: 1.65; }
* { box-sizing: border-box; }
body { margin: 0; }
header { background: #203c31; color: #fffdf0; padding: 1rem max(1.1rem, calc((100% - 56rem) / 2)); }
header a { color: inherit; text-decoration: none; font-weight: 750; letter-spacing: .06em; }
header span { display: block; opacity: .8; font-size: .8rem; }
main { max-width: 56rem; margin: auto; padding: 1.4rem 1.2rem 3rem; }
nav { display: flex; gap: .7rem 1.1rem; flex-wrap: wrap; margin-bottom: 1.7rem; font-size: .9rem; }
a { color: #17694c; text-underline-offset: .18em; }
a:hover { color: #094732; }
a:focus-visible, button:focus-visible { outline: 3px solid #ad6b12; outline-offset: 4px; }
h1, h2, h3 { color: #203c31; line-height: 1.25; }
h1 { font-size: clamp(1.8rem, 5vw, 2.8rem); margin-top: 0; letter-spacing: -.025em; }
h2 { margin-top: 2rem; font-size: 1.5rem; }
h3 { font-size: 1.15rem; }
p, ul, ol, pre, figure { margin: 1rem 0; }
li { margin: .35rem 0; }
.eyebrow { text-transform: uppercase; letter-spacing: .12em; font-size: .75rem; font-weight: 750; }
.intro { font-size: 1.08rem; max-width: 42rem; }
.cards { list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr)); gap: 1rem; }
.cards li { border: 1px solid #d2d9cc; border-radius: .45rem; padding: 1.1rem 1.3rem; background: #fffef6; }
.cards h2 { font-size: 1.35rem; margin: 0 0 .5rem; }
.cards p { margin: 0; color: #526456; font-size: .9rem; }
code, pre { font-family: ui-monospace, Consolas, monospace; font-size: .9em; }
code { background: #e5e9dc; padding: .1em .3em; border-radius: .2em; }
pre { background: #1c3029; color: #f6f6df; padding: 1.1rem; border-radius: .4rem; overflow-x: auto; line-height: 1.65; }
pre code { background: transparent; color: inherit; padding: 0; font-size: 1em; }
img, svg, video { max-width: 100%; height: auto; }
figure { background: #fffef6; border: 1px solid #d2d9cc; padding: 1rem; border-radius: .4rem; }
figcaption, .muted { color: #526456; font-size: .88rem; }
table { border-collapse: collapse; width: 100%; font-size: .92rem; }
th, td { border: 1px solid #cdd6c6; text-align: left; padding: .6rem; }
th { background: #e5e9dc; }
aside, .note { border-left: 4px solid #a47822; padding: .5rem 1rem; background: #ecebdc; margin: 1.5rem 0; }
.related { margin-top: 2.5rem; padding-top: .2rem; border-top: 1px solid #cdd6c6; }
.instructions { white-space: pre-wrap; overflow-wrap: anywhere; }
footer { max-width: 56rem; margin: auto; padding: 0 1.2rem 2rem; color: #526456; font-size: .8rem; }
@media (max-width: 40rem) { :root { font-size: 16px; } main { padding: 1rem 1.1rem 2rem; } }
"""

NAVIGATION = r"""// Ordinary links and the parent fallback work without JavaScript/storage.
const root = new URL('.', window.location.href).href;
const key = 'nixboy-wiki:' + root;
function localPage(value) {
  if (typeof value !== 'string' || !value) return false;
  try {
    const url = new URL(value, window.location.href);
    return new URL('.', url).href === root &&
      /\/(index|(?:subject|article|challenge)-[a-z][a-z0-9_.-]*)\.html$/.test(url.pathname);
  } catch (_) { return false; }
}
// Remember only followed wiki links, so an external browser visit never becomes Back.
try {
  const pending = JSON.parse(sessionStorage.getItem(key) || 'null');
  sessionStorage.removeItem(key);
  if (pending && pending.target === window.location.href && localPage(pending.source)) {
    history.replaceState({...history.state, nixboyWikiPrevious: pending.source}, '');
  }
} catch (_) { /* File viewers may disable storage; use the referrer or parent. */ }
document.addEventListener('click', (event) => {
  const link = event.target.closest('a[href]');
  if (!link || event.defaultPrevented || event.button !== 0 ||
      event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  if (link.id === 'wiki-back') {
    const previous = history.state && history.state.nixboyWikiPrevious;
    if (history.length > 1 && (localPage(previous) || localPage(document.referrer))) {
      event.preventDefault();
      history.back();
    }
    return;
  }
  if (localPage(link.href) && !link.target && !link.hasAttribute('download')) {
    try { sessionStorage.setItem(key, JSON.stringify({source: location.href, target: link.href})); }
    catch (_) { /* Ordinary navigation still works. */ }
  }
});
"""


def _link(kind, identifier, label):
    return f'<a href="{kind}-{identifier}.html">{escape(label)}</a>'


def _page(title, body, parent="index.html", subject=None):
    subject_link = "" if subject is None else " " + _link("subject", subject.id, subject.title)
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} | nix-boy wiki</title>
<link rel="stylesheet" href="wiki.css">
<script src="navigation.js" defer></script>
</head>
<body>
<header><a href="index.html">nix-boy</a><span>Linux field wiki</span></header>
<main>
<nav aria-label="Wiki navigation"><a id="wiki-back" href="{escape(parent, quote=True)}">&#8592; Back</a>
<a href="index.html">Home</a>{subject_link}</nav>
{body}
</main>
<footer>Learning reference &middot; Challenges run through the nix-boy training hub.</footer>
</body>
</html>
'''


class _Fragment(HTMLParser):
    """Rewrite declarative URLs while retaining trusted authored HTML."""

    def __init__(self, wiki, article, assets):
        super().__init__(convert_charrefs=False)
        self.wiki = wiki
        self.article = article
        self.assets = assets
        self.parts = []

    def _url(self, tag, name, value):
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if name == "href" and scheme in {"wiki", "challenge"}:
            kind = "article" if scheme == "wiki" else "challenge"
            path = f"{kind}-{unquote(parsed.path)}.html"
            return urlunsplit(("", "", path, "", parsed.fragment))
        if tag == "img" and name == "src":
            source = (self.wiki.root / self.article.file).parent / unquote(parsed.path)
            source = source.resolve()
            # Validate again at export time, including symlinks and changed files.
            if parsed.scheme or parsed.netloc or not source.is_relative_to(self.wiki.root) \
                    or not source.is_file():
                raise ValueError(f"invalid local wiki image: {value}")
            target = "wiki-assets/" + source.relative_to(self.wiki.root).as_posix()
            self.assets[target] = source
            return urlunsplit(("", "", quote(target, safe="/"), parsed.query, parsed.fragment))
        return value

    def _tag(self, tag, attrs, ending):
        rendered = []
        for name, value in attrs:
            rendered.append(name if value is None else
                            f'{name}="{escape(self._url(tag, name, value), quote=True)}"')
        self.parts.append("<" + tag + (" " + " ".join(rendered) if rendered else "") + ending)

    def handle_starttag(self, tag, attrs):
        self._tag(tag, attrs, ">")

    def handle_startendtag(self, tag, attrs):
        self._tag(tag, attrs, " />")

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def handle_comment(self, data):
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.parts.append(f"<!{decl}>")


def export_wiki(content_dir, output):
    """Validate first, then export into a new or empty directory. Never replace files."""
    challenges = Catalog(content_dir)
    wiki = WikiCatalog(challenges.root / "wiki", challenges)
    requested = Path(output)
    if requested.is_symlink():
        raise ValueError("output must not be a symbolic link")
    output = requested.resolve()
    if output.is_relative_to(challenges.root) or challenges.root.is_relative_to(output):
        raise ValueError("output must be separate from the content directory")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("output must be a new or empty directory")

    pages = {}
    assets = {}
    cards = []
    for subject in wiki.subjects.values():
        count = len(subject.articles)
        cards.append(f'<li><h2>{_link("subject", subject.id, subject.title)}</h2>'
                     f'<p>{count} article{"s" if count != 1 else ""}</p></li>')
        links = "".join(f'<li>{_link("article", identifier, wiki.get(identifier).title)}</li>'
                        for identifier in subject.articles)
        pages[f"subject-{subject.id}.html"] = _page(subject.title,
            f'<p class="eyebrow">Subject</p><h1>{escape(subject.title)}</h1><ul>{links}</ul>')
    pages["index.html"] = _page("Linux field wiki",
        '<p class="eyebrow">Read. Try. Understand.</p><h1>Linux field wiki</h1>'
        '<p class="intro">Explore Linux by subject. Each article connects ideas, examples and '
        'related practice, independently of your character\'s skills.</p>'
        '<ul class="cards">' + "".join(cards) + '</ul>')

    for article in wiki.articles.values():
        subject = wiki.subject(article.subject)
        fragment = _Fragment(wiki, article, assets)
        fragment.feed(article.html)
        fragment.close()
        related = wiki.challenges_for(article.id)
        links = "".join(f'<li>{_link("challenge", item.id, item.title)}</li>' for item in related)
        related_html = f"<ul>{links}</ul>" if related else '<p class="muted">No linked challenges yet.</p>'
        pages[f"article-{article.id}.html"] = _page(article.title,
            f'<p class="eyebrow">{escape(subject.title)}</p><h1>{escape(article.title)}</h1>'
            '<article>' + "".join(fragment.parts) + '</article>'
            '<section class="related"><h2>Related challenges</h2>' + related_html + '</section>',
            f"subject-{subject.id}.html", subject)

    for challenge in challenges.challenges.values():
        readings = "".join(f'<li>{_link("article", identifier, wiki.get(identifier).title)}</li>'
                           for identifier in challenge.wiki)
        track = challenges.tracks[challenge.skill]
        band = track["levels"][challenge.level]
        parent = f"article-{challenge.wiki[0]}.html" if challenge.wiki else "index.html"
        pages[f"challenge-{challenge.id}.html"] = _page(challenge.title,
            f'<p class="eyebrow">Practice reference</p><h1>{escape(challenge.title)}</h1>'
            f'<p class="muted">{escape(track["title"])} &middot; {escape(band["title"])}</p>'
            f'<p class="instructions">{escape(challenge.instructions)}</p>'
            '<p class="note">Open the nix-boy training hub to select a challenge. '
            'The hub fills placeholders for your attempt and checks your work.</p>'
            '<section class="related"><h2>Suggested reading</h2>'
            + (f'<ul>{readings}</ul>' if readings else '<p class="muted">No reading links yet.</p>')
            + '</section>', parent)

    output.mkdir(parents=True, exist_ok=True)
    for name, value in {"wiki.css": STYLE, "navigation.js": NAVIGATION, **pages}.items():
        with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
    for target, source in assets.items():
        destination = output / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            outgoing.write(incoming.read())
    return {"status": "exported", "output": str(output), "subjects": len(wiki.subjects),
            "articles": len(wiki.articles), "challenges": len(challenges.challenges), "assets": len(assets)}


def main():
    parser = argparse.ArgumentParser(description="Export the nix-boy wiki for local HTML preview")
    parser.add_argument("--content-dir", type=Path, default=CONTENT_ROOT)
    parser.add_argument("--output", type=Path, required=True, help="new or empty output directory")
    args = parser.parse_args()
    try:
        result = export_wiki(args.content_dir, args.output)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        parser.exit(1, f"Cannot export wiki: {exc}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
