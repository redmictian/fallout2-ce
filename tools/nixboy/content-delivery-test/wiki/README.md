# Imported learner reading

This small reading collection adapts the supplied 29 October PR06 package into
the existing nix-boy wiki. It contains the two authored PR06 lessons, their two
guided practices, and the explicitly labeled nginx preparation outline. It does
not claim a complete nginx lesson set, course integration or SA67 certification.

Stable article IDs are `course.pr061`, `course.practice-pr061`, `course.pr062`,
`course.practice-pr062` and `course.nginx-preparation`. References between these
pages use the normal `wiki:` IDs; the reader supplies Home/Back/subject navigation.
References outside this small collection are descriptive text, not broken local
links or a route to assessor files. External documentation URLs remain readable
references; the explanations work offline.

`import_course_lessons.py` reproduces the import from the pinned source archive:

```sh
python tools/nixboy/import_course_lessons.py /path/to/pr06-nginx-focused-revision-2026-10-29.zip --output /new/empty/wiki
```

The archive identity and selected input hashes are in `import-provenance.json`.
The fixed allowlist excludes assessment cards, assessor answers, plans, review
records and historical archives. Public worked examples and formative disclosures
belong to the lessons and remain available; assessment keys and postcommit
explanations are separate trusted backend material and are never imported.

The original CSS is scoped to `.course-lesson`, with inline style attributes
externalized to the local stylesheet. Diagrams remain native inline SVG, tables
retain their overflow wrappers, and disclosures use native `details`/`summary`.
No script is imported. The optional arithmetic slider is disabled, with its
original explicit static cases retained below it, so there is no falsely active
control or dependency on JavaScript. The wiki content-security policy is unchanged.

The schema's optional per-article `stylesheets` list names root-relative local CSS.
The exporter follows bounded local CSS `@import` and `url(...)` image dependencies
and preserves their directory hierarchy. Remote resources, root escapes, import
cycles, unsupported asset types and oversize files reject export. This is a
dependency walker for reviewed authored CSS, not a general HTML/CSS sanitizer.
