---
title: Broken frontmatter
tags: [grammar
date: not-a-date: really
  - unclosed sequence

# Malformed frontmatter

Frozen Phase-C fixture. Do not edit.

The block above opens with `---` and never closes, and its second line is not
valid YAML either. Indexing this file must not raise: the body still carries the
word thunderstruck, which is unique in this vault and therefore provable, and
the note is flagged as having unparseable frontmatter rather than dropped.

日本語も少し混ぜておく: 単語 は本文にある。
