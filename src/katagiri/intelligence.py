"""D2: vocabulary coverage, the curriculum grammar DAG, and i+1 selection.

Why this module exists
---------------------
Two questions decide what the learner is served next, and until now both were
answered by guessing. *How much of this text do I already know?* — and *is the
grammar in it reachable from what I have actually studied?* D-28 is the standing
decision that i+1 selection is never allowed to answer the second question with
the first one: a sentence at 100% vocabulary coverage whose grammar sits three
unlearned nodes away is not comprehensible input, it is a wall. So coverage and
reachability are computed from two independent sources and combined only in
:func:`find_i_plus_one`, which is the only place in this module allowed to say
"serve this".

:func:`coverage` measures a text against the **real** ``known_set`` view — via
:mod:`katagiri.known`, the one sanctioned read path, so alias redirects, manual
marks and the Anki maturity rule all apply exactly as they do everywhere else.
:func:`import_curriculum` turns ``10-course/curriculum.md`` into ``item`` rows of
kind ``grammar`` plus ``item_edge`` rows, which is the graph reachability will be
walked over.

What counts as a token (the coverage basis)
------------------------------------------
A percentage is meaningless without its denominator, so the denominator is
spelled out here rather than left implicit in a query:

* **Ignored entirely** — punctuation, symbols and whitespace
  (:data:`IGNORED_POS1`). Never in the numerator, never in the denominator.
  Counting 。as an unknown word would make every long sentence look harder.
* **Function morphs** — particles and auxiliary verbs (:data:`FUNCTION_POS1`) are
  counted *separately* and excluded from the ratio. They are grammar, and grammar
  is what the DAG half of this module is for; folding は and です into a
  *vocabulary* percentage would push every real text under the ``<80`` band and
  double-count the same difficulty on both sides of the i+1 gate.
* **Everything else is content** and is in the denominator, bound affixes
  included (接頭辞 / 接尾辞). お茶 and 食べ方 are words the learner has to know,
  and dropping their affixes would quietly shrink the basis.

The ratio is over **tokens, not types** — the shape ``coverage_cache`` records
(``total_tokens`` / ``known_tokens``) and the shape a comprehensibility band
means. Types matter for the *unknown list*, which is ranked by in-text frequency
and carries the cumulative percentage each one would buy, so "learn these 15 to
reach 92%" is a number and not a slogan.

How a morph is looked up in the known set
-----------------------------------------
UniDic hands back a lemma, a surface and a katakana lemma reading; ``item`` rows
carry ``kanji`` and ``reading``. Three keys are tried, in order, first hit wins,
and the result records **which** key hit so a miss is diagnosable instead of
mysterious:

1. the **lemma** (食べる) — UniDic's dictionary form, which is what an item row's
   ``kanji`` normally is;
2. the **surface** (食べ), for the words whose lemma UniDic spells differently
   from the vault;
3. the **lemma reading folded to hiragana** — but **only for a lemma written
   without kanji**. Resolving a kanji word by its reading alone is how 飴 gets
   marked known because 雨 is: a homophone match inflates coverage silently, and
   an inflated coverage number is worse than a missing one. Kana words carry that
   ambiguity in the text itself, so nothing is lost by allowing it there.

An ambiguous surface (``known_word`` returning candidates instead of a verdict)
counts as **not known** and is reported in its own bucket. Guessing which 明日
the learner meant is exactly the quiet wrong answer this project cannot afford.

How curriculum.md maps to rows
------------------------------
``curriculum.md`` holds the graph in two shapes, and both are read:

* **Node blocks** — a fenced block declaring ``id:`` with optional ``prereqs:``,
  ``unlocks:`` and ``level:``. This is the authoritative shape (the file
  documents it under "Node format"), and the parser reuses
  :func:`katagiri.md_search.parse_frontmatter` by wrapping each block in ``---``
  delimiters rather than growing a second tolerant key/value parser.
* **Arrow diagrams** — the ASCII graphs the phase sections are actually written
  in (``A ──> B``, with ``└──>`` branches aligned under their parent). Today they
  are the only real edges in the file, so refusing to read them would mean the
  feature works on a synthetic fixture and not on the learner's own vault. It is
  a convenience source and says so: every edge in the result names the source and
  line that produced it, and a branch marker whose parent cannot be established
  by column is **reported as skipped, never guessed at**.

The section that *documents* the node format is not data:
:data:`FORMAT_ONLY_HEADINGS` names it, the block under it is skipped, and the
skip is reported — so the day a real node block is written there, it is visible
that it was ignored.

Edge direction is fixed by the schema's index. ``item_edge_to_idx`` is on
``(to_id, edge_type)``, so "what must come before X" has to be
``WHERE to_id = X``: both edge types therefore point **earlier → later**.
``prereqs: [A]`` on node B stores ``A → B`` as ``prereq``; ``unlocks: [C]`` on B
stores ``B → C`` as ``unlock``; a diagram arrow ``A ──> B`` stores ``A → B`` as
``prereq``, because the file's own opening line is about declaring prerequisites.

Idempotency, and what the import will not do
--------------------------------------------
Re-running over an unchanged file writes nothing: item rows upsert with
``COALESCE(existing, new)`` so a curated ``understanding`` or ``level`` is never
overwritten by a stub, and edges are inserted ``ON CONFLICT DO NOTHING`` against
their own primary key. Every id referenced by an edge gets an item row — a
prerequisite mentioned but not yet described becomes a stub, because a DAG with
missing endpoints is not a DAG (and ``item_edge`` has real foreign keys).

``item`` and ``item_edge`` are **source-of-truth** tables, so this import is
additive: an edge deleted from ``curriculum.md`` is *reported* as an orphan and
left alone. Drop-and-rebuild is the derived-table rule and applying it here would
delete the learner's hand-made edges along with the file's.

Three more per-node tags (T028, D-39) — ``jf_can_do``, ``irodori_lesson`` and
``tae_kim_section``, free-text external references to the JF can-do catalogue,
an Irodori lesson, and a Tae Kim's Guide section — land on the same node ids
without a schema change: no new table, and no new column on ``item`` either.
A migration adding columns was considered and rejected: this project's whole-
schema-in-one-migration rule (D-12/D-27) only carries a scoped exception for
migration 0002 (D-38), ``tests/test_db.py::test_packaged_migrations_are_discoverable``
pins the packaged set to versions ``[1, 2]``, and every other free-looking
``item`` column (``home_topic``, ``lexeme_ref``, ``jmdict_seq``, ...) is
already load-bearing elsewhere (grammar reachability, the dictionary linker) —
so writing a tag into one would risk exactly the gate-corruption D-39 rules
out. Instead these three tags are rows in ``settings`` (``scope`` = the node
id, ``key`` = ``curriculum_<tag>``) — a table the schema already describes as
"global with per-topic overrides", which is exactly what a grammar node's id
is, and one no gate or reachability computation reads. Unlike ``level``,
curriculum.md is the *only* author of these three tags, so a re-import
overwrites the stored value rather than protecting it with ``COALESCE`` —
there is no hand-curated value elsewhere to lose. Removal keeps the same
shape as the edge doctrine above: a tag present in ``settings`` but absent
from a re-import is reported as an orphan attribute and left in place, never
deleted.

A cycle is refused, whole. The union of parsed edges and the edges already in the
database is topologically sorted first, and a cycle comes back as
:data:`CURRICULUM_CYCLE` with the cycle named — reachability over a cyclic
"DAG" is not a slow answer, it is a wrong one, and a half-imported graph is worse
than none.

The i+1 gate: what "reachable" means, exactly
--------------------------------------------
:func:`find_i_plus_one` offers a candidate only when **both** halves pass, and
the grammar half is spelled out here because "reachable" is the word that decides
whether D-28 is actually implemented or merely quoted:

* Reachability is walked over **``prereq`` edges only.** ``unlock`` is an
  availability signal — "this became available once you had that" — and walking
  it would let a node be reached through a door that never claimed to be a
  requirement. Unlock edges are still read and reported (``unlock_ready``), so
  the signal is visible; it is simply never an input to the verdict.
* A node is **mastered** when the real ``known_set`` says so, *or* when its
  ``item.understanding`` self-rating is at least
  :data:`DEFAULT_MIN_UNDERSTANDING`. Both are allowed because grammar points are
  rarely Anki cards — ``understanding`` is the column the schema provides for
  exactly this ("an input to selection"), and requiring a mature card would make
  every grammar point permanently unreachable.
* A grammar point ``g`` is **reachable** when every node in its *transitive*
  prereq closure, excluding ``g`` itself, is mastered. ``g`` itself may be
  unmastered — that is the **+1**. One unmastered *prerequisite*, however deep,
  makes ``g`` unreachable.
* A candidate passes the grammar gate when every grammar point on it is
  reachable **and** at most :data:`DEFAULT_MAX_NEW_GRAMMAR` of them are new. Two
  new grammar points in one sentence is i+2, whatever its coverage says.
* A candidate whose grammar points cannot be established is gated out by default
  (:data:`GATE_GRAMMAR_UNKNOWN`) — nothing annotated, an id with no ``item`` row,
  or an id naming something that is not a grammar point, all three. "No grammar
  information" is not "reachable grammar", and letting it through would be
  deciding on vocabulary alone, which is the one thing D-28 forbids.
  ``require_grammar=False`` opts out explicitly, and the result says so.

Which grammar points a candidate carries comes from three places, first hit
winning, and the answer records which: an explicit annotation on the candidate;
``prereq`` edges pointing *at* the candidate's item id (a sentence needing a
grammar point is exactly ``g-… → s-…`` in the shape ``item_edge_to_idx``
indexes); or the item's ``home_topic`` when that is a grammar slug. No new table
and no new edge type: the schema already expresses this.

``sealed`` items are never offered, with no flag to override it (D-26 — probes
may read the canary set, drills never). ``production_eligible`` is deliberately
*not* consulted: it restricts what may be drilled *as production* on grounds of
*register*, and this function selects reading material by default.

``production=True`` is the opt-in that finally consumes the reading/production
distinction this function otherwise leaves alone: A0 **production** drills may
draw only from the audio-anchored pool (D-38 — ``item.audio_source IS NOT
NULL``), and an item marked ``text_only`` is withheld from it even when
anchored, because "was recorded" and "is fit to produce from" are different
claims. An unanchored or ``text_only`` candidate is gated with
:data:`GATE_NOT_AUDIO_ANCHORED` rather than dropped or replaced — no
substitution and no synthesis (no TTS; F-02 stays deferred) — so the reason is
always visible in ``gated_by``. A candidate with no stored ``item`` row (ad hoc
text offered by the caller) carries no anchor metadata to check, so it is
withheld the same way: absence of anchoring information is not evidence of
anchoring.

Comprehension debt, and how it is folded
---------------------------------------
Debt is the accumulated, still-unpaid comprehension failure on an item, and it
is what ranks the candidates that pass the gate: material that touches what the
learner owes is remediation, material that touches nothing is a pleasant waste
of a session. It is folded from the two sources the schema designates —
``observation`` rows (source-of-truth) and ``item_stat_cache.comprehension_debt``
(the derived cache over them) — and from nothing else. ``event`` rows are not
folded in: ``log_error``'s events feed the confusion graph, and counting a
mistake once as an event and again as an observation would inflate whichever
items happen to be logged twice.

One observation contributes ``signal × attribution × recency``:

* **signal** — an *assisted* attempt is debt (:data:`DEBT_ASSISTED`): the
  learner could not do it alone. An unassisted attempt whose ``produced`` differs
  from its ``expected`` is a miss (:data:`DEBT_MISS`), weighted *below* assisted
  because that comparison is a shallow string comparison over two free-text
  columns and is noise for a task type where several answers are right. An
  unassisted attempt with nothing to contradict it is **credit**
  (:data:`DEBT_CLEAN_CREDIT`, negative): debt is repaid by performing, which is
  the only thing that should repay it.
* **attribution** — the ``coverage_band`` the performance happened in decides how
  much of it belongs to the *item*. Needing help at ``>=95`` coverage is the
  item's fault; needing help at ``<80`` is mostly the input's, so blaming the
  item at full weight would manufacture debt out of hard material
  (:data:`DEBT_ATTRIBUTION`). Credit runs the other way — succeeding unassisted
  inside ``<80`` input is the stronger evidence (:data:`CREDIT_ATTRIBUTION`).
* **recency** — exponential decay with a :data:`DEBT_HALF_LIFE_DAYS` half-life.
  Debt is a present-tense question; a struggle from six months ago that never
  recurred is history, not debt.

The total is clamped at zero (an item performed cleanly ten times is not owed
*to* the learner) and the cache is folded in without double counting: a cache row
whose ``computed_ts`` is at or after the item's newest observation already
accounts for them and is used as-is (decayed to now); a stale one is used as a
prior plus only the observations recorded *after* it. Which of those happened is
reported per item (``source``). ``strength`` / ``review_count`` are reported
alongside but never folded into the number — nothing writes them yet, so their
scale is undefined, and inventing one would put a fabricated unit into a ranking.

Difficulty for me: four numbers, one score, and what happens when a file is gone
-------------------------------------------------------------------------------
"How hard is this?" has no answer without "for whom", so
:func:`difficulty_for_me` combines three corpus-level measurements with one
learner-level one and reports every component next to the total. Higher is
harder, on a 0–100 scale, and the number is *reported* — it does not gate
anything and it does not reorder :func:`find_i_plus_one` (the gate is D-28's two
halves and the ranking is comprehension debt; a difficulty score quietly
promoting material would make both unreadable).

* **readability** — the Lee & Hasebe formula, as implemented by the
  ``jreadability`` package. The package is *vendored as its sdist*, not
  installed: the six coefficients are read out of the vendored source at load
  time, so the arithmetic is upstream's and its provenance is a digest in
  ``vendor/CHECKSUMS.sha256`` rather than this file's memory of a paper. The
  features (mean sentence length in tokens, % kango, % wago, % verbs, %
  particles) are recomputed here on the *vendored full UniDic* instead of the
  ``unidic-lite`` upstream defaults to, because that is the dictionary every
  other number in this project comes from; the two agree exactly on upstream's
  own README example, and where they disagree the tokenizer, not the formula, is
  the difference. Upstream's documented band range ``[0.5, 6.5)`` is the
  normalisation: 6.5 is lower-elementary and 0.5 upper-advanced, so difficulty is
  the distance from the easy end.
* **frequency** — BCCWJ short-unit frequency rank, as a percentile. Short unit
  (``suw``) and not long unit because UniDic's morphs *are* short units, so a
  lemma from :mod:`katagiri.tokenizer` and a ``lemma`` column in that list are
  the same kind of object. A lemma the list does not carry counts as the rarest
  bucket rather than being skipped — 185k lemmas from a balanced corpus is broad
  enough that absence is evidence — and the share of such misses is reported so
  a caller can see how much of the number rests on it.
* **JLPT** — the tanos level lists, per level. Here a miss is *excluded* from the
  mean instead of counted as hardest, and the opposite treatment is deliberate:
  the JLPT lists cover ~8k words on purpose, so "not in any list" says nothing
  about difficulty (every proper noun in Japanese is unlisted). Rarity of the
  unlisted words is the frequency component's job. ``listed_pct`` says how much
  of the text the level mean is speaking for.
* **coverage %** — :func:`coverage` against the real ``known_set``, which is the
  only component that is about *this* learner, and it carries the heaviest weight
  (:data:`DIFFICULTY_WEIGHTS`) for that reason.

The total is the weighted mean of the components that are **actually available**,
renormalised over their weights — so a missing dataset widens the error bars
instead of dragging the score toward zero. ``weight_used`` (the fraction of the
full weight that answered) travels with every result, because a difficulty of 55
from coverage alone and a 55 from all four are not the same claim.

Vendored data, and degrading without lying
-----------------------------------------
Each dataset has a lazy, cached loader (:func:`load_readability_model`,
:func:`load_frequency_list`, :func:`load_jlpt_levels`) and each returns a
:class:`DatasetStatus` saying what it found. Two failure modes, deliberately
different:

* **absent, ambiguous or malformed** — a value, never an exception. The vendored
  difficulty data is optional by construction (``vendor/*`` is gitignored and
  acquired by hand per D-10), so a fresh checkout must still be able to run a
  study session: the component reports ``available: False`` with a code, and the
  score is computed from what is there.
* **digest mismatch** — :class:`~katagiri.jmdict_import.ChecksumError`, raised by
  the loader exactly as ``vendor/README.md`` rule 3 requires, and caught at the
  difficulty layer so the study loop survives. The component then carries
  :data:`DATASET_CHECKSUM` with the expected and actual digests in its note.
  Loud, in the result, and the bad bytes are never read — which is the whole
  point of the rule, and is not the same thing as tolerating them.

Nothing here logs an event: the write is a graph import, the event vocabulary for
it does not exist yet (adding one is a ``docs/db-schema.md`` change, which is
T021's kind of task), and inventing a type silently would put an unexplained row
in the one true history. Selection itself writes nothing at all — it is a read,
and a tool that quietly logged what it merely *considered* would corrupt the
event log's meaning. Registration of these functions as MCP tools is T017's.

Failures are values with stable codes, in the shape ``exercises`` and
``obsidian_proxy`` use: ``{"ok": False, "error": <code>, "note": <what
happened>, ...}``. Type misuse by a programmer still raises.
"""

from __future__ import annotations

import io
import re
import sqlite3
import tarfile
import zipfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from katagiri import known
from katagiri.db import resolve_alias
from katagiri.jmdict_import import (
    ChecksumError,
    VendorFileError,
    vendor_dir,
    verify_vendor_file,
)
from katagiri.logging_setup import get_logger
from katagiri.md_search import (
    VaultNotFoundError,
    parse_frontmatter,
    vault_root,
)
from katagiri.normalizer import is_han_char, katakana_to_hiragana
from katagiri.session_tools import COVERAGE_BANDS
from katagiri.tokenizer import Morph, TokenizerError, get_tagger, tokenize

logger = get_logger("intelligence")

# ---------------------------------------------------------------------------
# The coverage basis
# ---------------------------------------------------------------------------

#: UniDic ``pos1`` values that are not language: punctuation, symbols, spaces.
#: Dropped from numerator *and* denominator. Same three values
#: :mod:`katagiri.normalizer` refuses to map to a dictionary entry.
IGNORED_POS1: Final[frozenset[str]] = frozenset({"補助記号", "記号", "空白"})

#: UniDic ``pos1`` values counted separately and kept out of the ratio: these are
#: grammar, and grammar is gated by the DAG rather than by a vocabulary
#: percentage. See the module docstring for why the line is drawn here and not
#: around bound affixes.
FUNCTION_POS1: Final[frozenset[str]] = frozenset({"助詞", "助動詞"})

#: How many unknown types :func:`coverage` returns by default. 15 because that is
#: the size the tool contract in the vault's ``90-meta`` folder promises, in
#: ``mcp-spec.md`` ("learn these 15 to reach 92%"), and the cumulative
#: percentages make that promise checkable.
DEFAULT_TOP_UNKNOWN: Final = 15
MAX_TOP_UNKNOWN: Final = 200

#: Longest text :func:`coverage` will tokenize. A whole episode transcript is
#: fine; a pasted novel is a different tool with a cache behind it.
MAX_COVERAGE_CHARS: Final = 100_000

#: Per-type verdicts in the ``unknown`` list.
STATE_UNKNOWN: Final = "unknown"        # item row exists, known_set says no
STATE_UNSEEN: Final = "unseen"          # no item row at all — never studied
STATE_AMBIGUOUS: Final = "ambiguous"    # surface matched several items

#: Which lookup key produced a hit, reported per type so a miss is diagnosable.
KEY_LEMMA: Final = "lemma"
KEY_SURFACE: Final = "surface"
KEY_READING: Final = "reading"

# ---------------------------------------------------------------------------
# Curriculum
# ---------------------------------------------------------------------------

#: Vault-relative location of the curriculum. Vault-relative, like
#: ``exercises.CANARY_VAULT_PATH``: the vault is what the learner owns, and the
#: copy under ``docs/`` is the same file seen from the repository.
CURRICULUM_VAULT_PATH: Final = "10-course/curriculum.md"

#: Grammar ids are slugs (``g-wa-topic``), per ``docs/db-schema.md`` — they match
#: vault filenames directly, and a rename is recorded in ``alias`` rather than by
#: rewriting references. Anything else in a prereq list is skipped and reported.
GRAMMAR_ID_PREFIX: Final = "g-"
GRAMMAR_ID_RE: Final = re.compile(r"^g-[0-9a-z][0-9a-z-]*$")
#: The same shape, for finding ids inside a diagram line.
_ID_IN_LINE_RE: Final = re.compile(r"g-[0-9a-z][0-9a-z-]*")

EDGE_PREREQ: Final = "prereq"
EDGE_UNLOCK: Final = "unlock"
EDGE_TYPES: Final = (EDGE_PREREQ, EDGE_UNLOCK)

GRAMMAR_KIND: Final = "grammar"

#: Headings whose fenced blocks document the node format instead of declaring
#: nodes. Skipped, and the skip is reported.
FORMAT_ONLY_HEADINGS: Final[frozenset[str]] = frozenset({"node format"})

#: T028 (D-39): three free-text external-reference tags a node block may carry
#: alongside ``id``/``level``/``prereqs``/``unlocks``. Keys here are the
#: curriculum.md YAML keys; values are the ``settings`` key each is stored
#: under (``scope`` is the node id) — see ``_upsert_curriculum_attrs`` for why
#: ``settings`` and not a new column.
CURRICULUM_ATTR_SETTINGS_KEYS: Final[dict[str, str]] = {
    "jf_can_do": "curriculum_jf_can_do",
    "irodori_lesson": "curriculum_irodori_lesson",
    "tae_kim_section": "curriculum_tae_kim_section",
}
_SETTINGS_KEY_TO_ATTR: Final[dict[str, str]] = {
    value: key for key, value in CURRICULUM_ATTR_SETTINGS_KEYS.items()
}

#: Sources an edge can come from, reported on every edge.
SOURCE_NODE_BLOCK: Final = "node_block"
SOURCE_DIAGRAM: Final = "diagram"

#: Arrow spellings a diagram may use. ``──>`` is what the vault actually
#: contains; the ASCII and unicode forms are accepted so a hand-typed edit works.
_ARROWS: Final = ("──>", "-->", "->", "→", "─>")
#: Box-drawing characters that continue a branch upwards (``│``) or start one
#: (``└`` / ``├`` / ``┌``). A branch's parent is the node above it whose column
#: span covers the marker's column.
_BRANCH_MARKERS: Final = "└├┌"
_CONTINUATION_MARKERS: Final = "│└├┌|"

_FENCES: Final = ("```", "~~~")
_HEADING_RE: Final = re.compile(r"^#{1,6}\s+(?P<title>.*)$")
_YAML_INFO: Final = frozenset({"", "yaml", "yml", "text", "txt"})

SENTENCE_KIND: Final = "sentence"

# ---------------------------------------------------------------------------
# Reachability and mastery
# ---------------------------------------------------------------------------

#: Lowest ``item.understanding`` self-rating that counts a grammar point as
#: mastered for reachability. 3 of 5 is "can use it with effort" — below that the
#: learner has said they cannot rely on it, and treating a 2 as a satisfied
#: prerequisite would make the gate agree with the learner's own verdict against
#: them.
DEFAULT_MIN_UNDERSTANDING: Final = 3

#: How a node came to be mastered, reported so a surprising verdict is traceable.
MASTERY_KNOWN_SET: Final = "known_set"
MASTERY_UNDERSTANDING: Final = "understanding"

#: Where a candidate's grammar points came from, first hit winning.
GRAMMAR_FROM_EXPLICIT: Final = "explicit"
GRAMMAR_FROM_EDGES: Final = "item_edge"
GRAMMAR_FROM_HOME_TOPIC: Final = "home_topic"
GRAMMAR_FROM_NOTHING: Final = "none"

# ---------------------------------------------------------------------------
# Comprehension debt
# ---------------------------------------------------------------------------

#: Half-life of one observation's contribution to debt, in days. 30 days: inside
#: a month a struggle is still the learner's present situation, and by three
#: months an unrepeated one has decayed to an eighth of its weight.
DEBT_HALF_LIFE_DAYS: Final = 30.0

#: Per-observation signals. Assisted is the primary evidence; a mismatch on an
#: unassisted attempt is weighted below it because the comparison is shallow (see
#: the module docstring); a clean unassisted attempt is negative — it repays.
DEBT_ASSISTED: Final = 1.0
DEBT_MISS: Final = 0.75
DEBT_CLEAN_CREDIT: Final = -0.5

#: How much of a performance belongs to the *item* rather than to the difficulty
#: of the input it happened in. Keyed off :data:`COVERAGE_BANDS` in the schema's
#: own order (``>=95``, ``80-95``, ``<80``) rather than re-spelling the band
#: strings, so a band rename cannot leave these dicts silently stale.
DEBT_ATTRIBUTION: Final[dict[str, float]] = dict(
    zip(COVERAGE_BANDS, (1.0, 0.75, 0.5))
)
#: The same, for credit: succeeding unassisted in hard input is stronger evidence.
CREDIT_ATTRIBUTION: Final[dict[str, float]] = dict(
    zip(COVERAGE_BANDS, (0.5, 0.75, 1.0))
)

#: Which sources produced an item's debt figure.
DEBT_FROM_OBSERVATIONS: Final = "observations"
DEBT_FROM_CACHE: Final = "cache"
DEBT_FROM_CACHE_TAIL: Final = "cache+observations"
DEBT_FROM_NOTHING: Final = "none"

# ---------------------------------------------------------------------------
# The i+1 gate
# ---------------------------------------------------------------------------

#: How many accepted candidates :func:`find_i_plus_one` returns by default. Ten
#: is a session's worth of material to choose from, not a dashboard.
DEFAULT_TOP_CANDIDATES: Final = 10
MAX_TOP_CANDIDATES: Final = 200

#: How many candidates may be *offered* to one call, and how many the built-in
#: loader reads when the caller offers none.
MAX_CANDIDATES: Final = 500
DEFAULT_CANDIDATE_LIMIT: Final = 200

#: Longest single candidate. A candidate is a sentence or a short passage; a
#: chapter belongs in :func:`coverage`, which has its own much larger limit.
MAX_CANDIDATE_CHARS: Final = 2_000

#: The gate's defaults. ``<80`` is the schema's own "not comprehensible" band, so
#: 80% is the floor; one unknown word type and one new grammar point are the two
#: halves of the ``+1``.
DEFAULT_MIN_COVERAGE_PCT: Final = 80.0
DEFAULT_MAX_UNKNOWN_TYPES: Final = 1
DEFAULT_MAX_NEW_GRAMMAR: Final = 1

#: How many unknown types are reported per accepted candidate. Small: the point
#: here is *which* word is the +1, not a study list (that is :func:`coverage`).
DEFAULT_CANDIDATE_TOP_UNKNOWN: Final = 5

#: What ranks the survivors. Named in the result so T015b can add a mode without
#: a caller having to guess which order it got.
RANKED_BY_DEBT: Final = "comprehension_debt"

#: Why a candidate was gated out. Every failing reason is reported, not just the
#: first: "it also has three unknown words" is worth knowing.
GATE_UNREACHABLE_GRAMMAR: Final = "unreachable_grammar"
GATE_GRAMMAR_UNKNOWN: Final = "grammar_unknown"
GATE_TOO_MUCH_NEW_GRAMMAR: Final = "too_much_new_grammar"
GATE_COVERAGE_TOO_LOW: Final = "coverage_too_low"
GATE_TOO_MANY_UNKNOWN: Final = "too_many_unknown_types"
#: Withheld from an A0 **production** pool (``find_i_plus_one(production=True)``)
#: for lacking an audio anchor, or for carrying ``text_only=1`` despite one —
#: spec.md FR-018's own wording, kept verbatim (unlike its snake_case siblings)
#: because it is also T025's independent test string.
GATE_NOT_AUDIO_ANCHORED: Final = "text-only-not-for-A0-production"
GATE_NO_CONTENT: Final = "no_content_tokens"
GATE_SEALED: Final = "sealed"

# ---------------------------------------------------------------------------
# Failure codes
# ---------------------------------------------------------------------------

EMPTY_TEXT: Final = "empty_text"
TEXT_TOO_LARGE: Final = "text_too_large"
TOKENIZER_UNAVAILABLE: Final = "tokenizer_unavailable"
CURRICULUM_UNAVAILABLE: Final = "curriculum_unavailable"
CURRICULUM_EMPTY: Final = "curriculum_has_no_nodes"
CURRICULUM_CYCLE: Final = "curriculum_cycle"
BAD_TOP_UNKNOWN: Final = "bad_top_unknown"
NO_CANDIDATES: Final = "no_candidates"
TOO_MANY_CANDIDATES: Final = "too_many_candidates"
BAD_LIMIT: Final = "bad_limit"
BAD_GATE: Final = "bad_gate"
#: The *stored* graph has a cycle — distinct from :data:`CURRICULUM_CYCLE`, which
#: means the file would introduce one. Conflating them would hide which of the two
#: needs fixing.
GRAMMAR_DAG_CYCLE: Final = "grammar_dag_cycle"

TOKENIZER_UNAVAILABLE_NOTE: Final = (
    "Coverage is measured on the vendored UniDic dictionary, which could not be "
    "loaded, so no percentage is offered rather than one computed by a different "
    "tokenizer. See vendor/README.md; 'python -m katagiri.tokenizer verify' says "
    "which part is missing."
)


class IntelligenceError(RuntimeError):
    """Base class for every failure this module raises."""


class CurriculumParseError(IntelligenceError):
    """The curriculum file could not be read or decoded."""


def _utc_now() -> str:
    """ISO-8601 UTC to whole seconds, the format every CHECK in the schema wants."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Known-set lookup
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the known set says about one morph type.

    ``matched_by`` is the lookup key that hit (:data:`KEY_LEMMA`,
    :data:`KEY_SURFACE`, :data:`KEY_READING`) or ``None`` when nothing did.
    ``item_id`` is the id the verdict belongs to, after alias resolution.
    """

    is_known: bool
    state: str | None          # None when known; a STATE_* when not
    item_id: str | None
    matched_by: str | None
    suspect: bool
    redirected: bool


_KNOWN_VERDICT: Final = Verdict(
    is_known=True, state=None, item_id=None, matched_by=None,
    suspect=False, redirected=False,
)


class KnownLookup:
    """Cached ``known_set`` lookups for morphs, keyed by the tried keys.

    One instance per pass over a text — or per *batch* of candidate sentences,
    which is why it is a public class rather than a dict inside
    :func:`coverage`: T015a scores many candidates against the same known set and
    must not re-query 明日 once per sentence.

    Reads go through :mod:`katagiri.known`, never straight at the view, so alias
    redirects and manual-mark precedence are the same here as everywhere else.
    """

    __slots__ = ("_conn", "_cache", "queries")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._cache: dict[tuple[tuple[str, str], ...], Verdict] = {}
        #: How many ``known_word`` calls were actually issued. Cache-effectiveness
        #: is worth being able to assert on rather than assume.
        self.queries = 0

    def verdict(self, morph: Morph) -> Verdict:
        """The known-set verdict for ``morph``, computed once per key tuple."""
        keys = self.lookup_keys(morph)
        cached = self._cache.get(keys)
        if cached is not None:
            return cached
        result = self._resolve(keys)
        self._cache[keys] = result
        return result

    @staticmethod
    def lookup_keys(morph: Morph) -> tuple[tuple[str, str], ...]:
        """``(which, value)`` pairs to try, in order, deduplicated by value.

        The label travels with the value rather than being recovered from the
        position: when a word's surface *is* its lemma the list is shorter, and a
        positional label would then report a lemma hit as a surface hit.

        The reading key is offered **only for a kana-written lemma** — see the
        module docstring on why a kanji word is never resolved by its reading.
        """
        candidates: list[tuple[str, str]] = [
            (KEY_LEMMA, morph.lemma.strip()),
            (KEY_SURFACE, morph.surface.strip()),
        ]
        if morph.lemma_reading and not any(is_han_char(ch) for ch in morph.lemma):
            candidates.append(
                (KEY_READING, katakana_to_hiragana(morph.lemma_reading).strip())
            )
        keys: list[tuple[str, str]] = []
        values: set[str] = set()
        for which, value in candidates:
            if value and value not in values:
                values.add(value)
                keys.append((which, value))
        return tuple(keys)

    def _resolve(self, keys: Sequence[tuple[str, str]]) -> Verdict:
        ambiguous: Verdict | None = None
        seen: Verdict | None = None
        for which, key in keys:
            self.queries += 1
            answer = known.known_word(self._conn, key)
            if answer.get("ambiguous"):
                # Remembered, not returned yet: a later key may still give a
                # single honest verdict, and only then is the ambiguity moot.
                ambiguous = ambiguous or Verdict(
                    is_known=False,
                    state=STATE_AMBIGUOUS,
                    item_id=None,
                    matched_by=which,
                    suspect=False,
                    redirected=bool(answer.get("redirected")),
                )
                continue
            if not answer.get("found"):
                continue
            if answer.get("is_known"):
                return Verdict(
                    is_known=True,
                    state=None,
                    item_id=answer.get("item_id"),
                    matched_by=which,
                    suspect=bool(answer.get("suspect")),
                    redirected=bool(answer.get("redirected")),
                )
            seen = seen or Verdict(
                is_known=False,
                state=STATE_UNKNOWN,
                item_id=answer.get("item_id"),
                matched_by=which,
                suspect=bool(answer.get("suspect")),
                redirected=bool(answer.get("redirected")),
            )
        if seen is not None:
            return seen
        if ambiguous is not None:
            return ambiguous
        return Verdict(
            is_known=False,
            state=STATE_UNSEEN,
            item_id=None,
            matched_by=None,
            suspect=False,
            redirected=False,
        )


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def is_ignored(morph: Morph) -> bool:
    """Punctuation, symbols, whitespace — outside the basis entirely."""
    return (morph.pos1 in IGNORED_POS1) or not morph.surface.strip()


def is_function_morph(morph: Morph) -> bool:
    """A particle or auxiliary: counted, but not in the vocabulary ratio."""
    return morph.pos1 in FUNCTION_POS1


def is_content_morph(morph: Morph) -> bool:
    """In the denominator: not ignored, not a function morph."""
    return not is_ignored(morph) and not is_function_morph(morph)


def type_key(morph: Morph) -> tuple[str, str | None]:
    """The unit the unknown list counts: lemma plus coarse POS.

    POS is part of the key because 明日 as a noun and 明日 as an adverb are two
    entries in the vault, and collapsing them would report one of them as covered
    on the strength of the other.
    """
    return (morph.lemma, morph.pos1)


def coverage_band(known_pct: float | None) -> str | None:
    """The ``observation.coverage_band`` value for a percentage, or ``None``.

    Bands are the schema's three (:data:`COVERAGE_BANDS`, imported from
    ``session_tools`` rather than copied a third time — the value has to be
    exactly what ``log_observations`` accepts). Computed from the *unrounded*
    ratio, so 94.996% is ``80-95`` and not ``>=95``.
    """
    if known_pct is None:
        return None
    if known_pct >= 95:
        return COVERAGE_BANDS[0]
    if known_pct >= 80:
        return COVERAGE_BANDS[1]
    return COVERAGE_BANDS[2]


def _unknown_entry(
    key: tuple[str, str | None],
    *,
    occurrences: int,
    verdict: Verdict,
    reading: str | None,
    surface: str,
) -> dict[str, Any]:
    lemma, pos = key
    return {
        "lemma": lemma,
        "pos": pos,
        "reading": reading,
        "surface": surface,
        "occurrences": occurrences,
        "state": verdict.state,
        "item_id": verdict.item_id,
        "matched_by": verdict.matched_by,
    }


def coverage_from_morphs(
    conn: sqlite3.Connection,
    morphs: Iterable[Morph],
    *,
    top_unknown: int = DEFAULT_TOP_UNKNOWN,
    lookup: KnownLookup | None = None,
) -> dict[str, Any]:
    """Coverage for an already-tokenized text. The measurement itself.

    Split out from :func:`coverage` so a caller that already holds morphs —
    T015a, scoring many candidate sentences — pays for tokenization and for the
    :class:`KnownLookup` cache once instead of per candidate.

    Returns ``{"ok": True, "known_pct", "known_ratio", "band", "counts",
    "types", "unknown", "unknown_types", "note"}``. ``known_pct`` and ``band``
    are ``None`` when the text has no countable content token — the honest answer
    to "what percentage of nothing" is not zero.
    """
    if not isinstance(top_unknown, int) or isinstance(top_unknown, bool):
        raise TypeError("top_unknown must be an int.")
    if top_unknown < 0 or top_unknown > MAX_TOP_UNKNOWN:
        return {
            "ok": False,
            "error": BAD_TOP_UNKNOWN,
            "note": f"top_unknown must be between 0 and {MAX_TOP_UNKNOWN}.",
        }

    resolver = lookup if lookup is not None else KnownLookup(conn)

    total = ignored = function = 0
    counted_tokens = 0
    known_tokens = 0
    occurrences: Counter[tuple[str, str | None]] = Counter()
    first_seen: dict[tuple[str, str | None], Morph] = {}

    for morph in morphs:
        total += 1
        if is_ignored(morph):
            ignored += 1
            continue
        if is_function_morph(morph):
            function += 1
            continue
        counted_tokens += 1
        key = type_key(morph)
        occurrences[key] += 1
        first_seen.setdefault(key, morph)

    verdicts = {key: resolver.verdict(morph) for key, morph in first_seen.items()}

    unknown_types: list[tuple[tuple[str, str | None], int]] = []
    by_state: Counter[str] = Counter()
    for key, count in occurrences.items():
        verdict = verdicts[key]
        if verdict.is_known:
            known_tokens += count
            if verdict.suspect:
                by_state["known_suspect"] += count
            continue
        by_state[verdict.state or STATE_UNSEEN] += count
        unknown_types.append((key, count))

    # Frequency first, then lemma, so two types with equal counts come back in a
    # stable order — a ranked list that reshuffles between calls is unusable as
    # a study list.
    unknown_types.sort(key=lambda pair: (-pair[1], pair[0][0], pair[0][1] or ""))

    ratio = (known_tokens / counted_tokens) if counted_tokens else None
    pct = (ratio * 100) if ratio is not None else None

    unknown: list[dict[str, Any]] = []
    running = known_tokens
    for key, count in unknown_types[:top_unknown]:
        morph = first_seen[key]
        entry = _unknown_entry(
            key,
            occurrences=count,
            verdict=verdicts[key],
            reading=morph.lemma_reading,
            surface=morph.surface,
        )
        running += count
        # "Learn these N to reach X%": the coverage this text would have if every
        # type up to and including this one were known.
        entry["cumulative_pct"] = (
            round(running / counted_tokens * 100, 2) if counted_tokens else None
        )
        unknown.append(entry)

    result: dict[str, Any] = {
        "ok": True,
        "known_pct": round(pct, 2) if pct is not None else None,
        "known_ratio": round(ratio, 6) if ratio is not None else None,
        "band": coverage_band(pct),
        "counts": {
            "morphs": total,
            "counted_tokens": counted_tokens,
            "known_tokens": known_tokens,
            "unknown_tokens": counted_tokens - known_tokens,
            "function_tokens": function,
            "ignored_morphs": ignored,
            "by_state": dict(by_state),
        },
        "types": {
            "counted": len(occurrences),
            "known": len(occurrences) - len(unknown_types),
            "unknown": len(unknown_types),
        },
        "unknown": unknown,
        "unknown_types": len(unknown_types),
        "known_queries": resolver.queries,
        "note": None,
    }
    if counted_tokens == 0:
        result["note"] = (
            "no countable content token in this text (only punctuation, "
            "whitespace, particles or auxiliaries), so no percentage is offered."
        )
    return result


def coverage(
    conn: sqlite3.Connection,
    text: str,
    *,
    top_unknown: int = DEFAULT_TOP_UNKNOWN,
    lookup: KnownLookup | None = None,
) -> dict[str, Any]:
    """Known-word coverage of ``text``, measured against the real ``known_set``.

    Reads only. The result is not written to ``coverage_cache``: that table is
    keyed ``(scope_kind, scope_id)`` over media / episode / sentence / topic, and
    a pasted string is none of those — caching it under an invented scope id
    would put a row nothing can invalidate into a derived table.

    Failure values: :data:`EMPTY_TEXT`, :data:`TEXT_TOO_LARGE`,
    :data:`BAD_TOP_UNKNOWN`, :data:`TOKENIZER_UNAVAILABLE`.
    """
    if not isinstance(text, str):
        raise TypeError("coverage() takes the text as a str.")
    if not text.strip():
        return {
            "ok": False,
            "error": EMPTY_TEXT,
            "note": "coverage needs some text to measure.",
        }
    if len(text) > MAX_COVERAGE_CHARS:
        return {
            "ok": False,
            "error": TEXT_TOO_LARGE,
            "note": (
                f"{len(text)} characters is past the {MAX_COVERAGE_CHARS}-character "
                "limit for a single coverage call; measure it in parts."
            ),
        }

    try:
        morphs = tokenize(text)
    except TokenizerError as exc:
        logger.warning("coverage refused: tokenizer unavailable (%s)", exc)
        return {
            "ok": False,
            "error": TOKENIZER_UNAVAILABLE,
            "note": f"{TOKENIZER_UNAVAILABLE_NOTE} ({exc})",
        }

    result = coverage_from_morphs(
        conn, morphs, top_unknown=top_unknown, lookup=lookup
    )
    if result.get("ok"):
        result["chars"] = len(text)
    return result


# ---------------------------------------------------------------------------
# Curriculum parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DagNode:
    """One grammar node declared by a node block.

    ``jf_can_do``, ``irodori_lesson`` and ``tae_kim_section`` (T028, D-39) are
    optional free-text external-reference tags — a JF can-do id, an Irodori
    lesson reference, a Tae Kim's Guide section — each sharing ``line`` with
    the rest of the block, same as ``prereqs``/``unlocks`` already do: the
    block is one source-line reference, not one per key.
    """

    id: str
    level: str | None
    prereqs: tuple[str, ...]
    unlocks: tuple[str, ...]
    line: int
    jf_can_do: str | None = None
    irodori_lesson: str | None = None
    tae_kim_section: str | None = None


@dataclass(frozen=True, slots=True)
class DagEdge:
    """One dependency edge, with the provenance that produced it."""

    from_id: str
    to_id: str
    edge_type: str
    source: str
    line: int

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.from_id, self.to_id, self.edge_type)


@dataclass(frozen=True, slots=True)
class Curriculum:
    """The parsed graph: declared nodes, edges, and everything not understood.

    ``ids`` is every id the file mentions — a node that appears only as somebody
    else's prerequisite is still part of the graph, and ``item_edge``'s foreign
    keys mean it needs a row.
    """

    nodes: tuple[DagNode, ...]
    edges: tuple[DagEdge, ...]
    skipped: tuple[str, ...]
    attribute_only: tuple[DagNode, ...] = ()
    """Tag-only blocks (T028): ``id`` plus external-reference tags, no DAG
    structure of their own. Excluded from :attr:`nodes` so a tag-only block
    never inflates the node count of an id already declared elsewhere (e.g. by
    a diagram edge) — see :func:`_parse_node_block`."""

    @property
    def ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for node in self.nodes:
            if node.id not in seen:
                seen.append(node.id)
        for node in self.attribute_only:
            if node.id not in seen:
                seen.append(node.id)
        for edge in self.edges:
            for candidate in (edge.from_id, edge.to_id):
                if candidate not in seen:
                    seen.append(candidate)
        return tuple(seen)

    @property
    def levels(self) -> dict[str, str]:
        """id → declared level, for the nodes that declared one."""
        return {node.id: node.level for node in self.nodes if node.level}

    @property
    def attributes(self) -> dict[tuple[str, str], tuple[str, int]]:
        """``(id, attr key) -> (value, line)`` for every declared external tag.

        ``attr key`` is one of :data:`CURRICULUM_ATTR_SETTINGS_KEYS`'s keys
        (``jf_can_do``, ``irodori_lesson``, ``tae_kim_section``), not the
        ``settings`` key it is stored under — importers translate.
        """
        out: dict[tuple[str, str], tuple[str, int]] = {}
        for node in (*self.nodes, *self.attribute_only):
            for attr, value in (
                ("jf_can_do", node.jf_can_do),
                ("irodori_lesson", node.irodori_lesson),
                ("tae_kim_section", node.tae_kim_section),
            ):
                if value:
                    out[(node.id, attr)] = (value, node.line)
        return out


def _is_grammar_id(value: str) -> bool:
    return bool(GRAMMAR_ID_RE.match(value))


def _dedupe_edges(
    edges: Iterable[DagEdge], skipped: list[str]
) -> tuple[DagEdge, ...]:
    """First occurrence of each ``(from, to, type)`` wins; the rest are dropped.

    Not reported as a problem: the same edge stated by both a node block and a
    diagram is the file agreeing with itself, which is fine. A *self* edge is
    reported, because ``item_edge`` has ``CHECK (from_id <> to_id)`` and a node
    listed as its own prerequisite is a typo worth seeing.
    """
    out: dict[tuple[str, str, str], DagEdge] = {}
    for edge in edges:
        if edge.from_id == edge.to_id:
            skipped.append(
                f"line {edge.line}: {edge.from_id} is its own {edge.edge_type}; "
                "a self-edge cannot be stored."
            )
            continue
        out.setdefault(edge.key, edge)
    return tuple(out.values())


def _parse_node_block(
    body: str, *, start_line: int, skipped: list[str]
) -> tuple[list[DagNode], list[DagEdge], list[DagNode]]:
    """Parse one fenced block as one or more ``id:``-keyed node declarations.

    Documents are split on a bare ``---`` line, and each is handed to
    :func:`katagiri.md_search.parse_frontmatter` wrapped in delimiters — reusing
    the vault's own tolerant key/value parser (scalars, ``[a, b]`` inline lists,
    ``- item`` block lists, ``#`` comments) instead of writing a second one that
    would drift from it.

    Returns ``(nodes, edges, attribute_only)``. A block that declares none of
    ``level``/``prereqs``/``unlocks`` — only ``id`` plus one or more of the
    T028 external-reference tags — carries no DAG structure of its own, so it
    is not a node declaration: it lands in ``attribute_only`` instead of
    ``nodes``. This is what keeps a tag-only block from inflating the DAG's
    node count (an id already reachable only through a diagram edge, or
    through another block's ``prereqs``, stays exactly that; T028 only adds
    metadata to it, never a second, competing declaration of the node itself).
    """
    nodes: list[DagNode] = []
    edges: list[DagEdge] = []
    attribute_only: list[DagNode] = []

    offset = 0
    for chunk in re.split(r"(?m)^---[ \t]*$", body):
        chunk_line = start_line + offset
        offset += chunk.count("\n") + 1
        if not chunk.strip():
            continue
        document = chunk.strip("\n")
        fields, _ = parse_frontmatter("---\n" + document + "\n---\n")
        node_id = (fields.first("id") or "").strip()
        if not node_id:
            continue
        if not _is_grammar_id(node_id):
            skipped.append(
                f"line {chunk_line}: id {node_id!r} is not a grammar slug "
                f"({GRAMMAR_ID_PREFIX}...), so it is not a DAG node."
            )
            continue

        def edge_ids(key: str) -> list[str]:
            out: list[str] = []
            for raw in fields.fields.get(key, ()):
                value = raw.strip()
                if not value:
                    continue
                if not _is_grammar_id(value):
                    skipped.append(
                        f"line {chunk_line}: {key} entry {value!r} of {node_id} "
                        "is not a grammar slug."
                    )
                    continue
                out.append(value)
            return out

        prereqs = edge_ids("prereqs")
        unlocks = edge_ids("unlocks")
        level = fields.first("level") or None
        node = DagNode(
            id=node_id,
            level=level,
            prereqs=tuple(prereqs),
            unlocks=tuple(unlocks),
            line=chunk_line,
            jf_can_do=(fields.first("jf_can_do") or None),
            irodori_lesson=(fields.first("irodori_lesson") or None),
            tae_kim_section=(fields.first("tae_kim_section") or None),
        )
        has_tag = node.jf_can_do or node.irodori_lesson or node.tae_kim_section
        if level is None and not prereqs and not unlocks and has_tag:
            # Tag-only block (T028): see the docstring above. A bare
            # ``id:``-only block (no level, no edges, no tag either) still
            # counts as a real node — that is pre-existing behaviour this
            # change must not disturb.
            attribute_only.append(node)
        else:
            nodes.append(node)
        # Both types point earlier → later, so "what comes before X" is a
        # to_id lookup on the index the schema declares. See the docstring.
        edges.extend(
            DagEdge(prereq, node_id, EDGE_PREREQ, SOURCE_NODE_BLOCK, chunk_line)
            for prereq in prereqs
        )
        edges.extend(
            DagEdge(node_id, unlocked, EDGE_UNLOCK, SOURCE_NODE_BLOCK, chunk_line)
            for unlocked in unlocks
        )
    return nodes, edges, attribute_only


def _line_ids(line: str) -> list[tuple[int, int, str]]:
    """``(start, end, id)`` for every grammar id on one diagram line."""
    return [
        (match.start(), match.end(), match.group(0))
        for match in _ID_IN_LINE_RE.finditer(line)
        if _is_grammar_id(match.group(0))
    ]


def _has_arrow(segment: str) -> bool:
    return any(arrow in segment for arrow in _ARROWS)


def _parent_above(
    lines: Sequence[str], row: int, column: int
) -> str | None:
    """The node a branch marker at ``(row, column)`` hangs from, or ``None``.

    Walks upwards: the first line with an id whose column span covers ``column``
    is the parent; a line carrying a continuation marker at that column is passed
    through; anything else ends the search. This is how the diagram reads to a
    human, and when it does not resolve the caller reports a skip rather than
    attaching the branch to a guess.
    """
    for above in range(row - 1, -1, -1):
        line = lines[above]
        for start, end, node_id in _line_ids(line):
            if start <= column < end:
                return node_id
        char = line[column] if column < len(line) else " "
        if char in _CONTINUATION_MARKERS:
            continue
        return None
    return None


def _parse_diagram(
    lines: Sequence[str], *, start_line: int, skipped: list[str]
) -> list[DagEdge]:
    """Edges from an ASCII arrow diagram.

    Two shapes, and only two: a same-line chain ``A ──> B ──> C``, and a branch
    line ``└──> D`` whose parent is found by column. A chain link is only made
    when the text between two ids holds an arrow **and no branch marker** —
    without that second half, ``└──> g-no-possessive   └──> g-negation`` would
    read as an edge between the two branches, which is not what the picture says.
    """
    edges: list[DagEdge] = []

    for row, line in enumerate(lines):
        ids = _line_ids(line)
        if not ids:
            continue
        line_no = start_line + row

        for (_prev_start, prev_end, prev_id), (start, _end, node_id) in zip(
            ids, ids[1:]
        ):
            between = line[prev_end:start]
            if _has_arrow(between) and not any(
                marker in between for marker in _BRANCH_MARKERS
            ):
                edges.append(
                    DagEdge(prev_id, node_id, EDGE_PREREQ, SOURCE_DIAGRAM, line_no)
                )

        for column, char in enumerate(line):
            if char not in _BRANCH_MARKERS:
                continue
            target = next(
                (
                    node_id
                    for start, _end, node_id in ids
                    if start > column and _has_arrow(line[column:start])
                ),
                None,
            )
            if target is None:
                continue
            parent = _parent_above(lines, row, column)
            if parent is None:
                skipped.append(
                    f"line {line_no}: branch to {target} at column {column} has no "
                    "node above it in that column; the edge was not guessed at."
                )
                continue
            edges.append(
                DagEdge(parent, target, EDGE_PREREQ, SOURCE_DIAGRAM, line_no)
            )

    return edges


def parse_curriculum(text: str) -> Curriculum:
    """Parse curriculum markdown into nodes, edges and skips. Never raises.

    Frontmatter is stripped first (it is index metadata, not a node), then the
    body is walked fence by fence. A block declaring ``id:`` at column 0 is a
    node block; otherwise a block holding an arrow is a diagram. Blocks under a
    heading in :data:`FORMAT_ONLY_HEADINGS` are skipped — that section documents
    the shape, and importing its example would create four grammar points nobody
    is studying.
    """
    _, body = parse_frontmatter(text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = body.split("\n")

    nodes: list[DagNode] = []
    edges: list[DagEdge] = []
    skipped: list[str] = []
    attribute_only: list[DagNode] = []

    heading = ""
    fence: str | None = None
    info = ""
    block: list[str] = []
    block_start = 0

    def flush(end_heading: str) -> None:
        if not block:
            return
        content = "\n".join(block)
        if end_heading.strip().lower() in FORMAT_ONLY_HEADINGS:
            skipped.append(
                f"line {block_start}: block under {end_heading.strip()!r} skipped — "
                "that section documents the node format, it does not declare nodes."
            )
            return
        if info.strip().lower() not in _YAML_INFO:
            return
        if re.search(r"(?m)^id[ \t]*:", content):
            found_nodes, found_edges, found_attribute_only = _parse_node_block(
                content, start_line=block_start, skipped=skipped
            )
            nodes.extend(found_nodes)
            edges.extend(found_edges)
            attribute_only.extend(found_attribute_only)
            return
        if _has_arrow(content):
            edges.extend(
                _parse_diagram(block, start_line=block_start, skipped=skipped)
            )

    for index, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if fence is not None:
            if stripped.startswith(fence):
                flush(heading)
                block = []
                fence = None
                info = ""
                continue
            block.append(raw)
            continue
        opener = next((mark for mark in _FENCES if stripped.startswith(mark)), None)
        if opener is not None:
            fence = opener
            info = stripped[len(opener):]
            block = []
            block_start = index + 1
            continue
        match = _HEADING_RE.match(raw)
        if match is not None:
            heading = match.group("title")

    if fence is not None:
        # An unclosed fence: read what it held anyway and say so, the same
        # tolerance ``parse_frontmatter`` applies to an unclosed block.
        skipped.append(
            f"line {block_start}: code fence is never closed; its content was "
            "parsed anyway."
        )
        flush(heading)

    return Curriculum(
        nodes=tuple(nodes),
        edges=_dedupe_edges(edges, skipped),
        skipped=tuple(skipped),
        attribute_only=tuple(attribute_only),
    )


def curriculum_path(
    root: Path | str | None = None, path: Path | str | None = None
) -> Path:
    """Where the curriculum lives: ``path`` if given, else the vault's copy.

    The vault comes from ``config.toml`` through :func:`md_search.vault_root`, so
    nothing here depends on the repository layout and a test can point the loader
    anywhere.
    """
    if path is not None:
        return Path(path)
    return vault_root(root) / CURRICULUM_VAULT_PATH


def load_curriculum(
    root: Path | str | None = None, path: Path | str | None = None
) -> Curriculum:
    """Read and parse the curriculum file.

    Raises :class:`CurriculumParseError` when the file cannot be read or is not
    UTF-8, and :class:`~katagiri.md_search.VaultNotFoundError` when the vault
    itself is unconfigured — :func:`import_curriculum` turns both into values.
    """
    target = curriculum_path(root, path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise CurriculumParseError(
            f"Could not read the curriculum at {target}: {exc}. It is expected at "
            f"{CURRICULUM_VAULT_PATH} inside the configured vault."
        ) from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CurriculumParseError(
            f"{target} is not valid UTF-8 ({exc.reason} at byte {exc.start})."
        ) from exc
    # BOM and CRLF, for the same reason ``md_search._decode`` strips them: a
    # leading '﻿---' is not a frontmatter delimiter.
    return parse_curriculum(text.lstrip("﻿"))


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------


def find_cycle(edges: Iterable[tuple[str, str]]) -> tuple[str, ...] | None:
    """One cycle in a directed graph as a node path, or ``None`` if acyclic.

    Iterative depth-first search: a personal curriculum is small, but a recursive
    walk over a graph read from a file is a stack overflow waiting for a typo.
    The path is returned rather than a boolean because "there is a cycle" is not
    an actionable error message.
    """
    graph: dict[str, list[str]] = {}
    for from_id, to_id in edges:
        graph.setdefault(from_id, []).append(to_id)
        graph.setdefault(to_id, [])

    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(graph, WHITE)

    for root in sorted(graph):
        if colour[root] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = [root]
        colour[root] = GREY
        while stack:
            node, index = stack[-1]
            neighbours = graph[node]
            if index >= len(neighbours):
                colour[node] = BLACK
                stack.pop()
                path.pop()
                continue
            stack[-1] = (node, index + 1)
            nxt = neighbours[index]
            if colour[nxt] == GREY:
                start = path.index(nxt)
                return tuple(path[start:]) + (nxt,)
            if colour[nxt] == BLACK:
                continue
            colour[nxt] = GREY
            stack.append((nxt, 0))
            path.append(nxt)
    return None


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _existing_edges(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    return {
        (row["from_id"], row["to_id"], row["edge_type"])
        for row in conn.execute("SELECT from_id, to_id, edge_type FROM item_edge")
    }


def _existing_grammar_items(
    conn: sqlite3.Connection, ids: Sequence[str]
) -> dict[str, sqlite3.Row]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {
        row["id"]: row
        for row in conn.execute(
            f"SELECT id, kind, level FROM item WHERE id IN ({marks})", tuple(ids)
        )
    }


def _upsert_grammar_item(
    conn: sqlite3.Connection, *, item_id: str, level: str | None, created_ts: str
) -> None:
    """Insert the grammar node, or fill in a blank ``level`` on the existing row.

    ``COALESCE(existing, new)`` like ``session_tools._upsert_word_item``: an
    import may supply a level the row lacked and may never overwrite one someone
    curated — nor the ``understanding`` rating, which is the learner's and is not
    touched here at all.
    """
    conn.execute(
        """
        INSERT INTO item (id, kind, level, created_ts)
        VALUES (?, 'grammar', ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            level = COALESCE(item.level, excluded.level)
        """,
        (item_id, level, created_ts),
    )


def _existing_curriculum_attrs(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """``(node id, attr key)`` for every T028 tag already stored in ``settings``."""
    settings_keys = tuple(CURRICULUM_ATTR_SETTINGS_KEYS.values())
    marks = ",".join("?" * len(settings_keys))
    return {
        (row["scope"], _SETTINGS_KEY_TO_ATTR[row["key"]])
        for row in conn.execute(
            f"SELECT scope, key FROM settings WHERE key IN ({marks})", settings_keys
        )
    }


def _upsert_curriculum_attr(
    conn: sqlite3.Connection, *, item_id: str, attr: str, value: str, updated_ts: str
) -> None:
    """Write one T028 tag into ``settings``. See the module docstring for why.

    curriculum.md is the only author of these tags (unlike ``level``, nothing
    else ever curates them), so this overwrites on conflict rather than
    ``COALESCE``-protecting an existing value: a typo fixed in the file and
    re-imported should actually update the stored reference.
    """
    conn.execute(
        """
        INSERT INTO settings (scope, key, value, updated_ts)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(scope, key) DO UPDATE SET
            value = excluded.value,
            updated_ts = excluded.updated_ts
        """,
        (item_id, CURRICULUM_ATTR_SETTINGS_KEYS[attr], value, updated_ts),
    )


def import_curriculum(
    conn: sqlite3.Connection,
    *,
    root: Path | str | None = None,
    path: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import the curriculum graph into ``item`` / ``item_edge``. Idempotent.

    Every id the file mentions gets an ``item`` row of kind ``grammar`` (a stub
    when only its id is known), then every edge is inserted. One transaction:
    a graph that is half-written is a graph that answers reachability wrongly.

    ``dry_run=True`` computes and reports everything and writes nothing — the
    honest way to ask "what would this change?" of a source-of-truth table.

    Returns ``{"ok": True, "path", "dry_run", "nodes", "edges", "orphan_edges",
    "attributes", "orphan_attributes", "skipped", "note"}``. ``attributes`` /
    ``orphan_attributes`` are T028 (D-39): the ``jf_can_do``/``irodori_lesson``/
    ``tae_kim_section`` tags, additive in ``settings`` and never deleted, same
    doctrine as edges. Failure values: :data:`CURRICULUM_UNAVAILABLE`,
    :data:`CURRICULUM_EMPTY`, :data:`CURRICULUM_CYCLE`.
    """
    try:
        target = curriculum_path(root, path)
        parsed = load_curriculum(root, path)
    except (VaultNotFoundError, CurriculumParseError) as exc:
        return {"ok": False, "error": CURRICULUM_UNAVAILABLE, "note": str(exc)}

    ids = parsed.ids
    if not ids:
        return {
            "ok": False,
            "error": CURRICULUM_EMPTY,
            "path": str(target),
            "skipped": list(parsed.skipped),
            "note": (
                "no grammar node or edge was found. Nodes are declared in a "
                "fenced block with 'id:' plus optional 'prereqs:'/'unlocks:', or "
                "drawn as an arrow diagram; ids are slugs like 'g-wa-topic'."
            ),
        }

    existing_edges = _existing_edges(conn)
    parsed_keys = {edge.key for edge in parsed.edges}
    # The graph that would *result*, not just the file's half: importing an edge
    # that closes a cycle with rows already in the database is still a cycle.
    cycle = find_cycle(
        {(from_id, to_id) for from_id, to_id, _ in existing_edges | parsed_keys}
    )
    if cycle is not None:
        return {
            "ok": False,
            "error": CURRICULUM_CYCLE,
            "path": str(target),
            "cycle": list(cycle),
            "skipped": list(parsed.skipped),
            "note": (
                "the resulting graph would contain the cycle "
                f"{' -> '.join(cycle)}, so nothing was imported. Reachability over "
                "a cyclic graph has no answer, and half a graph is worse than none."
            ),
        }

    levels = parsed.levels
    before = _existing_grammar_items(conn, ids)
    declared = {node.id for node in parsed.nodes}

    wrong_kind = sorted(
        item_id
        for item_id, row in before.items()
        if row["kind"] != GRAMMAR_KIND
    )
    skipped = list(parsed.skipped)
    for item_id in wrong_kind:
        skipped.append(
            f"{item_id} already exists as a {before[item_id]['kind']} item; its "
            "kind was left alone and no edge to it was created."
        )
    blocked = set(wrong_kind)

    to_write = [item_id for item_id in ids if item_id not in blocked]
    edges = [
        edge
        for edge in parsed.edges
        if edge.from_id not in blocked and edge.to_id not in blocked
    ]

    created = [item_id for item_id in to_write if item_id not in before]
    stubs = [item_id for item_id in created if item_id not in declared]
    levelled = [
        item_id
        for item_id in to_write
        if item_id in before and before[item_id]["level"] is None and levels.get(item_id)
    ]
    new_edges = [edge for edge in edges if edge.key not in existing_edges]

    # In the database, absent from the file. Reported, never deleted: item_edge is
    # a source-of-truth table, so an edge the learner added by hand looks exactly
    # like an edge the file used to have.
    mentioned = set(ids)
    orphans = sorted(
        (from_id, to_id, edge_type)
        for from_id, to_id, edge_type in existing_edges - parsed_keys
        if from_id in mentioned or to_id in mentioned
    )

    # T028 (D-39): the three external-reference tags, same additive/orphan shape
    # as edges above but landing in `settings` (see the module docstring). Tags
    # on a blocked (wrong-kind) id are dropped along with its edges.
    parsed_attrs = {
        key: value_line
        for key, value_line in parsed.attributes.items()
        if key[0] not in blocked
    }
    existing_attrs = _existing_curriculum_attrs(conn)
    new_attrs = [key for key in parsed_attrs if key not in existing_attrs]
    orphan_attrs = sorted(
        (item_id, attr)
        for item_id, attr in existing_attrs - set(parsed_attrs)
        if item_id in mentioned
    )

    if not dry_run:
        now = _utc_now()
        owns = not conn.in_transaction
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        try:
            for item_id in to_write:
                _upsert_grammar_item(
                    conn, item_id=item_id, level=levels.get(item_id), created_ts=now
                )
            conn.executemany(
                "INSERT INTO item_edge (from_id, to_id, edge_type) VALUES (?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                [(edge.from_id, edge.to_id, edge.edge_type) for edge in edges],
            )
            for (item_id, attr), (value, _line) in parsed_attrs.items():
                _upsert_curriculum_attr(
                    conn, item_id=item_id, attr=attr, value=value, updated_ts=now
                )
        except Exception:
            if owns:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:  # pragma: no cover - rollback of a failed import
                    pass
            raise
        if owns:
            conn.execute("COMMIT")

    by_type = Counter(edge.edge_type for edge in parsed.edges)
    by_source = Counter(edge.source for edge in parsed.edges)
    logger.info(
        "curriculum import%s: %d ids (%d new, %d stubs), %d edges (%d new) from %s",
        " (dry run)" if dry_run else "",
        len(to_write),
        len(created),
        len(stubs),
        len(edges),
        len(new_edges),
        target,
    )

    return {
        "ok": True,
        "path": str(target),
        "dry_run": dry_run,
        "nodes": {
            "ids": len(to_write),
            "declared": len(declared - blocked),
            "items_created": len(created),
            "stubs_created": len(stubs),
            "levels_filled": len(levelled),
            "unchanged": len(to_write) - len(created) - len(levelled),
        },
        "edges": {
            "parsed": len(parsed.edges),
            "written": len(edges),
            "created": len(new_edges),
            "already_present": len(edges) - len(new_edges),
            "by_type": dict(by_type),
            "by_source": dict(by_source),
        },
        "orphan_edges": [
            {"from_id": from_id, "to_id": to_id, "edge_type": edge_type}
            for from_id, to_id, edge_type in orphans
        ],
        "attributes": {
            "parsed": len(parsed_attrs),
            "created": len(new_attrs),
            "already_present": len(parsed_attrs) - len(new_attrs),
            "by_attr": dict(Counter(attr for _item_id, attr in parsed_attrs)),
        },
        "orphan_attributes": [
            {"id": item_id, "attribute": attr} for item_id, attr in orphan_attrs
        ],
        "skipped": skipped,
        "note": " ".join(
            note
            for note in (
                "edges in the database but not in the file are reported, never "
                "deleted: item_edge is source-of-truth."
                if orphans
                else None,
                "attribute tags (jf_can_do/irodori_lesson/tae_kim_section) in the "
                "database but not in the file are reported, never deleted: "
                "curriculum.md is source-of-truth for these tags."
                if orphan_attrs
                else None,
            )
            if note is not None
        )
        or None,
    }


# ---------------------------------------------------------------------------
# Grammar reachability
# ---------------------------------------------------------------------------

_STAMP_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_WS_RE: Final = re.compile(r"\s+")

#: SQLite's default parameter ceiling is 999; 400 leaves room for the rest of a
#: statement and keeps one chunk's row count sane.
_IN_CHUNK: Final = 400


def _chunks(values: Sequence[str], size: int = _IN_CHUNK) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _parse_stamp(value: Any) -> datetime | None:
    """One of the schema's fixed-width UTC stamps as a datetime, or ``None``.

    Strict about the shape rather than tolerant: every timestamp column carries a
    ``GLOB`` CHECK for exactly this format, so anything else is a row that came
    from outside the schema and guessing at it would put a wrong age into a decay.
    """
    if not isinstance(value, str) or not _STAMP_RE.match(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _as_datetime(value: str | datetime | None) -> datetime:
    """``now`` as an aware UTC datetime. Explicit so a fold is reproducible."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(
            tzinfo=timezone.utc
        )
    if isinstance(value, str):
        parsed = _parse_stamp(value)
        if parsed is None:
            raise ValueError(
                "now must be an ISO-8601 UTC stamp of exactly the schema's shape "
                "(YYYY-MM-DDTHH:MM:SSZ)."
            )
        return parsed
    raise TypeError("now must be a datetime, an ISO-8601 UTC stamp, or None.")


@dataclass(frozen=True, slots=True)
class GrammarDag:
    """The stored dependency graph, loaded once for a whole batch.

    ``prereqs`` maps a node to the nodes that must come **before** it — the
    ``item_edge`` rows pointing *at* it, which is the direction
    ``item_edge_to_idx`` indexes. ``unlocked_by`` is the same lookup over
    ``unlock`` edges and exists to be *reported*, never walked: see the module
    docstring on why availability is not reachability.

    ``cycle`` is a cycle in the ``prereq`` edges if the stored graph has one.
    :func:`import_curriculum` refuses to create one, but hand-written rows can,
    and reachability over a cyclic graph has no answer.
    """

    prereqs: dict[str, tuple[str, ...]]
    unlocked_by: dict[str, tuple[str, ...]]
    cycle: tuple[str, ...] | None

    def prereq_closure(self, node: str) -> tuple[str, ...]:
        """Every node transitively required before ``node``, excluding itself.

        Breadth-first and iterative over a visited set, so a cycle terminates
        instead of recursing forever — the caller still refuses to answer when
        :attr:`cycle` is set, but a walk that hangs would be a worse failure than
        a wrong one.
        """
        seen: set[str] = {node}
        out: set[str] = set()
        frontier: list[str] = [node]
        while frontier:
            current = frontier.pop()
            for parent in self.prereqs.get(current, ()):
                if parent in seen:
                    continue
                seen.add(parent)
                out.add(parent)
                frontier.append(parent)
        return tuple(sorted(out))


def load_grammar_dag(conn: sqlite3.Connection) -> GrammarDag:
    """Read every ``item_edge`` row into a :class:`GrammarDag`. Reads only.

    Both edge types are loaded in one pass — the table is small, and reading it
    twice to keep the two maps separate would be two scans for no gain.
    """
    prereqs: dict[str, list[str]] = {}
    unlocked_by: dict[str, list[str]] = {}
    prereq_pairs: list[tuple[str, str]] = []
    for row in conn.execute(
        "SELECT from_id, to_id, edge_type FROM item_edge ORDER BY to_id, from_id"
    ):
        from_id, to_id, edge_type = row["from_id"], row["to_id"], row["edge_type"]
        if edge_type == EDGE_PREREQ:
            prereqs.setdefault(to_id, []).append(from_id)
            prereq_pairs.append((from_id, to_id))
        elif edge_type == EDGE_UNLOCK:
            unlocked_by.setdefault(to_id, []).append(from_id)
    return GrammarDag(
        prereqs={node: tuple(parents) for node, parents in prereqs.items()},
        unlocked_by={node: tuple(parents) for node, parents in unlocked_by.items()},
        cycle=find_cycle(prereq_pairs),
    )


@dataclass(frozen=True, slots=True)
class Mastery:
    """Whether one node counts as already acquired, and on what evidence."""

    item_id: str            # canonical, after alias resolution
    mastered: bool
    via: str | None         # a MASTERY_* value, or None when not mastered
    exists: bool            # is there an item row at all?
    kind: str | None
    understanding: int | None
    is_known: bool
    suspect: bool
    redirected: bool


class MasteryLookup:
    """Cached mastery verdicts for graph nodes, one instance per batch.

    Knownness goes through :mod:`katagiri.known` exactly as :class:`KnownLookup`
    does, so alias redirects and manual-mark precedence apply; ``understanding``
    is read from ``item`` because the known set does not carry it. Cached because
    one prereq node is typically in the closure of a dozen candidates.
    """

    __slots__ = ("_conn", "_min_understanding", "_cache", "queries")

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        min_understanding: int = DEFAULT_MIN_UNDERSTANDING,
    ) -> None:
        if not isinstance(min_understanding, int) or isinstance(
            min_understanding, bool
        ):
            raise TypeError("min_understanding must be an int.")
        self._conn = conn
        self._min_understanding = min_understanding
        self._cache: dict[str, Mastery] = {}
        self.queries = 0

    @property
    def min_understanding(self) -> int:
        return self._min_understanding

    def verdict(self, item_id: str) -> Mastery:
        cached = self._cache.get(item_id)
        if cached is not None:
            return cached
        result = self._resolve(item_id)
        self._cache[item_id] = result
        return result

    def mastered_ids(self) -> tuple[str, ...]:
        """Every id asked about so far that came back mastered. For reporting."""
        return tuple(
            sorted(key for key, value in self._cache.items() if value.mastered)
        )

    def _resolve(self, item_id: str) -> Mastery:
        redirect = resolve_alias(self._conn, item_id)
        canonical = redirect["canonical_id"]
        self.queries += 1
        answer = known.known_word(self._conn, canonical)
        row = self._conn.execute(
            "SELECT kind, understanding FROM item WHERE id = ?", (canonical,)
        ).fetchone()
        understanding = None if row is None else row["understanding"]
        # An ambiguous answer cannot happen for an id that has an item row, and
        # when it does happen it is a surface collision — never a verdict.
        is_known = bool(answer.get("is_known")) and not answer.get("ambiguous")
        via: str | None = None
        if is_known:
            via = MASTERY_KNOWN_SET
        elif understanding is not None and understanding >= self._min_understanding:
            via = MASTERY_UNDERSTANDING
        return Mastery(
            item_id=canonical,
            mastered=via is not None,
            via=via,
            exists=row is not None,
            kind=None if row is None else row["kind"],
            understanding=understanding,
            is_known=is_known,
            suspect=bool(answer.get("suspect")),
            redirected=bool(redirect["redirected"]),
        )


def grammar_reachability(
    conn: sqlite3.Connection,
    grammar_ids: Iterable[str],
    *,
    dag: GrammarDag | None = None,
    mastery: MasteryLookup | None = None,
    min_understanding: int = DEFAULT_MIN_UNDERSTANDING,
) -> dict[str, dict[str, Any]]:
    """Per grammar id: is it reachable, and what is missing if it is not?

    The rule, once, in code as in the module docstring: **every node in the
    transitive ``prereq`` closure, excluding the node itself, must be mastered.**
    The node itself may be unmastered — that is the ``+1``. ``unlock`` edges are
    reported as ``unlocked_by`` / ``unlock_ready`` and never consulted.

    Reads only. Pass ``dag`` and ``mastery`` to share one graph load and one
    verdict cache across a batch of candidates.
    """
    graph = dag if dag is not None else load_grammar_dag(conn)
    lookup = (
        mastery
        if mastery is not None
        else MasteryLookup(conn, min_understanding=min_understanding)
    )
    out: dict[str, dict[str, Any]] = {}
    for raw in grammar_ids:
        node = str(raw).strip()
        if not node or node in out:
            continue
        own = lookup.verdict(node)
        closure = graph.prereq_closure(own.item_id)
        missing: list[dict[str, Any]] = []
        for parent in closure:
            verdict = lookup.verdict(parent)
            if not verdict.mastered:
                missing.append(
                    {
                        "id": verdict.item_id,
                        "exists": verdict.exists,
                        "understanding": verdict.understanding,
                    }
                )
        unlock_sources = graph.unlocked_by.get(own.item_id, ())
        out[node] = {
            "id": node,
            "canonical_id": own.item_id,
            "exists": own.exists,
            "kind": own.kind,
            "mastered": own.mastered,
            "mastered_via": own.via,
            "understanding": own.understanding,
            "suspect": own.suspect,
            "redirected": own.redirected,
            "is_new": not own.mastered,
            "reachable": not missing,
            "prereqs": list(graph.prereqs.get(own.item_id, ())),
            "closure_size": len(closure),
            "missing_prereqs": missing,
            # Availability, reported and never walked (D-28, module docstring).
            "unlocked_by": list(unlock_sources),
            "unlock_ready": [
                source for source in unlock_sources if lookup.verdict(source).mastered
            ],
        }
    return out


# ---------------------------------------------------------------------------
# Comprehension debt
# ---------------------------------------------------------------------------


def _normalise_answer(value: str) -> str:
    """The shallow form used to compare ``expected`` against ``produced``.

    Whitespace-collapsed and case-folded, and nothing more. Deliberately shallow
    — see :data:`DEBT_MISS` and the module docstring for why the signal it feeds
    is weighted below the assisted one rather than trusted.
    """
    return _WS_RE.sub(" ", value.strip()).casefold()


def _is_miss(expected: Any, produced: Any) -> bool:
    if not isinstance(expected, str) or not isinstance(produced, str):
        return False
    if not expected.strip() or not produced.strip():
        return False
    return _normalise_answer(expected) != _normalise_answer(produced)


def _decay(age_days: float, half_life_days: float) -> float:
    """Exponential decay factor. Negative ages (clock skew) clamp to full weight."""
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def _observation_weight(
    row: Mapping[str, Any] | sqlite3.Row,
    *,
    now: datetime,
    half_life_days: float,
) -> tuple[float, str] | None:
    """One observation's signed contribution, and which bucket it fell in.

    ``None`` when the row's timestamp is not the schema's shape — such a row is
    counted as skipped rather than folded at a guessed age.
    """
    stamp = _parse_stamp(row["ts"])
    if stamp is None:
        return None
    age_days = (now - stamp).total_seconds() / 86_400.0
    recency = _decay(age_days, half_life_days)
    band = row["coverage_band"]
    unassisted = bool(row["unassisted"])
    if not unassisted:
        return recency * DEBT_ASSISTED * DEBT_ATTRIBUTION.get(band, 1.0), "assisted"
    if _is_miss(row["expected"], row["produced"]):
        return recency * DEBT_MISS * DEBT_ATTRIBUTION.get(band, 1.0), "miss"
    return recency * DEBT_CLEAN_CREDIT * CREDIT_ATTRIBUTION.get(band, 1.0), "clean"


def _observations_for(
    conn: sqlite3.Connection, item_ids: Sequence[str]
) -> dict[str, list[sqlite3.Row]]:
    out: dict[str, list[sqlite3.Row]] = {}
    for chunk in _chunks(item_ids):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"""
            SELECT item_id, ts, unassisted, coverage_band, expected, produced
              FROM observation
             WHERE item_id IN ({marks})
             ORDER BY item_id, ts
            """,
            tuple(chunk),
        ):
            out.setdefault(row["item_id"], []).append(row)
    return out


def _stat_cache_for(
    conn: sqlite3.Connection, item_ids: Sequence[str]
) -> dict[str, sqlite3.Row]:
    out: dict[str, sqlite3.Row] = {}
    for chunk in _chunks(item_ids):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"""
            SELECT item_id, comprehension_debt, strength, review_count,
                   computed_ts, last_event_ts
              FROM item_stat_cache
             WHERE item_id IN ({marks})
            """,
            tuple(chunk),
        ):
            out[row["item_id"]] = row
    return out


def comprehension_debt(
    conn: sqlite3.Connection,
    item_ids: Iterable[str],
    *,
    now: str | datetime | None = None,
    half_life_days: float = DEBT_HALF_LIFE_DAYS,
) -> dict[str, dict[str, Any]]:
    """Accumulated comprehension debt per item, folded from the two sources.

    ``observation`` rows are the live evidence and
    ``item_stat_cache.comprehension_debt`` is the derived prior over them; the
    fold never counts an observation twice — a cache row at or after an item's
    newest observation is used as-is (decayed to ``now``), a stale one is used
    plus only the observations recorded after its ``computed_ts``. See the module
    docstring for the signal / attribution / recency terms.

    Reads only, and returns an entry for **every** requested id, including ids
    with no evidence at all (``debt`` 0.0, ``source`` :data:`DEBT_FROM_NOTHING`) —
    a missing key would make a caller's ranking silently skip an item.

    ``half_life_days`` must be positive; a non-positive one is a programmer error
    and raises.
    """
    if not isinstance(half_life_days, (int, float)) or isinstance(
        half_life_days, bool
    ):
        raise TypeError("half_life_days must be a number.")
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive.")
    moment = _as_datetime(now)

    wanted: list[str] = []
    seen: set[str] = set()
    for raw in item_ids:
        value = str(raw).strip()
        if value and value not in seen:
            seen.add(value)
            wanted.append(value)
    if not wanted:
        return {}

    observations = _observations_for(conn, wanted)
    cached = _stat_cache_for(conn, wanted)

    out: dict[str, dict[str, Any]] = {}
    for item_id in wanted:
        rows = observations.get(item_id, [])
        cache_row = cached.get(item_id)
        cache_debt = None if cache_row is None else cache_row["comprehension_debt"]
        cache_stamp = (
            None if cache_row is None else _parse_stamp(cache_row["computed_ts"])
        )

        base = 0.0
        folded = rows
        source = DEBT_FROM_OBSERVATIONS if rows else DEBT_FROM_NOTHING
        if cache_debt is not None and cache_stamp is not None:
            base = float(cache_debt) * _decay(
                (moment - cache_stamp).total_seconds() / 86_400.0, half_life_days
            )
            computed_ts = cache_row["computed_ts"]
            folded = [row for row in rows if str(row["ts"]) > computed_ts]
            source = DEBT_FROM_CACHE_TAIL if folded else DEBT_FROM_CACHE

        buckets: Counter[str] = Counter()
        total = base
        skipped = 0
        for row in folded:
            weighed = _observation_weight(
                row, now=moment, half_life_days=half_life_days
            )
            if weighed is None:
                skipped += 1
                continue
            contribution, bucket = weighed
            total += contribution
            buckets[bucket] += 1

        out[item_id] = {
            "item_id": item_id,
            # Clamped: performing cleanly does not put the learner in credit, it
            # clears the debt. A negative debt would invert the ranking.
            "debt": round(max(0.0, total), 4),
            "source": source,
            "observations": len(rows),
            "folded_observations": len(folded),
            "assisted": buckets["assisted"],
            "misses": buckets["miss"],
            "clean": buckets["clean"],
            "skipped_observations": skipped,
            "last_observation_ts": rows[-1]["ts"] if rows else None,
            "cache": None
            if cache_row is None
            else {
                "comprehension_debt": cache_row["comprehension_debt"],
                # Reported, never folded: nothing writes these yet, so their scale
                # is undefined (see the module docstring).
                "strength": cache_row["strength"],
                "review_count": cache_row["review_count"],
                "computed_ts": cache_row["computed_ts"],
                "last_event_ts": cache_row["last_event_ts"],
            },
        }
    return out


# ---------------------------------------------------------------------------
# Difficulty for me: the vendored datasets
# ---------------------------------------------------------------------------

#: Component names, used as the keys of :data:`DIFFICULTY_WEIGHTS`, of the
#: ``components`` block in a result, and of nothing else.
COMPONENT_READABILITY: Final = "readability"
COMPONENT_FREQUENCY: Final = "frequency"
COMPONENT_JLPT: Final = "jlpt"
COMPONENT_COVERAGE: Final = "coverage"

#: How much each component contributes to the combined score. Coverage carries
#: the most because it is the only one that knows who is reading (see the module
#: docstring); the corpus-level three split the rest in the order they are
#: trustworthy at sentence scale — readability is fitted on graded corpora,
#: frequency is a rank, and the JLPT lists are a curriculum artefact that stops
#: at ~8k words. Weights are renormalised over whatever is available, so these
#: are ratios, not a promise that all four ever answer at once.
DIFFICULTY_WEIGHTS: Final[dict[str, float]] = {
    COMPONENT_COVERAGE: 0.40,
    COMPONENT_READABILITY: 0.25,
    COMPONENT_FREQUENCY: 0.20,
    COMPONENT_JLPT: 0.15,
}

#: Dataset names, as reported in ``datasets`` and in log lines.
DATASET_JREADABILITY: Final = "jreadability"
DATASET_BCCWJ: Final = "bccwj"
DATASET_JLPT: Final = "jlpt"

#: ``vendor/<dir>/<file>`` layout, mirroring ``vendor/jmdict`` and
#: ``vendor/kanjium``: one directory per component, globbed rather than pinned by
#: name so a version bump is a re-download plus a manifest line, not a code edit.
JREADABILITY_DIR_NAME: Final = "jreadability"
JREADABILITY_SDIST_GLOB: Final = "jreadability-*.tar.gz"
#: The member of the sdist that carries the formula. Matched by suffix because
#: the archive's top directory is version-stamped.
JREADABILITY_MODULE_SUFFIX: Final = "src/jreadability/jreadability.py"

BCCWJ_DIR_NAME: Final = "bccwj"
BCCWJ_ZIP_GLOB: Final = "BCCWJ_frequencylist_suw_ver*.zip"
#: Columns the parser needs from the short-unit list. ``lemma`` is UniDic's
#: 語彙素 — the same object :attr:`Morph.lemma` is — and ``pos`` is its
#: hyphen-joined POS path, of which only the first level is compared.
BCCWJ_COLUMNS: Final = ("rank", "lemma", "pos", "frequency", "pmw")

JLPT_DIR_NAME: Final = "jlpt"
#: ``n<level>-vocab-*.anki``: the level is in the filename, so a file cannot be
#: loaded under the wrong level by being placed in the wrong order.
JLPT_FILE_RE: Final = re.compile(r"^n([1-5])-vocab-[0-9a-z-]+\.anki$")
#: The Anki-1 tables and the field the Japanese sits in. Anki-1 exports are
#: plain SQLite, which is why they are the vendored form: no new dependency, and
#: the level lists are read with the standard library.
JLPT_FRONT_FIELD: Final = "Front"
JLPT_REQUIRED_TABLES: Final = ("fields", "fieldModels")

#: Per-level difficulty on the 0–100 scale. N5 is the easy end by definition;
#: the steps are even because the JLPT itself offers no distance between levels.
JLPT_DIFFICULTY: Final[dict[int, float]] = {5: 0.0, 4: 25.0, 3: 50.0, 2: 75.0, 1: 100.0}
JLPT_LEVELS: Final = (5, 4, 3, 2, 1)

#: Upstream's documented score range and band names (jreadability README): a
#: score is *higher* for easier text, so difficulty is the distance from
#: :data:`READABILITY_MAX`.
READABILITY_MIN: Final = 0.5
READABILITY_MAX: Final = 6.5
READABILITY_BANDS: Final[tuple[tuple[float, str], ...]] = (
    (5.5, "lower-elementary"),
    (4.5, "upper-elementary"),
    (3.5, "lower-intermediate"),
    (2.5, "upper-intermediate"),
    (1.5, "lower-advanced"),
    (0.5, "upper-advanced"),
)

#: The five feature names whose coefficients are read out of the vendored sdist,
#: plus the intercept. Named exactly as upstream names them, because the names
#: *are* the extraction pattern.
READABILITY_TERMS: Final = (
    "mean_length_of_sentence",
    "percentage_of_kango",
    "percentage_of_wago",
    "percentage_of_verbs",
    "percentage_of_particles",
)
_COEFF_RE_TEMPLATE: Final = r"{term}\s*\*\s*(-?\d+(?:\.\d+)?)"
_INTERCEPT_RE: Final = re.compile(r"\+\s*(\d+(?:\.\d+)?)\s*\)")

#: Upstream splits sentences on exactly these four characters, and counts a
#: trailing fragment as a sentence.
SENTENCE_ENDINGS: Final = ("。", "？", "！", "．")
#: UniDic's 語種 (word origin) values the formula distinguishes.
GOSHU_KANGO: Final = "漢"
GOSHU_WAGO: Final = "和"
#: The formula's verb rule: 動詞 excluding 非自立可能 (あり in あります and the
#: like), and 助詞 for particles.
VERB_POS1: Final = "動詞"
VERB_EXCLUDED_POS2: Final = "非自立可能"
PARTICLE_POS1: Final = "助詞"

#: Per-dataset failure codes. A dataset that is absent and a dataset whose bytes
#: are wrong are different problems with different fixes, so they never share a
#: code.
DATASET_MISSING: Final = "dataset_missing"
DATASET_AMBIGUOUS: Final = "dataset_ambiguous"
DATASET_UNREADABLE: Final = "dataset_unreadable"
DATASET_MALFORMED: Final = "dataset_malformed"
DATASET_CHECKSUM: Final = "dataset_checksum_mismatch"

#: :func:`difficulty_for_me` refusals.
BAD_WEIGHTS: Final = "bad_weights"
NO_COMPONENTS: Final = "no_components"

#: How many worst-ranked lemmas a frequency profile names, and how many unlisted
#: ones. Enough to explain the number, not a study list.
DEFAULT_TOP_RARE: Final = 5


@dataclass(frozen=True, slots=True)
class DatasetStatus:
    """What one vendored dataset's loader found. Reported, never inferred.

    ``available`` is the only field a caller has to branch on; ``error`` names
    which of the :data:`DATASET_MISSING` family happened, ``digest`` is the
    verified sha256 (so a result can pin which bytes produced it), and
    ``entries`` is whatever counting means for that dataset — coefficients,
    ranked lemmas, levelled words.
    """

    name: str
    available: bool
    path: str | None = None
    digest: str | None = None
    version: str | None = None
    entries: int | None = None
    error: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "path": self.path,
            "sha256": self.digest,
            "version": self.version,
            "entries": self.entries,
            "error": self.error,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class ReadabilityModel:
    """The Lee & Hasebe coefficients, as read from the vendored sdist."""

    coefficients: dict[str, float]
    intercept: float
    status: DatasetStatus

    @property
    def available(self) -> bool:
        return self.status.available

    def score(self, features: Mapping[str, float]) -> float:
        """Apply the formula to one feature mapping keyed by :data:`READABILITY_TERMS`."""
        total = self.intercept
        for term, coefficient in self.coefficients.items():
            total += coefficient * float(features[term])
        return total


@dataclass(frozen=True, slots=True)
class FrequencyList:
    """BCCWJ short-unit ranks, keyed by ``(lemma, pos1)`` and by lemma alone.

    Two indexes over one entry tuple rather than two copies of the data:
    ``by_lemma_pos`` is the honest key (の the particle and の the noun are
    different rows), ``by_lemma`` is the fallback for a morph whose POS the list
    spells differently, and both hold *indexes* into :attr:`entries`.
    """

    entries: tuple[tuple[int, int, float], ...]   # (rank, frequency, pmw)
    by_lemma_pos: dict[tuple[str, str], int]
    by_lemma: dict[str, int]
    status: DatasetStatus

    @property
    def available(self) -> bool:
        return self.status.available

    @property
    def ranked(self) -> int:
        return len(self.entries)

    def lookup(self, lemma: str, pos1: str | None) -> tuple[int, int, float] | None:
        """``(rank, frequency, pmw)`` for a morph, or ``None`` when unlisted."""
        if pos1:
            index = self.by_lemma_pos.get((lemma, pos1))
            if index is not None:
                return self.entries[index]
        index = self.by_lemma.get(lemma)
        return None if index is None else self.entries[index]

    def percentile(self, rank: int) -> float:
        """Where a rank sits among the ranked lemmas: 100 is the commonest."""
        if not self.entries:
            return 0.0
        return round(100.0 * (1.0 - (rank - 1) / len(self.entries)), 4)


@dataclass(frozen=True, slots=True)
class JlptLevels:
    """Word → JLPT level (5 = N5, easiest), folded from the per-level lists.

    A word listed at two levels keeps the **easier** one: the lists overlap, and
    claiming a word is N1 because an N1 list repeats it would make every level's
    core vocabulary look advanced.
    """

    by_word: dict[str, int]
    counts: dict[int, int]
    status: DatasetStatus
    sources: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.status.available

    def level(self, *keys: str | None) -> int | None:
        """The easiest level any of ``keys`` is listed at, or ``None``."""
        for key in keys:
            if not key:
                continue
            found = self.by_word.get(key)
            if found is not None:
                return found
        return None


# ---------------------------------------------------------------------------
# Locating and loading the vendored difficulty data
# ---------------------------------------------------------------------------


def _dataset_dir(name: str) -> Path:
    """``<repo>/vendor/<name>``, via the same resolver the other loaders use."""
    return vendor_dir() / name


def _single_vendor_file(
    dataset: str, directory: Path, pattern: str
) -> tuple[Path | None, DatasetStatus | None]:
    """The one file matching ``pattern``, or a status explaining why not.

    Several matches is a refusal, not "newest wins", for the reason
    :func:`katagiri.jmdict_import.default_jmdict_zip` gives: which build produced
    a number is provenance.
    """
    try:
        candidates = sorted(directory.glob(pattern)) if directory.is_dir() else []
    except OSError as exc:  # pragma: no cover - unreadable vendor directory
        return None, DatasetStatus(
            name=dataset,
            available=False,
            path=str(directory),
            error=DATASET_UNREADABLE,
            note=f"could not list {directory}: {exc}.",
        )
    if not candidates:
        return None, DatasetStatus(
            name=dataset,
            available=False,
            path=str(directory / pattern),
            error=DATASET_MISSING,
            note=(
                f"no file matching {pattern} under {directory}. Difficulty data is "
                "vendored by hand (D-10) — see vendor/README.md; the score is "
                "computed from the components that are present."
            ),
        )
    if len(candidates) > 1:
        listing = ", ".join(path.name for path in candidates)
        return None, DatasetStatus(
            name=dataset,
            available=False,
            path=str(directory),
            error=DATASET_AMBIGUOUS,
            note=(
                f"{len(candidates)} files match {pattern} in {directory} "
                f"({listing}); refusing to guess which one the score came from."
            ),
        )
    return candidates[0], None


def _verified(
    dataset: str, path: Path, *, manifest_path: Path | str | None = None
) -> tuple[str | None, DatasetStatus | None]:
    """Verify one vendored file's digest.

    A mismatch **raises** (``vendor/README.md`` rule 3 — the bad bytes are never
    read); a file the manifest does not list, or one that cannot be read, comes
    back as a status, because an un-pinned optional dataset is a setup step that
    is half done rather than tampering.
    """
    if not path.is_file():
        return None, DatasetStatus(
            name=dataset,
            available=False,
            path=str(path),
            error=DATASET_MISSING,
            note=(
                f"{path} does not exist. Difficulty data is vendored by hand "
                "(D-10) — see vendor/README.md; the score is computed from the "
                "components that are present."
            ),
        )
    try:
        return verify_vendor_file(path, manifest_path=manifest_path), None
    except ChecksumError as exc:
        if exc.expected is None:
            return None, DatasetStatus(
                name=dataset,
                available=False,
                path=str(path),
                error=DATASET_MISSING,
                note=(
                    f"{path.name} is not listed in vendor/CHECKSUMS.sha256, so it is "
                    "not the bytes this build was written against. Add its digest "
                    f"(see vendor/README.md). ({exc})"
                ),
            )
        raise
    except VendorFileError as exc:
        return None, DatasetStatus(
            name=dataset,
            available=False,
            path=str(path),
            error=DATASET_UNREADABLE,
            note=str(exc),
        )


def _unavailable_model(status: DatasetStatus) -> ReadabilityModel:
    return ReadabilityModel(coefficients={}, intercept=0.0, status=status)


def _unavailable_frequency(status: DatasetStatus) -> FrequencyList:
    return FrequencyList(
        entries=(), by_lemma_pos={}, by_lemma={}, status=status
    )


def _unavailable_jlpt(status: DatasetStatus) -> JlptLevels:
    return JlptLevels(by_word={}, counts={}, status=status)


_READABILITY_CACHE: dict[str, ReadabilityModel] = {}
_FREQUENCY_CACHE: dict[str, FrequencyList] = {}
_JLPT_CACHE: dict[str, JlptLevels] = {}


def reset_difficulty_caches() -> None:
    """Forget every loaded dataset. For tests, and for a re-vendor mid-session."""
    _READABILITY_CACHE.clear()
    _FREQUENCY_CACHE.clear()
    _JLPT_CACHE.clear()


def _version_from_name(name: str, prefix: str, suffix: str) -> str | None:
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix): len(name) - len(suffix)] or None
    return None


def load_readability_model(
    *,
    path: Path | str | None = None,
    manifest_path: Path | str | None = None,
    refresh: bool = False,
) -> ReadabilityModel:
    """The readability coefficients, read from the vendored ``jreadability`` sdist.

    The formula is *not* written out in this file. The vendored source is opened,
    its ``readability_score = (...)`` expression is read, and each coefficient is
    taken from the term it multiplies — so the numbers are upstream's, pinned by
    the digest in ``vendor/CHECKSUMS.sha256``, and a version bump that changed one
    of them would change the digest rather than silently disagreeing with a
    hard-coded copy.

    Cached per resolved path. Failure is a value (:data:`DATASET_MISSING`,
    :data:`DATASET_MALFORMED`, …) except for a digest mismatch, which raises.
    """
    target = Path(path) if path is not None else None
    if target is None:
        directory = _dataset_dir(JREADABILITY_DIR_NAME)
        target, status = _single_vendor_file(
            DATASET_JREADABILITY, directory, JREADABILITY_SDIST_GLOB
        )
        if target is None:
            assert status is not None
            return _unavailable_model(status)

    key = str(target)
    if not refresh and key in _READABILITY_CACHE:
        return _READABILITY_CACHE[key]

    digest, status = _verified(
        DATASET_JREADABILITY, target, manifest_path=manifest_path
    )
    if digest is None:
        assert status is not None
        return _unavailable_model(status)

    version = _version_from_name(target.name, "jreadability-", ".tar.gz")
    try:
        with tarfile.open(target, "r:gz") as archive:
            member = next(
                (
                    entry
                    for entry in archive.getmembers()
                    if entry.name.replace("\\", "/").endswith(
                        JREADABILITY_MODULE_SUFFIX
                    )
                ),
                None,
            )
            if member is None:
                return _unavailable_model(
                    DatasetStatus(
                        name=DATASET_JREADABILITY,
                        available=False,
                        path=key,
                        digest=digest,
                        version=version,
                        error=DATASET_MALFORMED,
                        note=(
                            f"{target.name} contains no {JREADABILITY_MODULE_SUFFIX}; "
                            "this is not the jreadability sdist the loader reads the "
                            "coefficients from."
                        ),
                    )
                )
            handle = archive.extractfile(member)
            source = b"" if handle is None else handle.read()
    except (OSError, tarfile.TarError) as exc:
        return _unavailable_model(
            DatasetStatus(
                name=DATASET_JREADABILITY,
                available=False,
                path=key,
                digest=digest,
                version=version,
                error=DATASET_UNREADABLE,
                note=f"could not read {target}: {exc}.",
            )
        )

    text = source.decode("utf-8", "replace")
    coefficients: dict[str, float] = {}
    for term in READABILITY_TERMS:
        match = re.search(_COEFF_RE_TEMPLATE.format(term=re.escape(term)), text)
        if match is not None:
            coefficients[term] = float(match.group(1))
    intercept_match = _INTERCEPT_RE.search(text)
    missing = [term for term in READABILITY_TERMS if term not in coefficients]
    if missing or intercept_match is None:
        return _unavailable_model(
            DatasetStatus(
                name=DATASET_JREADABILITY,
                available=False,
                path=key,
                digest=digest,
                version=version,
                error=DATASET_MALFORMED,
                note=(
                    "could not read the formula out of the vendored source "
                    f"({'missing ' + ', '.join(missing) if missing else 'no intercept'}"
                    "). The coefficients are read from upstream rather than copied "
                    "into this file, so a refactor upstream shows up here as a "
                    "refusal instead of as a wrong number."
                ),
            )
        )

    model = ReadabilityModel(
        coefficients=coefficients,
        intercept=float(intercept_match.group(1)),
        status=DatasetStatus(
            name=DATASET_JREADABILITY,
            available=True,
            path=key,
            digest=digest,
            version=version,
            entries=len(coefficients) + 1,
            note=(
                "Lee & Hasebe coefficients read from the vendored sdist; features "
                "are recomputed on the vendored full UniDic."
            ),
        ),
    )
    _READABILITY_CACHE[key] = model
    return model


def load_frequency_list(
    *,
    path: Path | str | None = None,
    manifest_path: Path | str | None = None,
    refresh: bool = False,
) -> FrequencyList:
    """BCCWJ short-unit frequency ranks from the vendored zip. Cached per path.

    The zip holds one TSV; it is streamed rather than extracted, and only the
    five columns the score needs are kept. Rows arrive in rank order, so the
    first row for a key is its best rank and later duplicates are skipped.
    """
    target = Path(path) if path is not None else None
    if target is None:
        directory = _dataset_dir(BCCWJ_DIR_NAME)
        target, status = _single_vendor_file(DATASET_BCCWJ, directory, BCCWJ_ZIP_GLOB)
        if target is None:
            assert status is not None
            return _unavailable_frequency(status)

    key = str(target)
    if not refresh and key in _FREQUENCY_CACHE:
        return _FREQUENCY_CACHE[key]

    digest, status = _verified(DATASET_BCCWJ, target, manifest_path=manifest_path)
    if digest is None:
        assert status is not None
        return _unavailable_frequency(status)

    version = _version_from_name(
        target.name, "BCCWJ_frequencylist_suw_", ".zip"
    )
    entries: list[tuple[int, int, float]] = []
    by_lemma_pos: dict[tuple[str, str], int] = {}
    by_lemma: dict[str, int] = {}
    try:
        with zipfile.ZipFile(target) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".tsv")
            ]
            if len(names) != 1:
                return _unavailable_frequency(
                    DatasetStatus(
                        name=DATASET_BCCWJ,
                        available=False,
                        path=key,
                        digest=digest,
                        version=version,
                        error=DATASET_MALFORMED,
                        note=(
                            f"{target.name} holds {len(names)} .tsv members; the "
                            "short-unit frequency list is exactly one."
                        ),
                    )
                )
            with archive.open(names[0]) as raw:
                stream = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                header = stream.readline().rstrip("\r\n").split("\t")
                position = {column: header.index(column) for column in BCCWJ_COLUMNS
                            if column in header}
                absent = [
                    column for column in BCCWJ_COLUMNS if column not in position
                ]
                if absent:
                    return _unavailable_frequency(
                        DatasetStatus(
                            name=DATASET_BCCWJ,
                            available=False,
                            path=key,
                            digest=digest,
                            version=version,
                            error=DATASET_MALFORMED,
                            note=(
                                f"{names[0]} is missing the column(s) "
                                f"{', '.join(absent)}; this is not the short-unit "
                                "frequency list this loader parses."
                            ),
                        )
                    )
                width = max(position.values()) + 1
                for line in stream:
                    row = line.rstrip("\r\n").split("\t")
                    if len(row) < width:
                        continue
                    lemma = row[position["lemma"]].strip()
                    if not lemma:
                        continue
                    try:
                        rank = int(row[position["rank"]])
                        frequency = int(row[position["frequency"]])
                        pmw = float(row[position["pmw"]] or 0.0)
                    except ValueError:
                        continue
                    index = len(entries)
                    entries.append((rank, frequency, pmw))
                    pos1 = row[position["pos"]].split("-", 1)[0].strip()
                    if pos1:
                        by_lemma_pos.setdefault((lemma, pos1), index)
                    by_lemma.setdefault(lemma, index)
    except (OSError, zipfile.BadZipFile) as exc:
        return _unavailable_frequency(
            DatasetStatus(
                name=DATASET_BCCWJ,
                available=False,
                path=key,
                digest=digest,
                version=version,
                error=DATASET_UNREADABLE,
                note=f"could not read {target}: {exc}.",
            )
        )

    if not entries:
        return _unavailable_frequency(
            DatasetStatus(
                name=DATASET_BCCWJ,
                available=False,
                path=key,
                digest=digest,
                version=version,
                error=DATASET_MALFORMED,
                note=f"{target.name} parsed to zero usable rows.",
            )
        )

    listing = FrequencyList(
        entries=tuple(entries),
        by_lemma_pos=by_lemma_pos,
        by_lemma=by_lemma,
        status=DatasetStatus(
            name=DATASET_BCCWJ,
            available=True,
            path=key,
            digest=digest,
            version=version,
            entries=len(entries),
            note=(
                "BCCWJ short-unit (suw) frequency list; ranks are over "
                f"{len(entries)} lemmas, and an unlisted lemma counts as the "
                "rarest bucket."
            ),
        ),
    )
    _FREQUENCY_CACHE[key] = listing
    return listing


def jlpt_level_files(directory: Path | str | None = None) -> dict[int, Path]:
    """``{level: path}`` for the ``n<level>-vocab-*.anki`` files that exist."""
    root = Path(directory) if directory is not None else _dataset_dir(JLPT_DIR_NAME)
    if not root.is_dir():
        return {}
    found: dict[int, Path] = {}
    for candidate in sorted(root.iterdir()):
        match = JLPT_FILE_RE.match(candidate.name)
        if match is not None and candidate.is_file():
            found.setdefault(int(match.group(1)), candidate)
    return found


def _read_anki_front_values(path: Path) -> list[str]:
    """Every ``Front`` field value in one Anki-1 export.

    Opened ``mode=ro&immutable=1``: these files are vendored, finished, and never
    written to, which is exactly the case that flag is honest for (contrast
    :mod:`katagiri.anki_snapshot`, where a live collection makes it a lie).
    """
    uri = f"file:{quote(path.as_posix(), safe='/:')}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not set(JLPT_REQUIRED_TABLES) <= tables:
            raise sqlite3.DatabaseError(
                f"{path.name} has no {'/'.join(JLPT_REQUIRED_TABLES)} tables; it is "
                "not an Anki-1 vocabulary export."
            )
        rows = conn.execute(
            """
            SELECT f.value AS value
              FROM fields f
              JOIN fieldModels m ON m.id = f.fieldModelId
             WHERE m.name = ?
            """,
            (JLPT_FRONT_FIELD,),
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows if isinstance(row[0], str)]


_TAG_RE: Final = re.compile(r"<[^>]+>")
_ENTITY_RE: Final = re.compile(r"&(?:nbsp|#160);")


def _clean_jlpt_word(value: str) -> str:
    """One list entry as a lookup key: markup and separators out, nothing else.

    The exports are flashcard fronts, so a few carry ``<br>`` or ``&nbsp;``. They
    are stripped; the word itself is **not** normalised further (no reading fold,
    no bracket surgery), because a key this loader invents is a key the tokenizer
    will never produce.
    """
    text = _ENTITY_RE.sub(" ", _TAG_RE.sub(" ", value))
    return text.replace("　", " ").strip()


def load_jlpt_levels(
    *,
    directory: Path | str | None = None,
    manifest_path: Path | str | None = None,
    refresh: bool = False,
) -> JlptLevels:
    """JLPT levels from the vendored tanos per-level lists. Cached per directory.

    Every level file present is read and verified; a missing level is a note, not
    a refusal — N5-only data still answers "is this word beginner vocabulary". One
    level whose *digest* is wrong refuses the whole set, though: a level mean
    computed over four of five lists, with no way to see which one dropped out, is
    a number that looks the same as a correct one.
    """
    root = Path(directory) if directory is not None else _dataset_dir(JLPT_DIR_NAME)
    key = str(root)
    if not refresh and key in _JLPT_CACHE:
        return _JLPT_CACHE[key]

    files = jlpt_level_files(root)
    if not files:
        return _unavailable_jlpt(
            DatasetStatus(
                name=DATASET_JLPT,
                available=False,
                path=key,
                error=DATASET_MISSING,
                note=(
                    f"no n<level>-vocab-*.anki file under {root}. See "
                    "vendor/README.md ('JLPT level lists'); the score is computed "
                    "without the JLPT component."
                ),
            )
        )

    by_word: dict[str, int] = {}
    counts: dict[int, int] = {}
    digests: list[str] = []
    sources: list[str] = []
    problems: list[str] = []
    for level in JLPT_LEVELS:
        path = files.get(level)
        if path is None:
            continue
        digest, status = _verified(DATASET_JLPT, path, manifest_path=manifest_path)
        if digest is None:
            assert status is not None
            problems.append(f"N{level}: {status.note}")
            continue
        try:
            values = _read_anki_front_values(path)
        except sqlite3.DatabaseError as exc:
            problems.append(f"N{level}: {exc}")
            continue
        added = 0
        for raw in values:
            word = _clean_jlpt_word(raw)
            if not word:
                continue
            # The easier level wins: files are read N5 → N1 and a lower level
            # never overwrites a higher one.
            if by_word.setdefault(word, level) == level:
                added += 1
        counts[level] = added
        digests.append(digest[:12])
        sources.append(path.name)

    if not by_word:
        return _unavailable_jlpt(
            DatasetStatus(
                name=DATASET_JLPT,
                available=False,
                path=key,
                error=DATASET_MALFORMED,
                note=(
                    "no usable word was read from the vendored JLPT lists. "
                    + " ".join(problems)
                ).strip(),
            )
        )

    levels = JlptLevels(
        by_word=by_word,
        counts=counts,
        sources=tuple(sources),
        status=DatasetStatus(
            name=DATASET_JLPT,
            available=True,
            path=key,
            digest="+".join(digests) or None,
            version="+".join(f"N{level}" for level in sorted(counts, reverse=True)),
            entries=len(by_word),
            note=(
                "tanos JLPT vocabulary lists (CC BY, Jonathan Waller); a word "
                "listed at several levels keeps the easiest."
                + (" Problems: " + " ".join(problems) if problems else "")
            ),
        ),
    )
    _JLPT_CACHE[key] = levels
    return levels


def difficulty_datasets(
    *, manifest_path: Path | str | None = None, refresh: bool = False
) -> dict[str, dict[str, Any]]:
    """Status of all three vendored difficulty datasets, without scoring anything.

    The honest answer to "is difficulty scoring set up?", for a setup check or a
    tool surface. A digest mismatch is reported here rather than raised, because
    the question being asked *is* "what is wrong".
    """
    out: dict[str, dict[str, Any]] = {}
    for name, loader in (
        (DATASET_JREADABILITY, load_readability_model),
        (DATASET_BCCWJ, load_frequency_list),
        (DATASET_JLPT, load_jlpt_levels),
    ):
        try:
            loaded = loader(manifest_path=manifest_path, refresh=refresh)  # type: ignore[operator]
            out[name] = loaded.status.as_dict()
        except ChecksumError as exc:
            out[name] = DatasetStatus(
                name=name,
                available=False,
                error=DATASET_CHECKSUM,
                note=str(exc),
            ).as_dict()
    return out


# ---------------------------------------------------------------------------
# The four components
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadabilityFeatures:
    """The five inputs the formula takes, plus the counts they came from."""

    tokens: int
    sentences: int
    mean_length_of_sentence: float
    percentage_of_kango: float
    percentage_of_wago: float
    percentage_of_verbs: float
    percentage_of_particles: float

    def as_terms(self) -> dict[str, float]:
        return {
            "mean_length_of_sentence": self.mean_length_of_sentence,
            "percentage_of_kango": self.percentage_of_kango,
            "percentage_of_wago": self.percentage_of_wago,
            "percentage_of_verbs": self.percentage_of_verbs,
            "percentage_of_particles": self.percentage_of_particles,
        }


def readability_features(
    text: str, *, tagger: Any | None = None
) -> ReadabilityFeatures | None:
    """The formula's features for ``text``, or ``None`` when there is no token.

    This runs its own pass over the tagger's nodes instead of reusing the
    :class:`Morph` list the rest of the module works from, for one reason:
    :attr:`Morph` carries no ``goshu`` (語種) field, and kango/wago are two of the
    five features. Widening ``Morph`` is :mod:`katagiri.tokenizer`'s to do (a
    shared file this task does not own), and deriving 語種 from the frequency list
    would make readability depend on whether *another* dataset is vendored. So the
    cost is one extra tokenization of the same text, paid only when a difficulty
    score is asked for.

    Raises :class:`~katagiri.tokenizer.TokenizerError` if the tagger cannot be
    built — the caller turns that into :data:`TOKENIZER_UNAVAILABLE`.
    """
    if not isinstance(text, str):
        raise TypeError("readability_features() takes the text as a str.")
    active = tagger if tagger is not None else get_tagger()
    nodes = list(active(text)) if text.strip() else []
    if not nodes:
        return None

    sentences = 1
    for node in nodes:
        if node.surface in SENTENCE_ENDINGS:
            sentences += 1
    # Upstream counts a trailing fragment as a sentence and does not count an
    # empty one, so a text ending in 。has exactly as many sentences as
    # terminators.
    if nodes[-1].surface in SENTENCE_ENDINGS:
        sentences -= 1
    sentences = max(1, sentences)

    kango = wago = verbs = particles = 0
    for node in nodes:
        feature = node.feature
        goshu = getattr(feature, "goshu", None)
        if goshu == GOSHU_KANGO:
            kango += 1
        elif goshu == GOSHU_WAGO:
            wago += 1
        pos1 = getattr(feature, "pos1", None)
        if pos1 == VERB_POS1 and getattr(feature, "pos2", None) != VERB_EXCLUDED_POS2:
            verbs += 1
        elif pos1 == PARTICLE_POS1:
            particles += 1

    total = len(nodes)
    return ReadabilityFeatures(
        tokens=total,
        sentences=sentences,
        mean_length_of_sentence=total / sentences,
        percentage_of_kango=100.0 * kango / total,
        percentage_of_wago=100.0 * wago / total,
        percentage_of_verbs=100.0 * verbs / total,
        percentage_of_particles=100.0 * particles / total,
    )


def readability_band(score: float) -> str:
    """Upstream's band name for a score (README's six ranges)."""
    for floor, name in READABILITY_BANDS:
        if score >= floor:
            return name
    return READABILITY_BANDS[-1][1]


def _scaled_readability_difficulty(score: float) -> float:
    """A readability score as 0–100 difficulty: distance from the easy end."""
    clamped = min(READABILITY_MAX, max(READABILITY_MIN, score))
    span = READABILITY_MAX - READABILITY_MIN
    return round(100.0 * (READABILITY_MAX - clamped) / span, 2)


def readability_profile(
    text: str,
    *,
    model: ReadabilityModel | None = None,
    tagger: Any | None = None,
    features: ReadabilityFeatures | None = None,
) -> dict[str, Any]:
    """The readability component: score, band, difficulty, and the features.

    ``available`` is False — with a reason — when the model is not vendored or the
    text has no token; the score is never guessed at from a partial formula.
    """
    resolved = model if model is not None else _safe_readability_model()
    if not resolved.available:
        return {
            "available": False,
            "error": resolved.status.error,
            "note": resolved.status.note,
            "score": None,
            "band": None,
            "difficulty": None,
        }
    computed = (
        features
        if features is not None
        else readability_features(text, tagger=tagger)
    )
    if computed is None:
        return {
            "available": False,
            "error": EMPTY_TEXT,
            "note": "no token to measure readability on.",
            "score": None,
            "band": None,
            "difficulty": None,
        }
    score = resolved.score(computed.as_terms())
    return {
        "available": True,
        "error": None,
        "score": round(score, 3),
        "band": readability_band(score),
        "difficulty": _scaled_readability_difficulty(score),
        "scale": {"min": READABILITY_MIN, "max": READABILITY_MAX, "higher_is_easier": True},
        "tokens": computed.tokens,
        "sentences": computed.sentences,
        "features": {
            name: round(value, 4) for name, value in computed.as_terms().items()
        },
        "note": None,
    }


def frequency_profile(
    morphs: Iterable[Morph],
    *,
    frequency: FrequencyList | None = None,
    top_rare: int = DEFAULT_TOP_RARE,
) -> dict[str, Any]:
    """The frequency component over a text's content morphs.

    Per content token: its BCCWJ percentile (100 = commonest), with an unlisted
    lemma counted at 0 — see the module docstring for why absence is treated as
    evidence here and not in the JLPT component. Difficulty is ``100 - mean``.
    """
    resolved = frequency if frequency is not None else _safe_frequency_list()
    if not resolved.available:
        return {
            "available": False,
            "error": resolved.status.error,
            "note": resolved.status.note,
            "difficulty": None,
        }

    tokens = 0
    listed = 0
    total_percentile = 0.0
    per_type: dict[tuple[str, str | None], dict[str, Any]] = {}
    for morph in morphs:
        if not is_content_morph(morph):
            continue
        tokens += 1
        key = type_key(morph)
        found = resolved.lookup(morph.lemma, morph.pos1)
        if found is None:
            percentile = 0.0
            rank = None
        else:
            rank = found[0]
            percentile = resolved.percentile(rank)
            listed += 1
        total_percentile += percentile
        entry = per_type.setdefault(
            key,
            {
                "lemma": morph.lemma,
                "pos": morph.pos1,
                "rank": rank,
                "pmw": None if found is None else round(found[2], 4),
                "percentile": percentile,
                "occurrences": 0,
            },
        )
        entry["occurrences"] += 1

    if not tokens:
        return {
            "available": False,
            "error": GATE_NO_CONTENT,
            "note": "no content token to look up a frequency for.",
            "difficulty": None,
        }

    mean_percentile = total_percentile / tokens
    rare = sorted(
        per_type.values(),
        key=lambda entry: (entry["percentile"], entry["lemma"]),
    )[: max(0, top_rare)]
    return {
        "available": True,
        "error": None,
        "difficulty": round(100.0 - mean_percentile, 2),
        "mean_percentile": round(mean_percentile, 2),
        "content_tokens": tokens,
        "listed_tokens": listed,
        "unlisted_tokens": tokens - listed,
        "unlisted_pct": round(100.0 * (tokens - listed) / tokens, 2),
        "ranked_lemmas": resolved.ranked,
        "rarest": rare,
        "note": (
            "an unlisted lemma counts as the rarest bucket (percentile 0); "
            "'unlisted_pct' is how much of the mean that is."
        ),
    }


def jlpt_profile(
    morphs: Iterable[Morph], *, levels: JlptLevels | None = None
) -> dict[str, Any]:
    """The JLPT component over a text's content morphs.

    A token is looked up by lemma first, then by surface. Unlisted tokens are
    **excluded** from the mean and reported as ``listed_pct``: the lists are a
    deliberately partial curriculum artefact, so absence from them is not
    difficulty (the frequency component is where rarity is counted).
    """
    resolved = levels if levels is not None else _safe_jlpt_levels()
    if not resolved.available:
        return {
            "available": False,
            "error": resolved.status.error,
            "note": resolved.status.note,
            "difficulty": None,
        }

    tokens = 0
    by_level: Counter[int] = Counter()
    total = 0.0
    hardest: int | None = None
    for morph in morphs:
        if not is_content_morph(morph):
            continue
        tokens += 1
        level = resolved.level(morph.lemma, morph.surface)
        if level is None:
            continue
        by_level[level] += 1
        total += JLPT_DIFFICULTY[level]
        hardest = level if hardest is None else min(hardest, level)

    listed = sum(by_level.values())
    if not tokens:
        return {
            "available": False,
            "error": GATE_NO_CONTENT,
            "note": "no content token to look up a JLPT level for.",
            "difficulty": None,
        }
    if not listed:
        return {
            "available": False,
            "error": DATASET_MISSING,
            "note": (
                "no content token in this text appears in the vendored JLPT lists, "
                "so no level mean is offered rather than one over zero words."
            ),
            "difficulty": None,
            "content_tokens": tokens,
            "listed_tokens": 0,
            "listed_pct": 0.0,
        }

    return {
        "available": True,
        "error": None,
        "difficulty": round(total / listed, 2),
        "content_tokens": tokens,
        "listed_tokens": listed,
        "listed_pct": round(100.0 * listed / tokens, 2),
        "hardest_level": None if hardest is None else f"N{hardest}",
        "by_level": {f"N{level}": by_level[level] for level in JLPT_LEVELS if by_level[level]},
        "levelled_words": resolved.status.entries,
        "note": (
            "unlisted tokens are excluded from the mean; 'listed_pct' is how much "
            "of the text it speaks for."
        ),
    }


def _safe_readability_model() -> ReadabilityModel:
    """:func:`load_readability_model`, with a digest mismatch as a value.

    Rule 3 of ``vendor/README.md`` is honoured by the loader (it raises and never
    reads the file); the study loop is kept alive here by turning that into an
    unavailable component whose note carries both digests.
    """
    try:
        return load_readability_model()
    except ChecksumError as exc:
        return _unavailable_model(
            DatasetStatus(
                name=DATASET_JREADABILITY,
                available=False,
                error=DATASET_CHECKSUM,
                note=str(exc),
            )
        )


def _safe_frequency_list() -> FrequencyList:
    """:func:`load_frequency_list`, with a digest mismatch as a value."""
    try:
        return load_frequency_list()
    except ChecksumError as exc:
        return _unavailable_frequency(
            DatasetStatus(
                name=DATASET_BCCWJ,
                available=False,
                error=DATASET_CHECKSUM,
                note=str(exc),
            )
        )


def _safe_jlpt_levels() -> JlptLevels:
    """:func:`load_jlpt_levels`, with a digest mismatch as a value."""
    try:
        return load_jlpt_levels()
    except ChecksumError as exc:
        return _unavailable_jlpt(
            DatasetStatus(
                name=DATASET_JLPT,
                available=False,
                error=DATASET_CHECKSUM,
                note=str(exc),
            )
        )


# ---------------------------------------------------------------------------
# The combined score
# ---------------------------------------------------------------------------


def _check_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    """Validate and complete a weight mapping. Unknown keys are a refusal."""
    if weights is None:
        return dict(DIFFICULTY_WEIGHTS)
    if not isinstance(weights, Mapping):
        raise TypeError("weights must be a mapping of component name to number.")
    unknown = sorted(set(weights) - set(DIFFICULTY_WEIGHTS))
    if unknown:
        raise ValueError(
            f"unknown difficulty component(s): {', '.join(unknown)}. The four are "
            f"{', '.join(DIFFICULTY_WEIGHTS)}."
        )
    out = dict(DIFFICULTY_WEIGHTS)
    for name, value in weights.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"weight for {name} must be a number.")
        out[name] = float(value)
    return out


def _coverage_component(measured: Mapping[str, Any]) -> dict[str, Any]:
    """Coverage as a difficulty component: the share the learner does not know."""
    if not measured.get("ok"):
        return {
            "available": False,
            "error": measured.get("error"),
            "note": measured.get("note"),
            "difficulty": None,
        }
    known_pct = measured.get("known_pct")
    if known_pct is None:
        return {
            "available": False,
            "error": GATE_NO_CONTENT,
            "note": measured.get("note"),
            "difficulty": None,
        }
    return {
        "available": True,
        "error": None,
        "difficulty": round(100.0 - float(known_pct), 2),
        "known_pct": known_pct,
        "band": measured.get("band"),
        "counted_tokens": measured["counts"]["counted_tokens"],
        "unknown_types": measured.get("unknown_types"),
        "note": None,
    }


def combine_difficulty(
    components: Mapping[str, Mapping[str, Any]],
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Weighted mean over the components that answered, renormalised.

    Returns ``{"difficulty", "weight_used", "components_used", "components_missing"}``
    with ``difficulty`` ``None`` when nothing answered — a score computed from no
    component would be a number with no claim behind it.
    """
    resolved = _check_weights(weights)
    used: list[str] = []
    missing: list[str] = []
    weighted = 0.0
    weight_total = 0.0
    for name in DIFFICULTY_WEIGHTS:
        component = components.get(name) or {}
        value = component.get("difficulty")
        weight = resolved.get(name, 0.0)
        if not component.get("available") or value is None or weight <= 0:
            if not component.get("available"):
                missing.append(name)
            continue
        used.append(name)
        weighted += weight * float(value)
        weight_total += weight
    full = sum(weight for weight in resolved.values() if weight > 0)
    return {
        "difficulty": round(weighted / weight_total, 2) if weight_total else None,
        "weight_used": round(weight_total / full, 4) if full else 0.0,
        "components_used": used,
        "components_missing": missing,
        "weights": {name: resolved[name] for name in DIFFICULTY_WEIGHTS},
    }


def difficulty_for_me(
    conn: sqlite3.Connection,
    text: str | None = None,
    *,
    morphs: Sequence[Morph] | None = None,
    coverage_result: Mapping[str, Any] | None = None,
    weights: Mapping[str, float] | None = None,
    lookup: KnownLookup | None = None,
    tagger: Any | None = None,
    model: ReadabilityModel | None = None,
    frequency: FrequencyList | None = None,
    levels: JlptLevels | None = None,
    top_unknown: int = 0,
    top_rare: int = DEFAULT_TOP_RARE,
) -> dict[str, Any]:
    """How hard ``text`` is *for this learner*: one 0–100 score, four components.

    Higher is harder. The score is reported and never gates anything (see the
    module docstring); ``weight_used`` says how much of the intended weight
    actually answered, so a degraded score is visibly degraded.

    ``morphs`` and ``coverage_result`` let a caller that has already tokenized and
    measured — :func:`find_i_plus_one` — reuse both instead of paying for them
    again; the readability pass is separate either way (``Morph`` carries no
    ``goshu``, see :func:`readability_features`).

    Reads only. Failure values: :data:`EMPTY_TEXT`, :data:`TEXT_TOO_LARGE`,
    :data:`TOKENIZER_UNAVAILABLE`, :data:`BAD_WEIGHTS`, :data:`NO_COMPONENTS`.
    """
    if text is None and morphs is None:
        raise TypeError("difficulty_for_me() needs text, or morphs to score.")
    if text is not None and not isinstance(text, str):
        raise TypeError("difficulty_for_me() takes the text as a str.")
    try:
        resolved_weights = _check_weights(weights)
    except ValueError as exc:
        return {"ok": False, "error": BAD_WEIGHTS, "note": str(exc)}

    if text is not None:
        if not text.strip() and morphs is None:
            return {
                "ok": False,
                "error": EMPTY_TEXT,
                "note": "difficulty needs some text to measure.",
            }
        if len(text) > MAX_COVERAGE_CHARS:
            return {
                "ok": False,
                "error": TEXT_TOO_LARGE,
                "note": (
                    f"{len(text)} characters is past the {MAX_COVERAGE_CHARS}-"
                    "character limit for one difficulty call; measure it in parts."
                ),
            }

    try:
        active = tagger if tagger is not None else (
            get_tagger() if (morphs is None or text is not None) else None
        )
        if morphs is None:
            assert text is not None
            token_list: Sequence[Morph] = tokenize(text, tagger=active)
        else:
            token_list = morphs
        features = (
            readability_features(text, tagger=active) if text is not None else None
        )
    except TokenizerError as exc:
        logger.warning("difficulty_for_me refused: tokenizer unavailable (%s)", exc)
        return {
            "ok": False,
            "error": TOKENIZER_UNAVAILABLE,
            "note": f"{TOKENIZER_UNAVAILABLE_NOTE} ({exc})",
        }

    measured = (
        dict(coverage_result)
        if coverage_result is not None
        else coverage_from_morphs(
            conn, token_list, top_unknown=top_unknown, lookup=lookup
        )
    )

    readability_model = model if model is not None else _safe_readability_model()
    frequency_list = frequency if frequency is not None else _safe_frequency_list()
    jlpt_levels = levels if levels is not None else _safe_jlpt_levels()

    components: dict[str, dict[str, Any]] = {
        COMPONENT_COVERAGE: _coverage_component(measured),
        COMPONENT_READABILITY: (
            readability_profile(
                text or "", model=readability_model, tagger=active, features=features
            )
            if features is not None or text is not None
            else {
                "available": False,
                "error": EMPTY_TEXT,
                "note": (
                    "readability needs the text itself (it is measured over every "
                    "token, including the particles coverage excludes)."
                ),
                "difficulty": None,
            }
        ),
        COMPONENT_FREQUENCY: frequency_profile(
            token_list, frequency=frequency_list, top_rare=top_rare
        ),
        COMPONENT_JLPT: jlpt_profile(token_list, levels=jlpt_levels),
    }
    for name, component in components.items():
        component["weight"] = resolved_weights[name]

    combined = combine_difficulty(components, resolved_weights)
    if combined["difficulty"] is None:
        return {
            "ok": False,
            "error": NO_COMPONENTS,
            "note": (
                "no difficulty component could be measured: no vendored dataset "
                "loaded and coverage had no content token. 'components' says why "
                "for each."
            ),
            "components": components,
            "datasets": {
                DATASET_JREADABILITY: readability_model.status.as_dict(),
                DATASET_BCCWJ: frequency_list.status.as_dict(),
                DATASET_JLPT: jlpt_levels.status.as_dict(),
            },
        }

    missing = combined["components_missing"]
    return {
        "ok": True,
        "difficulty": combined["difficulty"],
        "higher_is_harder": True,
        "weight_used": combined["weight_used"],
        "components_used": combined["components_used"],
        "components_missing": missing,
        "components": components,
        "weights": combined["weights"],
        "datasets": {
            DATASET_JREADABILITY: readability_model.status.as_dict(),
            DATASET_BCCWJ: frequency_list.status.as_dict(),
            DATASET_JLPT: jlpt_levels.status.as_dict(),
        },
        "chars": None if text is None else len(text),
        "note": (
            "scored on "
            f"{len(combined['components_used'])} of {len(DIFFICULTY_WEIGHTS)} "
            f"components ({combined['weight_used']:.0%} of the intended weight); "
            f"missing: {', '.join(missing)}."
            if missing
            else None
        ),
    }


# ---------------------------------------------------------------------------
# i+1 selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """One piece of material offered to the gate.

    ``id`` is its ``item`` id when it is stored material — that is what lets the
    gate read its grammar annotation and its ``sealed`` flag. ``grammar_ids`` is
    an explicit annotation and wins over anything read from the database, so a
    caller holding better information is never overruled by a stale edge.
    """

    text: str
    id: str | None = None
    grammar_ids: tuple[str, ...] = field(default_factory=tuple)
    source: str | None = None


def as_candidate(value: Candidate | Mapping[str, Any] | str) -> Candidate:
    """Normalise one offered candidate. Type misuse raises, per module convention."""
    if isinstance(value, Candidate):
        return value
    if isinstance(value, str):
        return Candidate(text=value)
    if isinstance(value, Mapping):
        text = value.get("text", value.get("jp"))
        if not isinstance(text, str):
            raise TypeError(
                "a candidate mapping needs 'text' (or 'jp') as a str."
            )
        item_id = value.get("id", value.get("item_id"))
        if item_id is not None and not isinstance(item_id, str):
            raise TypeError("a candidate's id must be a str when given.")
        raw_grammar = value.get("grammar_ids", value.get("grammar", ()))
        if isinstance(raw_grammar, str):
            raise TypeError(
                "grammar_ids must be a sequence of ids, not a single string."
            )
        grammar = tuple(str(entry).strip() for entry in raw_grammar or ())
        origin = value.get("source")
        return Candidate(
            text=text,
            id=(item_id.strip() or None) if isinstance(item_id, str) else None,
            grammar_ids=tuple(entry for entry in grammar if entry),
            source=None if origin is None else str(origin),
        )
    raise TypeError(
        "a candidate must be a Candidate, a mapping with 'text', or a str."
    )


def candidates_from_items(
    conn: sqlite3.Connection,
    *,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    topic: str | None = None,
) -> list[Candidate]:
    """Stored sentence items as candidates, newest-id-last. Reads only.

    The text comes from ``sentence_text``, which is where a sentence item's
    Japanese actually lives, so an item with no indexed text is simply not a
    candidate rather than a candidate with no text. ``sealed`` rows are excluded
    here *as well as* at the gate — D-26 is worth enforcing at both ends.
    """
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit must be an int.")
    rows = conn.execute(
        """
        SELECT i.id AS id, s.jp AS jp, i.home_topic AS home_topic
          FROM item i
          JOIN sentence_text s ON s.item_id = i.id
         WHERE i.kind = ?
           AND i.sealed = 0
           AND (? IS NULL OR i.home_topic = ?)
         ORDER BY i.id
         LIMIT ?
        """,
        (SENTENCE_KIND, topic, topic, max(0, limit)),
    ).fetchall()
    return [
        Candidate(text=row["jp"], id=row["id"], source=SENTENCE_KIND)
        for row in rows
        if row["jp"] and row["jp"].strip()
    ]


def _candidate_rows(
    conn: sqlite3.Connection, ids: Sequence[str]
) -> dict[str, sqlite3.Row]:
    out: dict[str, sqlite3.Row] = {}
    for chunk in _chunks(ids):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            "SELECT id, kind, sealed, home_topic, audio_source, text_only "
            f"FROM item WHERE id IN ({marks})",
            tuple(chunk),
        ):
            out[row["id"]] = row
    return out


def _grammar_ids_from_edges(
    conn: sqlite3.Connection, item_id: str
) -> tuple[str, ...]:
    """Grammar points a stored item needs, from ``prereq`` edges pointing at it.

    ``WHERE to_id = ?`` is the query ``item_edge_to_idx`` exists for, and
    ``g-… → s-…`` as ``prereq`` is the schema's own way to say "this sentence
    requires that grammar point" — no new edge type, no new table.
    """
    return tuple(
        row["from_id"]
        for row in conn.execute(
            """
            SELECT e.from_id AS from_id
              FROM item_edge e
              JOIN item g ON g.id = e.from_id
             WHERE e.to_id = ? AND e.edge_type = ? AND g.kind = ?
             ORDER BY e.from_id
            """,
            (item_id, EDGE_PREREQ, GRAMMAR_KIND),
        )
    )


def _resolve_candidate_grammar(
    conn: sqlite3.Connection,
    candidate: Candidate,
    row: sqlite3.Row | None,
) -> tuple[tuple[str, ...], str]:
    """``(grammar ids, where they came from)``. First source that yields any wins.

    An explicit annotation is passed through **whole**, unfiltered: an id that is
    not a grammar slug, or names no grammar item, is gated by
    :data:`GATE_GRAMMAR_UNKNOWN` rather than quietly dropped here. Dropping it
    would turn "you annotated this with something I do not understand" into "this
    has no grammar", and the second answer lets the candidate through.
    """
    explicit = tuple(value.strip() for value in candidate.grammar_ids if value.strip())
    if explicit:
        return explicit, GRAMMAR_FROM_EXPLICIT
    if candidate.id:
        from_edges = _grammar_ids_from_edges(conn, candidate.id)
        if from_edges:
            return from_edges, GRAMMAR_FROM_EDGES
    if row is not None:
        topic = row["home_topic"]
        if isinstance(topic, str) and _is_grammar_id(topic.strip()):
            return (topic.strip(),), GRAMMAR_FROM_HOME_TOPIC
    return (), GRAMMAR_FROM_NOTHING


def candidate_item_ids(
    morphs: Iterable[Morph], lookup: KnownLookup
) -> tuple[str, ...]:
    """The distinct ``item`` ids a candidate's content morphs resolve to.

    Known items included: an item the learner "knows" and still needs help with
    is exactly where debt accumulates, so filtering to unknowns would hide the
    most interesting debt. Uses the batch's :class:`KnownLookup`, so this costs no
    query a coverage pass has not already paid for.
    """
    out: list[str] = []
    seen: set[str] = set()
    for morph in morphs:
        if not is_content_morph(morph):
            continue
        item_id = lookup.verdict(morph).item_id
        if item_id and item_id not in seen:
            seen.add(item_id)
            out.append(item_id)
    return tuple(out)


def _difficulty_summary(scored: Mapping[str, Any]) -> dict[str, Any]:
    """One candidate's difficulty, small enough to sit on 500 result entries.

    The full component blocks (features, rarest lemmas, per-level counts) belong
    to :func:`difficulty_for_me` on a single text; what a ranked list needs is the
    number, how much of the weight produced it, and the one figure per component
    that explains it.
    """
    if not scored.get("ok"):
        return {
            "ok": False,
            "error": scored.get("error"),
            "note": scored.get("note"),
            "score": None,
        }
    components: Mapping[str, Any] = scored.get("components") or {}
    readability = components.get(COMPONENT_READABILITY) or {}
    frequency = components.get(COMPONENT_FREQUENCY) or {}
    jlpt = components.get(COMPONENT_JLPT) or {}
    coverage_component = components.get(COMPONENT_COVERAGE) or {}
    return {
        "ok": True,
        "score": scored["difficulty"],
        "higher_is_harder": True,
        "weight_used": scored["weight_used"],
        "components_used": scored["components_used"],
        "components_missing": scored["components_missing"],
        "by_component": {
            name: (components.get(name) or {}).get("difficulty")
            for name in DIFFICULTY_WEIGHTS
        },
        "readability_score": readability.get("score"),
        "readability_band": readability.get("band"),
        "frequency_mean_percentile": frequency.get("mean_percentile"),
        "frequency_unlisted_pct": frequency.get("unlisted_pct"),
        "jlpt_hardest_level": jlpt.get("hardest_level"),
        "jlpt_listed_pct": jlpt.get("listed_pct"),
        "known_pct": coverage_component.get("known_pct"),
    }


def _bad_gate(note: str) -> dict[str, Any]:
    return {"ok": False, "error": BAD_GATE, "note": note}


def _check_cap(name: str, value: int | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int or None.")
    if value < 0:
        return _bad_gate(f"{name} cannot be negative; None means no limit.")
    return None


def find_i_plus_one(
    conn: sqlite3.Connection,
    candidates: Iterable[Candidate | Mapping[str, Any] | str] | None = None,
    *,
    top: int = DEFAULT_TOP_CANDIDATES,
    min_coverage_pct: float = DEFAULT_MIN_COVERAGE_PCT,
    max_unknown_types: int | None = DEFAULT_MAX_UNKNOWN_TYPES,
    max_new_grammar: int | None = DEFAULT_MAX_NEW_GRAMMAR,
    min_understanding: int = DEFAULT_MIN_UNDERSTANDING,
    require_grammar: bool = True,
    production: bool = False,
    include_gated: bool = False,
    top_unknown: int = DEFAULT_CANDIDATE_TOP_UNKNOWN,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    topic: str | None = None,
    now: str | datetime | None = None,
    lookup: KnownLookup | None = None,
    score_difficulty: bool = True,
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Material that is i+1 on **both** axes, ranked by comprehension debt.

    A candidate is offered only when its grammar is reachable in the stored
    ``prereq`` graph *and* its vocabulary coverage clears the gate. The two are
    computed from independent sources and neither substitutes for the other: a
    sentence at 100% coverage whose grammar has an unmastered prerequisite is
    gated out with :data:`GATE_UNREACHABLE_GRAMMAR`, which is D-28 in one line.

    With ``candidates=None`` the stored sentence items are used (see
    :func:`candidates_from_items`). Every candidate is measured whether or not it
    passes, so ``coverage``/``grammar``/``debt`` are real numbers on gated
    candidates too; ``include_gated=True`` returns them with their reasons rather
    than only counting them.

    ``production=True`` restricts the pool to A0 **production** drills (D-38 /
    FR-018): a candidate is gated with :data:`GATE_NOT_AUDIO_ANCHORED` unless it
    is a stored item with ``audio_source IS NOT NULL`` and ``text_only = 0``. An
    unanchored or ``text_only`` item is never substituted or synthesised (no
    TTS) — it is reported, in ``gated_by``, with that reason and nothing else
    changes about it. Default ``False`` selects reading material exactly as
    before, unaffected by ``audio_source``/``text_only``.

    Every entry also carries a difficulty-for-me score
    (:func:`difficulty_for_me`, ``score_difficulty=False`` to skip the extra
    tokenization pass it costs). It is **reported only**: the gate is D-28's two
    halves and the ranking stays comprehension debt (:data:`RANKED_BY_DEBT`), so a
    vendored dataset appearing or disappearing can never change which material is
    offered — only how it is described.

    Reads only — nothing here writes a row or logs an event, not even a record of
    what was considered.

    Returns ``{"ok": True, "candidates", "gated", "counts", "gates",
    "ranked_by", "difficulty_datasets", "note"}``. Failure values: :data:`NO_CANDIDATES`,
    :data:`TOO_MANY_CANDIDATES`, :data:`BAD_LIMIT`, :data:`BAD_GATE`,
    :data:`BAD_TOP_UNKNOWN`, :data:`GRAMMAR_DAG_CYCLE`,
    :data:`TOKENIZER_UNAVAILABLE`.
    """
    if not isinstance(top, int) or isinstance(top, bool):
        raise TypeError("top must be an int.")
    if top < 1 or top > MAX_TOP_CANDIDATES:
        return {
            "ok": False,
            "error": BAD_LIMIT,
            "note": f"top must be between 1 and {MAX_TOP_CANDIDATES}.",
        }
    if not isinstance(min_coverage_pct, (int, float)) or isinstance(
        min_coverage_pct, bool
    ):
        raise TypeError("min_coverage_pct must be a number.")
    if not 0 <= min_coverage_pct <= 100:
        return _bad_gate("min_coverage_pct must be between 0 and 100.")
    for name, value in (
        ("max_unknown_types", max_unknown_types),
        ("max_new_grammar", max_new_grammar),
    ):
        refusal = _check_cap(name, value)
        if refusal is not None:
            return refusal
    if not isinstance(min_understanding, int) or isinstance(min_understanding, bool):
        raise TypeError("min_understanding must be an int.")
    if not 1 <= min_understanding <= 5:
        return _bad_gate(
            "min_understanding must be between 1 and 5 — it is the range the "
            "item.understanding CHECK allows."
        )
    if not isinstance(top_unknown, int) or isinstance(top_unknown, bool):
        raise TypeError("top_unknown must be an int.")
    if top_unknown < 0 or top_unknown > MAX_TOP_UNKNOWN:
        return {
            "ok": False,
            "error": BAD_TOP_UNKNOWN,
            "note": f"top_unknown must be between 0 and {MAX_TOP_UNKNOWN}.",
        }
    try:
        difficulty_weights = _check_weights(weights)
    except ValueError as exc:
        return {"ok": False, "error": BAD_WEIGHTS, "note": str(exc)}
    moment = _as_datetime(now)

    offered = (
        candidates_from_items(conn, limit=candidate_limit, topic=topic)
        if candidates is None
        else [as_candidate(value) for value in candidates]
    )
    if not offered:
        return {
            "ok": False,
            "error": NO_CANDIDATES,
            "note": (
                "nothing to choose from. Offer candidates explicitly, or index "
                "sentence items (item.kind = 'sentence' with a sentence_text row) "
                "for the built-in loader to read."
            ),
        }
    if len(offered) > MAX_CANDIDATES:
        return {
            "ok": False,
            "error": TOO_MANY_CANDIDATES,
            "note": (
                f"{len(offered)} candidates is past the {MAX_CANDIDATES} limit for "
                "one call; select in batches."
            ),
        }

    dag = load_grammar_dag(conn)
    if dag.cycle is not None:
        return {
            "ok": False,
            "error": GRAMMAR_DAG_CYCLE,
            "cycle": list(dag.cycle),
            "note": (
                "the stored prereq edges contain the cycle "
                f"{' -> '.join(dag.cycle)}, so reachability has no answer and "
                "nothing was offered. Fix the item_edge rows; import_curriculum "
                "refuses to create a cycle, so these were added by hand."
            ),
        }

    try:
        tagger = get_tagger()
    except TokenizerError as exc:
        logger.warning("find_i_plus_one refused: tokenizer unavailable (%s)", exc)
        return {
            "ok": False,
            "error": TOKENIZER_UNAVAILABLE,
            "note": f"{TOKENIZER_UNAVAILABLE_NOTE} ({exc})",
        }

    resolver = lookup if lookup is not None else KnownLookup(conn)
    mastery = MasteryLookup(conn, min_understanding=min_understanding)
    rows = _candidate_rows(
        conn, [candidate.id for candidate in offered if candidate.id]
    )

    # Loaded once for the whole batch, not once per candidate: the frequency list
    # is 185k rows and re-reading it 500 times would make the score cost more than
    # the selection it decorates.
    readability_model = _safe_readability_model() if score_difficulty else None
    frequency_list = _safe_frequency_list() if score_difficulty else None
    jlpt_levels = _safe_jlpt_levels() if score_difficulty else None

    analysed: list[dict[str, Any]] = []
    debt_wanted: set[str] = set()

    for candidate in offered:
        row = rows.get(candidate.id) if candidate.id else None
        reasons: list[str] = []
        text = candidate.text if isinstance(candidate.text, str) else ""
        if not text.strip():
            reasons.append(EMPTY_TEXT)
        if len(text) > MAX_CANDIDATE_CHARS:
            reasons.append(TEXT_TOO_LARGE)
        if row is not None and row["sealed"]:
            # No override flag on purpose: D-26 — probes may read the sealed
            # pool, material selection never serves from it.
            reasons.append(GATE_SEALED)
        if production and (
            row is None or row["audio_source"] is None or row["text_only"]
        ):
            # A0 production pool restriction (D-38 / FR-018): withheld, never
            # substituted, never synthesised (no TTS — F-02 stays deferred). A
            # candidate with no stored row (ad hoc text) has no anchor to
            # check, which is withheld the same way as an unanchored one.
            reasons.append(GATE_NOT_AUDIO_ANCHORED)

        grammar_ids, grammar_source = _resolve_candidate_grammar(
            conn, candidate, row
        )
        reachability = grammar_reachability(
            conn, grammar_ids, dag=dag, mastery=mastery
        )
        unreachable = [
            entry for entry in reachability.values() if not entry["reachable"]
        ]
        new_points = [entry for entry in reachability.values() if entry["is_new"]]
        unresolved = [
            entry
            for entry in reachability.values()
            if not entry["exists"] or entry["kind"] != GRAMMAR_KIND
        ]
        if unreachable:
            reasons.append(GATE_UNREACHABLE_GRAMMAR)
        if require_grammar and (unresolved or not grammar_ids):
            # Three ways to have no grammar information, one gate: nothing
            # annotated, an id with no ``item`` row, or an id naming something
            # that is not a grammar point. None of them is "reachable grammar" —
            # and an empty prereq closure would otherwise read as "no
            # prerequisites, go ahead", which is the quiet wrong answer D-28 is
            # about.
            reasons.append(GATE_GRAMMAR_UNKNOWN)
        if max_new_grammar is not None and len(new_points) > max_new_grammar:
            reasons.append(GATE_TOO_MUCH_NEW_GRAMMAR)

        if TEXT_TOO_LARGE in reasons or EMPTY_TEXT in reasons:
            morphs: list[Morph] = []
        else:
            morphs = tokenize(text, tagger=tagger)
        measured = coverage_from_morphs(
            conn, morphs, top_unknown=top_unknown, lookup=resolver
        )
        if not measured.get("ok"):  # pragma: no cover - inputs validated above
            return measured
        known_pct = measured["known_pct"]
        if known_pct is None:
            reasons.append(GATE_NO_CONTENT)
        elif known_pct < min_coverage_pct:
            reasons.append(GATE_COVERAGE_TOO_LOW)
        if (
            max_unknown_types is not None
            and measured["unknown_types"] > max_unknown_types
        ):
            reasons.append(GATE_TOO_MANY_UNKNOWN)

        vocab_ids = candidate_item_ids(morphs, resolver)
        grammar_item_ids = tuple(
            entry["canonical_id"] for entry in reachability.values()
        )
        debt_wanted.update(vocab_ids)
        debt_wanted.update(grammar_item_ids)

        # Reported, never gating: computed after every gate reason is already
        # decided, from the morphs and the coverage this candidate already paid
        # for, so it cannot influence the verdict above it.
        difficulty = (
            _difficulty_summary(
                difficulty_for_me(
                    conn,
                    text,
                    morphs=morphs,
                    coverage_result=measured,
                    weights=difficulty_weights,
                    lookup=resolver,
                    tagger=tagger,
                    model=readability_model,
                    frequency=frequency_list,
                    levels=jlpt_levels,
                )
            )
            if score_difficulty
            else None
        )

        analysed.append(
            {
                "candidate": candidate,
                "reasons": reasons,
                "coverage": measured,
                "difficulty": difficulty,
                "grammar_source": grammar_source,
                "reachability": reachability,
                "unreachable": unreachable,
                "new_points": new_points,
                "unresolved_points": unresolved,
                "vocab_ids": vocab_ids,
                "grammar_item_ids": grammar_item_ids,
            }
        )

    debts = comprehension_debt(conn, sorted(debt_wanted), now=moment)

    entries: list[dict[str, Any]] = []
    for analysis in analysed:
        candidate: Candidate = analysis["candidate"]
        measured = analysis["coverage"]
        by_item = [
            debts[item_id]
            for item_id in (
                *analysis["grammar_item_ids"],
                *analysis["vocab_ids"],
            )
            if item_id in debts
        ]
        total_debt = round(sum(entry["debt"] for entry in by_item), 4)
        grammar_debt = round(
            sum(
                debts[item_id]["debt"]
                for item_id in analysis["grammar_item_ids"]
                if item_id in debts
            ),
            4,
        )
        entries.append(
            {
                "id": candidate.id,
                "text": candidate.text,
                "source": candidate.source,
                "accepted": not analysis["reasons"],
                "gated_by": list(dict.fromkeys(analysis["reasons"])),
                "coverage": {
                    "known_pct": measured["known_pct"],
                    "known_ratio": measured["known_ratio"],
                    "band": measured["band"],
                    "counted_tokens": measured["counts"]["counted_tokens"],
                    "unknown_types": measured["unknown_types"],
                    "unknown": measured["unknown"],
                },
                "grammar": {
                    "ids": list(analysis["reachability"]),
                    "resolved_from": analysis["grammar_source"],
                    "reachable": not analysis["unreachable"],
                    "new": [entry["canonical_id"] for entry in analysis["new_points"]],
                    "unresolved": [
                        entry["canonical_id"]
                        for entry in analysis["unresolved_points"]
                    ],
                    "unreachable": [
                        {
                            "id": entry["canonical_id"],
                            "missing_prereqs": [
                                missing["id"] for missing in entry["missing_prereqs"]
                            ],
                        }
                        for entry in analysis["unreachable"]
                    ],
                    "points": list(analysis["reachability"].values()),
                },
                "debt": {
                    "total": total_debt,
                    "grammar": grammar_debt,
                    "vocab": round(total_debt - grammar_debt, 4),
                    "by_item": by_item,
                },
                "difficulty": analysis["difficulty"],
            }
        )

    accepted = [entry for entry in entries if entry["accepted"]]
    gated = [entry for entry in entries if not entry["accepted"]]
    # Debt first — material that touches what the learner owes is the point.
    # Then higher coverage (the more comprehensible of two equally useful
    # candidates), then fewer new grammar points, then id and text so the order
    # never reshuffles between identical calls.
    accepted.sort(
        key=lambda entry: (
            -entry["debt"]["total"],
            -(entry["coverage"]["known_pct"] or 0.0),
            len(entry["grammar"]["new"]),
            entry["id"] or "",
            entry["text"],
        )
    )
    for position, entry in enumerate(accepted, start=1):
        entry["order"] = position

    by_reason: Counter[str] = Counter()
    for entry in gated:
        by_reason.update(entry["gated_by"])

    logger.info(
        "find_i_plus_one: %d offered, %d accepted, %d gated (%s)",
        len(entries),
        len(accepted),
        len(gated),
        ", ".join(f"{code}={count}" for code, count in sorted(by_reason.items()))
        or "none",
    )

    notes: list[str] = []
    if not require_grammar:
        notes.append(
            "require_grammar=False: candidates whose grammar could not be "
            "established were offered on vocabulary coverage alone, which D-28 "
            "forbids by default."
        )
    if not accepted and gated:
        notes.append(
            "every candidate was gated out; 'counts.by_reason' names why, and "
            "include_gated=True returns each candidate's measurements."
        )
    datasets = (
        {
            DATASET_JREADABILITY: readability_model.status.as_dict(),
            DATASET_BCCWJ: frequency_list.status.as_dict(),
            DATASET_JLPT: jlpt_levels.status.as_dict(),
        }
        if score_difficulty
        and readability_model is not None
        and frequency_list is not None
        and jlpt_levels is not None
        else {}
    )
    absent = [
        name for name, status in datasets.items() if not status["available"]
    ]
    if absent:
        notes.append(
            "difficulty is scored without "
            f"{', '.join(absent)} (not vendored, or not verifiable); "
            "'difficulty_datasets' says why per dataset, and each candidate's "
            "'difficulty.weight_used' says how much of the score that left. "
            "Gating and ranking are unaffected."
        )

    return {
        "ok": True,
        "candidates": accepted[:top],
        "gated": gated if include_gated else [],
        "counts": {
            "offered": len(entries),
            "accepted": len(accepted),
            "returned": len(accepted[:top]),
            "gated": len(gated),
            "by_reason": dict(by_reason),
            "unannotated": sum(
                1
                for entry in entries
                if entry["grammar"]["resolved_from"] == GRAMMAR_FROM_NOTHING
            ),
        },
        "gates": {
            "min_coverage_pct": float(min_coverage_pct),
            "max_unknown_types": max_unknown_types,
            "max_new_grammar": max_new_grammar,
            "min_understanding": min_understanding,
            "require_grammar": bool(require_grammar),
            "production": bool(production),
            "reachability_edge_type": EDGE_PREREQ,
        },
        "ranked_by": RANKED_BY_DEBT,
        "scored_difficulty": bool(score_difficulty),
        "difficulty_datasets": datasets,
        "as_of": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "known_queries": resolver.queries,
        "mastery_queries": mastery.queries,
        "mastered_nodes": len(mastery.mastered_ids()),
        "note": " ".join(notes) or None,
    }


__all__ = [
    "BAD_GATE",
    "BAD_LIMIT",
    "BAD_TOP_UNKNOWN",
    "BAD_WEIGHTS",
    "BCCWJ_COLUMNS",
    "BCCWJ_DIR_NAME",
    "BCCWJ_ZIP_GLOB",
    "COMPONENT_COVERAGE",
    "COMPONENT_FREQUENCY",
    "COMPONENT_JLPT",
    "COMPONENT_READABILITY",
    "COVERAGE_BANDS",
    "CREDIT_ATTRIBUTION",
    "CURRICULUM_CYCLE",
    "CURRICULUM_EMPTY",
    "CURRICULUM_UNAVAILABLE",
    "CURRICULUM_VAULT_PATH",
    "DATASET_AMBIGUOUS",
    "DATASET_BCCWJ",
    "DATASET_CHECKSUM",
    "DATASET_JLPT",
    "DATASET_JREADABILITY",
    "DATASET_MALFORMED",
    "DATASET_MISSING",
    "DATASET_UNREADABLE",
    "DEBT_ASSISTED",
    "DEBT_ATTRIBUTION",
    "DEBT_CLEAN_CREDIT",
    "DEBT_FROM_CACHE",
    "DEBT_FROM_CACHE_TAIL",
    "DEBT_FROM_NOTHING",
    "DEBT_FROM_OBSERVATIONS",
    "DEBT_HALF_LIFE_DAYS",
    "DEBT_MISS",
    "DEFAULT_CANDIDATE_LIMIT",
    "DEFAULT_CANDIDATE_TOP_UNKNOWN",
    "DEFAULT_MAX_NEW_GRAMMAR",
    "DEFAULT_MAX_UNKNOWN_TYPES",
    "DEFAULT_MIN_COVERAGE_PCT",
    "DEFAULT_MIN_UNDERSTANDING",
    "DEFAULT_TOP_CANDIDATES",
    "DEFAULT_TOP_RARE",
    "DEFAULT_TOP_UNKNOWN",
    "DIFFICULTY_WEIGHTS",
    "EDGE_PREREQ",
    "EDGE_TYPES",
    "EDGE_UNLOCK",
    "EMPTY_TEXT",
    "FORMAT_ONLY_HEADINGS",
    "FUNCTION_POS1",
    "GATE_COVERAGE_TOO_LOW",
    "GATE_GRAMMAR_UNKNOWN",
    "GATE_NOT_AUDIO_ANCHORED",
    "GATE_NO_CONTENT",
    "GATE_SEALED",
    "GATE_TOO_MANY_UNKNOWN",
    "GATE_TOO_MUCH_NEW_GRAMMAR",
    "GATE_UNREACHABLE_GRAMMAR",
    "GOSHU_KANGO",
    "GOSHU_WAGO",
    "GRAMMAR_DAG_CYCLE",
    "GRAMMAR_FROM_EDGES",
    "GRAMMAR_FROM_EXPLICIT",
    "GRAMMAR_FROM_HOME_TOPIC",
    "GRAMMAR_FROM_NOTHING",
    "GRAMMAR_ID_PREFIX",
    "GRAMMAR_ID_RE",
    "GRAMMAR_KIND",
    "IGNORED_POS1",
    "JLPT_DIFFICULTY",
    "JLPT_DIR_NAME",
    "JLPT_FILE_RE",
    "JLPT_FRONT_FIELD",
    "JLPT_LEVELS",
    "JLPT_REQUIRED_TABLES",
    "JREADABILITY_DIR_NAME",
    "JREADABILITY_MODULE_SUFFIX",
    "JREADABILITY_SDIST_GLOB",
    "KEY_LEMMA",
    "KEY_READING",
    "KEY_SURFACE",
    "MASTERY_KNOWN_SET",
    "MASTERY_UNDERSTANDING",
    "MAX_CANDIDATES",
    "MAX_CANDIDATE_CHARS",
    "MAX_COVERAGE_CHARS",
    "MAX_TOP_CANDIDATES",
    "MAX_TOP_UNKNOWN",
    "NO_CANDIDATES",
    "NO_COMPONENTS",
    "PARTICLE_POS1",
    "RANKED_BY_DEBT",
    "READABILITY_BANDS",
    "READABILITY_MAX",
    "READABILITY_MIN",
    "READABILITY_TERMS",
    "SENTENCE_ENDINGS",
    "SENTENCE_KIND",
    "SOURCE_DIAGRAM",
    "SOURCE_NODE_BLOCK",
    "STATE_AMBIGUOUS",
    "STATE_UNKNOWN",
    "STATE_UNSEEN",
    "TEXT_TOO_LARGE",
    "TOKENIZER_UNAVAILABLE",
    "TOO_MANY_CANDIDATES",
    "VERB_EXCLUDED_POS2",
    "VERB_POS1",
    "Candidate",
    "Curriculum",
    "CurriculumParseError",
    "DagEdge",
    "DagNode",
    "DatasetStatus",
    "FrequencyList",
    "GrammarDag",
    "IntelligenceError",
    "JlptLevels",
    "KnownLookup",
    "Mastery",
    "MasteryLookup",
    "ReadabilityFeatures",
    "ReadabilityModel",
    "Verdict",
    "as_candidate",
    "candidate_item_ids",
    "candidates_from_items",
    "combine_difficulty",
    "comprehension_debt",
    "coverage",
    "coverage_band",
    "coverage_from_morphs",
    "curriculum_path",
    "difficulty_datasets",
    "difficulty_for_me",
    "find_cycle",
    "find_i_plus_one",
    "frequency_profile",
    "grammar_reachability",
    "import_curriculum",
    "is_content_morph",
    "is_function_morph",
    "is_ignored",
    "jlpt_level_files",
    "jlpt_profile",
    "load_curriculum",
    "load_frequency_list",
    "load_grammar_dag",
    "load_jlpt_levels",
    "load_readability_model",
    "parse_curriculum",
    "readability_band",
    "readability_features",
    "readability_profile",
    "reset_difficulty_caches",
    "type_key",
]
