"""Renderer-independent Linux wiki catalog and declarative cross-reference checks."""

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit

import yaml

CONTENT_ROOT = Path(__file__).resolve().parent / "content"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
MAX_INDEX_BYTES = 65536
MAX_ARTICLE_BYTES = 512 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024


class WikiLoader(yaml.SafeLoader):
    """Keep duplicate YAML keys from silently replacing navigation entries."""


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise ValueError(f"duplicate YAML key: {key}")
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as exc:
            raise ValueError("invalid YAML mapping key") from exc
    return result


WikiLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class Subject:
    id: str
    title: str
    articles: tuple


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    subject: str
    file: str
    html: str
    stylesheets: tuple = ()


def _text(value, maximum=200):
    return isinstance(value, str) and 0 < len(value) <= maximum and "\0" not in value


def _identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def _read_text(path, maximum):
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f"wiki file exceeds {maximum} bytes: {path}")
    text = data.decode("utf-8")
    if "\0" in text:
        raise ValueError(f"NUL in wiki file: {path}")
    return text


class _References(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag == "img" and not any(name == "src" for name, _ in attrs):
            self.images.append(None)
        for name, value in attrs:
            if name == "href" and value is not None:
                self.links.append(value)
            if tag == "img" and name == "src":
                self.images.append(value)

    handle_startendtag = handle_starttag


class WikiCatalog:
    """Load ordered navigation and HTML fragments, independent of skill tracks.

    ``challenges`` is a challenge_content.Catalog. Backlinks come only from its
    Challenge.wiki IDs; a link inside an article never creates a second backlink
    list. Authored HTML is trusted local content, not sanitized browser input.
    """

    def __init__(self, root, challenges):
        self.root = Path(root).resolve()
        self.challenges = challenges
        index = self._local_file("index.yaml")
        raw = yaml.load(_read_text(index, MAX_INDEX_BYTES), Loader=WikiLoader)
        if not isinstance(raw, dict) or set(raw) != {"schema", "subjects"} \
                or type(raw["schema"]) is not int or raw["schema"] != 1 \
                or not isinstance(raw["subjects"], list) or not 1 <= len(raw["subjects"]) <= 128:
            raise ValueError("wiki index needs schema 1 and between one and 128 subjects")
        self.subjects = {}
        self.articles = {}
        self._checked_stylesheets = set()
        asset_paths = {}
        identifiers = set()
        references = {}
        for subject in raw["subjects"]:
            if not isinstance(subject, dict) or set(subject) != {"id", "title", "articles"} \
                    or not _identifier(subject["id"]) or subject["id"] in identifiers \
                    or not _text(subject["title"]) or not isinstance(subject["articles"], list) \
                    or not 1 <= len(subject["articles"]) <= 2048:
                raise ValueError("invalid or duplicate wiki subject identity/title/articles")
            identifiers.add(subject["id"])
            article_ids = []
            for article in subject["articles"]:
                if not isinstance(article, dict) or not {"id", "title", "file"} <= set(article) \
                        or set(article) - {"id", "title", "file", "stylesheets"} \
                        or not _identifier(article["id"]) or article["id"] in identifiers \
                        or not _text(article["title"]):
                    raise ValueError("invalid or duplicate wiki article identity/title")
                path = self._local_file(article["file"])
                if path.suffix.lower() not in {".html", ".htm"}:
                    raise ValueError(f"wiki article must be an HTML file: {article['id']}")
                html = _read_text(path, MAX_ARTICLE_BYTES)
                stylesheets = article.get("stylesheets", [])
                if not isinstance(stylesheets, list) or len(stylesheets) > 8 \
                        or any(not _text(item, 1024) for item in stylesheets) \
                        or len(set(stylesheets)) != len(stylesheets):
                    raise ValueError("invalid local wiki stylesheets")
                for stylesheet in stylesheets:
                    self._stylesheet(self._local_file(stylesheet), asset_paths, set())
                parser = _References()
                parser.feed(html)
                parser.close()
                for source in parser.images:
                    asset = self._image_file(source, (self.root / article["file"]).parent)
                    asset_paths[asset] = None
                spec = Article(article["id"], article["title"], subject["id"], article["file"], html,
                               tuple(stylesheets))
                self.articles[spec.id] = spec
                references[spec.id] = parser.links
                article_ids.append(spec.id)
                identifiers.add(spec.id)
            self.subjects[subject["id"]] = Subject(subject["id"], subject["title"], tuple(article_ids))
        if len(self.articles) > 2048:
            raise ValueError("wiki exceeds 2048 articles")
        self.asset_paths = tuple(asset_paths)
        for identifier, links in references.items():
            for href in links:
                link = urlsplit(href)
                if link.scheme.lower() not in {"wiki", "challenge"}:
                    continue
                target = unquote(link.path)
                lookup = self.articles if link.scheme.lower() == "wiki" else challenges.challenges
                if link.netloc or link.query or not _identifier(target) or target not in lookup:
                    raise ValueError(f"unresolved {link.scheme} reference in {identifier}: {href}")
        related = {identifier: [] for identifier in self.articles}
        for challenge in challenges.challenges.values():
            article_ids = getattr(challenge, "wiki", ())
            if not isinstance(article_ids, (list, tuple)) or any(not _identifier(item) for item in article_ids) \
                    or len(set(article_ids)) != len(article_ids):
                raise ValueError(f"invalid wiki references in challenge: {challenge.id}")
            for identifier in article_ids:
                if not _identifier(identifier) or identifier not in self.articles:
                    raise ValueError(f"unresolved wiki reference in challenge {challenge.id}: {identifier}")
                related[identifier].append(challenge)
        self._related = {identifier: tuple(items) for identifier, items in related.items()}

    def _local_file(self, name, base=None):
        if not _text(name, 1024) or "\\" in name or ":" in name or "?" in name or "#" in name \
                or PurePosixPath(name).is_absolute():
            raise ValueError(f"wiki file must be a relative local path: {name}")
        path = ((base if base is not None else self.root) / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"wiki file escapes its root: {name}")
        if not path.is_file():
            raise ValueError(f"missing wiki file: {name}")
        return path

    def _image_file(self, source, base):
        if not isinstance(source, str):
            raise ValueError("wiki image needs a local src")
        url = urlsplit(source)
        if url.scheme or url.netloc:
            raise ValueError(f"wiki image needs a local src: {source}")
        path = self._local_file(unquote(url.path), base)
        if path.stat().st_size > MAX_ASSET_BYTES:
            raise ValueError(f"wiki asset exceeds {MAX_ASSET_BYTES} bytes: {source}")
        return path

    def _stylesheet(self, path, assets, active):
        """Follow reviewed local CSS imports/url assets; never fetch resources.

        This is a bounded dependency walker, not an arbitrary CSS sanitizer.
        Escaped/remote resource spellings are deliberately outside its contract.
        Original relative paths are retained in export, so nested URLs still work.
        """
        if path.suffix.lower() != ".css":
            raise ValueError("wiki stylesheet must be a local CSS file")
        if path in active:
            raise ValueError("cyclic wiki stylesheet import")
        if path in self._checked_stylesheets:
            return
        if len(assets) >= 256:
            raise ValueError("wiki stylesheet asset graph exceeds 256 files")
        css = _read_text(path, MAX_ARTICLE_BYTES)
        assets[path] = None
        active.add(path)
        # Comments cannot create dependencies. All url(...) references and
        # quoted @imports are then checked against the same root/size policy.
        stripped = re.sub(r"/\*[\s\S]*?\*/", "", css)
        sources = re.findall(r"url\(\s*['\"]?([^)'\"]+?)['\"]?\s*\)", stripped, re.I)
        sources += re.findall(r"@import\s+['\"]([^'\"]+)['\"]", stripped, re.I)
        for source in sources:
            asset = self._image_file(source.strip(), path.parent)
            if asset.suffix.lower() == ".css":
                self._stylesheet(asset, assets, active)
            else:
                if asset.suffix.lower() not in {".svg", ".png", ".jpg", ".jpeg", ".webp"}:
                    raise ValueError("unsupported local wiki CSS asset")
                assets[asset] = None
                if len(assets) > 256:
                    raise ValueError("wiki stylesheet asset graph exceeds 256 files")
        active.remove(path)
        self._checked_stylesheets.add(path)

    def get(self, identifier):
        try:
            return self.articles[identifier]
        except (KeyError, TypeError) as exc:
            raise ValueError("unknown wiki article id") from exc

    def subject(self, identifier):
        try:
            return self.subjects[identifier]
        except (KeyError, TypeError) as exc:
            raise ValueError("unknown wiki subject id") from exc

    def challenges_for(self, identifier):
        self.get(identifier)
        return self._related[identifier]


def main():
    parser = argparse.ArgumentParser(description="Validate wiki navigation and challenge cross-references")
    parser.add_argument("--content-dir", type=Path, default=CONTENT_ROOT)
    args = parser.parse_args()
    # The challenge loader may import this module to validate references itself.
    from challenge_content import Catalog
    try:
        challenges = Catalog(args.content_dir)
        wiki = WikiCatalog(args.content_dir / "wiki", challenges)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        parser.exit(1, f"Invalid wiki content: {exc}\n")
    print(json.dumps({"status": "valid", "root": str(wiki.root), "subjects": len(wiki.subjects),
                      "articles": len(wiki.articles), "challenges": len(challenges.challenges)}))


if __name__ == "__main__":
    main()
