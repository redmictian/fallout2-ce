# Wiki content

This directory owns learning articles. `../challenges/` owns exercises and
`../tracks.yaml` owns Fallout progression. A wiki subject is a Linux topic;
it does not select an engine skill, level, reward, or challenge pool.

`index.yaml` is the ordered navigation configuration:

```yaml
schema: 1
subjects:
  - id: filesystem
    title: Files and access
    articles:
      - id: linux.permissions
        title: Understanding permissions
        file: articles/permissions.html
```

Choose short, stable IDs. Moving `linux.permissions` to another subject,
renaming its title/file, or moving a related challenge between tracks must
not change the article ID. Every article belongs to one subject in this index.
IDs use lowercase letters, digits, dots, underscores and hyphens; start with
a letter and use at most 64 characters.

Article files are UTF-8 HTML fragments, without a document wrapper. The page
wrapper supplies the article title as `h1`. Start with introductory paragraphs,
then use `h2` sections and `h3` subsections, lists, tables and `pre`/`code` for structure.
Escape `<`, `>` and `&` in code examples. Use descriptive image alternative
text. Keep illustrations in `assets/` and use paths relative to the HTML file,
such as `../assets/permissions.svg`. The included SVG is hand-authored and has
no external dependencies. Content is trusted installed mod material; do not
insert external resources or content from learners. Generated pages enforce a
Content Security Policy: resources and the bundled JavaScript/CSS must come from
the local wiki, with network connections, frames, workers, embedded objects and
form submissions blocked. Inline scripts, event handlers and inline styles are
blocked. Future interactive lessons must use reviewed local JavaScript files
and export support; arbitrary authored scripts are not supported by this version.

Link by identity, never by skill folder or filename:

```html
<a href="wiki:linux.permissions">Read about permissions</a>
<a href="challenge:unarmed.permissions.private-file">Try a private-file task</a>
```

A contextual challenge link is prose navigation, not the association registry.
To associate an exercise, add the optional `wiki` list to its challenge YAML:

```yaml
wiki: [linux.paths, linux.permissions]
```

That list is authoritative. The wiki derives related exercises from those
references; do not author a second reverse list in the index or article.
The challenge ID still identifies that exercise wherever its file is stored.
Use an existing exact challenge ID in a contextual link. Articles remain
independent of challenge availability and character progression.

From the source checkout root, using the bridge's Python environment, validate
both catalogs and export the static HTML reader:

```sh
python tools/nixboy/challenge_content.py
python tools/nixboy/wiki_content.py
python tools/nixboy/wiki_pages.py --output ../out/wiki-export
```

The export output directory must be new or empty. In this workspace the existing
WSL Python at `/home/mictian/build/nixboy-venv/bin/python` has PyYAML; the packager
does not invoke Python. The checker checks duplicate
IDs, missing content, and unresolved article/challenge references; it does not
verify the teaching material or run example commands. The export contains
`index.html`, subject/article/challenge pages, local styles/navigation and images,
plus `wiki-export.json` with source and generated-file hashes. Keep the manifest
with its export. Packaging accepts `-WikiDirectory` and rejects a stale export,
modified file or incomplete file set.

Deploy the complete export as `nixboy/wiki/` beside the game. The reader's default
`[wiki] home=nixboy/wiki/index.html` points there; Ctrl+W opens it from gameplay
or Pip-Boy, and Escape closes it. Windows WebView2 renders these local pages
without a Python service, Docker or the terminal bridge. Home, subjects, links
and Back work within the reader. HTML preview in an ordinary browser remains
useful for authoring but does not establish in-game rendering.

Editing raw `nixboy/content/wiki/` does not update the displayed pages until
you export and deploy again. Close the reader first and preserve local edits
before replacing an installed export or content directory. Copy only to the
mod's disposable installation. The mod workspace's `docs/wiki-design.md`
records the renderer decision and the tested deployment.

The examples were checked against the GNU manuals for
[Bash tilde expansion](https://www.gnu.org/software/bash/manual/html_node/Tilde-Expansion.html),
[pipelines](https://www.gnu.org/software/bash/manual/html_node/Pipelines.html),
[redirections](https://www.gnu.org/software/bash/manual/html_node/Redirections.html),
and [ordinary file mode bits](https://www.gnu.org/software/coreutils/manual/html_node/Mode-Structure.html).
