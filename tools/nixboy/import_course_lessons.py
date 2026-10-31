"""Import the reviewed learner-only PR06 package into the existing local wiki.

Fixed allowlist and pinned input identity prevent accidentally publishing
assessment/assessor material. This is an adapter for the supplied package, not a
general web scraper or new renderer. No resources are downloaded or executed.
"""
import argparse
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import zipfile

import yaml


PACKAGE_SHA256 = "370fa90c24acb4308f2441bdacaef9f717a38c5bdc5dc23cd8e1cf223c6fdf2e"
LESSONS = {
    "pr061.html": ("course.pr061", "PR06.1 · What is consuming the resource?"),
    "practice-pr061.html": ("course.practice-pr061", "Guided practice · Observe CPU, memory and capacity"),
    "pr062.html": ("course.pr062", "PR06.2 · Count, sample and investigate"),
    "practice-pr062.html": ("course.practice-pr062", "Guided practice · Measure a permitted correction"),
    "web-server-preparation.html": ("course.nginx-preparation", "nginx preparation outline and comparison"),
}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


@dataclass
class Element:
    tag: str
    attrs: list = field(default_factory=list)
    children: list = field(default_factory=list)


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.root = Element("document")
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Element(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def handle_entityref(self, name):
        self.handle_data("&" + name + ";")

    def handle_charref(self, name):
        self.handle_data("&#" + name + ";")


def descendants(node):
    if isinstance(node, Element):
        yield node
        for child in node.children:
            yield from descendants(child)


def scoped_css(css):
    """Scope this reviewed flat-rule/media stylesheet to the imported article."""
    css = re.sub(r"/\*[\s\S]*?\*/", "", css)
    result, cursor = [], 0
    while cursor < len(css):
        opening = css.find("{", cursor)
        if opening < 0:
            break
        header = css[cursor:opening].strip()
        depth, end = 1, opening + 1
        while depth and end < len(css):
            depth += (css[end] == "{") - (css[end] == "}")
            end += 1
        if depth:
            raise ValueError("unbalanced reviewed lesson stylesheet")
        body = css[opening + 1:end - 1]
        if header.startswith("@"):
            if not header.startswith(("@media", "@supports")):
                raise ValueError("unsupported lesson stylesheet at-rule")
            result.append(header + "{" + scoped_css(body) + "}")
        else:
            selectors = []
            for selector in header.split(","):
                selector = re.sub(r"\bmain\b", ".lesson-main", selector.strip())
                selector = re.sub(r"(?<![-\w.])(?:html|body)(?![-\w])|:root", ".course-lesson", selector)
                if not selector.startswith(".course-lesson"):
                    selector = ".course-lesson " + selector
                selectors.append(selector)
            result.append(",".join(selectors) + "{" + body + "}")
        cursor = end
    return "\n".join(result) + "\n"


def adapt_document(html, inline_styles):
    document = Document(html)
    body = next(node for node in descendants(document.root) if node.tag == "body")
    main = next(node for node in descendants(body) if node.tag == "main")
    hero = next((node for node in body.children if isinstance(node, Element) and
                 "hero" in dict(node.attrs).get("class", "").split()), None)

    def render(node):
        if isinstance(node, str):
            return node
        if node.tag in {"script", "style", "iframe", "object", "embed"}:
            return ""
        tag, attrs = node.tag, dict(node.attrs)
        children = "".join(render(child) for child in node.children)
        if tag == "main":
            tag = "div"
            attrs["class"] = (attrs.get("class", "") + " lesson-main").strip()
        if tag == "a" and "href" in attrs:
            link = urlsplit(attrs["href"])
            if not link.scheme and not link.netloc and not link.path:
                pass  # Local article anchor.
            elif not link.scheme and not link.netloc and link.path in LESSONS:
                attrs["href"] = "wiki:" + LESSONS[link.path][0] + ("#" + link.fragment if link.fragment else "")
            elif not link.scheme and not link.netloc and link.path == "index.html":
                # The exported reader supplies its own Home/Back/subject route.
                return children
            elif link.scheme in {"https", "http"}:
                return '<span class="reference">' + children + " [" + escape(attrs["href"]) + "]</span>"
            else:
                # Omitted practice/source/assessment documents are not copied or
                # left as broken links. Their descriptive text remains honest.
                return '<span class="reference">' + children + " (reference outside this reading demo)</span>"
        for name in tuple(attrs):
            if name.lower().startswith("on"):
                raise ValueError("event handlers are not allowed in imported lessons")
        style = attrs.pop("style", None)
        if style:
            key = "source-style-" + hashlib.sha256(style.encode()).hexdigest()[:12]
            inline_styles[key] = style
            attrs["class"] = (attrs.get("class", "") + " " + key).strip()
        if tag == "input":
            # The optional arithmetic slider has explicit worked cases below it.
            # Keep that fallback, never display a falsely live nonworking input.
            attrs["disabled"] = None
            attrs["title"] = "Static model: use the explicit cases below."
        encoded = "".join(" " + key if value is None else
                          f' {key}="{escape(value, quote=True)}"' for key, value in attrs.items())
        return "<" + tag + encoded + ">" + ("" if tag in VOID else children + "</" + tag + ">")

    body_class = dict(body.attrs).get("class", "")
    contents = (render(hero) if hero else "") + render(main)
    return '<div class="course-lesson"><div class="' + escape(body_class, quote=True) + '">' + contents + "</div></div>\n"


def import_lessons(package, output):
    package, output = Path(package), Path(output)
    if hashlib.sha256(package.read_bytes()).hexdigest() != PACKAGE_SHA256:
        raise ValueError("unexpected lesson package identity; review before importing a different revision")
    if output.exists() and any(output.iterdir()):
        raise ValueError("lesson import output must be new or empty")
    if output.is_symlink():
        raise ValueError("lesson import output must not be a symlink")
    inline_styles, imported, hashes = {}, {}, {}
    with zipfile.ZipFile(package) as archive:
        for name in LESSONS:
            raw = archive.read(name)
            hashes[name] = hashlib.sha256(raw).hexdigest()
            imported[name] = adapt_document(raw.decode("utf-8-sig"), inline_styles)
        css = archive.read("pilot.css")
        hashes["pilot.css"] = hashlib.sha256(css).hexdigest()
    scoped = scoped_css(css.decode("utf-8-sig"))
    for key, value in sorted(inline_styles.items()):
        scoped += ".course-lesson ." + key + "{" + value + "}\n"
    scoped += ".course-lesson .reference{overflow-wrap:anywhere}.course-lesson input:disabled{opacity:.65}\n"
    (output / "articles").mkdir(parents=True, exist_ok=True)
    (output / "assets").mkdir()
    (output / "assets/course-lessons.css").write_text(scoped, encoding="utf-8", newline="\n")
    articles = []
    for name, (identifier, title) in LESSONS.items():
        (output / "articles" / name).write_text(imported[name], encoding="utf-8", newline="\n")
        articles.append({"id": identifier, "title": title, "file": "articles/" + name,
                         "stylesheets": ["assets/course-lessons.css"]})
    index = {"schema": 1, "subjects": [{"id": "course.resources", "title": "Resource investigation", "articles": articles[:4]},
                                      {"id": "course.services", "title": "nginx service preparation", "articles": articles[4:]}]}
    (output / "index.yaml").write_text(yaml.safe_dump(index, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    provenance = {"schema": 1, "package_sha256": PACKAGE_SHA256, "selected_source_sha256": hashes,
                  "article_ids": [value[0] for value in LESSONS.values()],
                  "excluded": "All assessment, assessor, planning, verification and historical files; optional JS not imported",
                  "adaptations": ["body lesson/hero only", "scoped local CSS", "inline styles externalized",
                                  "local lesson links use stable wiki IDs", "omitted/external links rendered as references",
                                  "arithmetic slider disabled; original explicit static cases retained"]}
    (output / "import-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(import_lessons(args.package, args.output), indent=2))


if __name__ == "__main__":
    main()
