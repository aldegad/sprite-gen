# Prompt assembly — the caller's text, then the engine's pieces, each said once

A prompt the engine sends is the caller's own text and then the engine's sentences. Every engine
sentence is a **piece** with a topic, and every piece goes on through one place
(`sprite_gen/gen/prompt_parts.py`, `Prompt.add`). That place checks what the prompt already says, so
a piece is not said twice and two pieces do not disagree. The caller's text is never edited.

## The pieces

**A still** (`sprite-gen gen`, `gen.still_prompt`), in this order:

| Piece | When | Sentence from |
|---|---|---|
| the caller's text | always | `--prompt` / `--prompt-file` |
| view | `--direction` | `batch.still_view_text` |
| handed | `--direction --handed` | `handedness.text` |
| facing | `--ref` with `--facing` | `facing.prompt_suffix` |
| key background | `--transparent --ref` when `auto` plans a key | `chroma.KEY_BACKGROUND_TEXT` |
| layout guide | `--layout-guide` | `gen.layout_guide_text` |

With `--direction`, the view sentence says the turn, so the facing piece says only what it does not:
"Left here means toward the left edge of the image, regardless of the reference image's
orientation. Preserve the subject's design." Without `--direction` it says the turn itself ("The
subject must be facing left (toward the left edge of the image), …"). Said after a three-quarter
view sentence, that full-profile wording asked for a different turn than the view's 45 degrees.

A facing correction (`--facing-fix regen`) regenerates with the facing piece **replaced** by its
correction ("CORRECTION REQUIRED: …"), said last. It is not added after the sentence it corrects.

**A clip** (`video-prompt`, `video-set`, `batch.clip_prompt_parts`):

| Piece | When | Sentence from |
|---|---|---|
| motion | always | the state's own (`MOTION_TEXT`, `VIEW_MOTION_TEXT`), or the caller's `--motion` |
| hold, gait hold, repeat | a caller's `--motion` | `HOLD_TEXT`, `GAIT_HOLD_TEXT`, `REPEAT_TEXT` |
| view, frame, camera, background, design, rhythm | always | `VIEW_TEXT` in `COMMON_TEXT` / `PINNED_LOOP_TEXT` / `ACTION_COMMON_TEXT` |
| Lite walk, Lite head | a Lite model's walk | `LITE_WALK_TEXT`, `LITE_HEAD_TEXT` |
| handed | `--handed` | `handedness.text(clip=True)` |
| side view | `sprite-gen video --direction side` | `video.side_view_prompt` |

The walk paragraph says a thing once too. That a gait does not move across the screen is the frame
sentence's ("does not move across the screen"); the side and diagonal gait sentences and holds no
longer add "without moving across the screen". A walk or run seen from the front, from behind or at
a diagonal says its view in the gait sentence or hold ("facing the viewer", "keeps the exact
three-quarter back angle of the image"), so the view sentence is left out of its prompt
(`batch.GAIT_SAYS_VIEW`); a side gait keeps the view sentence, which carries the facing.

`video --direction side` holds the side view ("The subject stays in exact side view, facing left. No
turning around.") only for a prompt that does not say it already: a `video-prompt` or `video-set`
prompt does ("seen from the exact side, facing left") and is sent as it is.

The handed piece of a clip is one sentence per wrist (or other part), not per item: two items on one
wrist share it, and with an item on each wrist neither sentence calls the other wrist bare. In a
side view's walk or run it says the far arm's item shows each time that arm swings forward
([handedness](video-pipeline.md#handedness--an-item-on-one-side)).

**A front or back walk's start still** (`batch.walk_start_prompt`): the mid-step redraw sentence,
the key background line, then the still's handed sentences.

## What "already said" means

- **The same sentence.** A sentence the prompt already carries, word for word, is dropped from the
  piece. A start-still prompt handed back to `gen --direction --handed` keeps one copy of the handed
  sentences; a `--motion` paragraph that quotes an engine rule ("Camera completely locked, …") is
  not followed by the rule again.
- **The same topic** (`prompt_parts.ALREADY_SAID`). A key background the prompt names leaves the key
  line out. It is read from the hex code (`#FF00FF`, `00ff00`), the key's name before "background",
  "backdrop", "screen" or "chroma key" ("a green screen", "magenta-colored background"), the name
  after one ("a background of pure magenta", "background: solid green"), or "keyed on magenta" /
  "on a green key" (`chroma.named_key_background`). A colour in the subject is not a key ("a green
  frog", "holding a green key"), and neither is one the prompt rules out ("no green screen",
  "without a magenta background").
- **The opposite, in the engine's own words, is refused** before anything is generated: a prompt
  that rules out the key the engine would ask for ("no green screen" with `--chroma-key green`), and
  a clip prompt that says the side view faces one way sent to `video --direction side --facing` the
  other.

## The caller's own words

The engine cannot tell which of two statements the caller meant, so it reports and does not edit:

| The text says | The option says | Result |
|---|---|---|
| "facing left", "walks to the left" | `--facing right`, or a front or back view | a **conflict**: a warning on stderr |
| "a black smartwatch on its right wrist" | `--handed "the black smartwatch=left wrist"` | a **conflict**: a warning on stderr |
| "facing right" | `--facing right` | a **repeat**: a note that it is said twice |
| "a black smartwatch on its left wrist" | the same `--handed` | a **repeat**: a note |

`gen` prints them as `[gen] warning: …` / `[gen] note: …` and records them in the report's
`extra.prompt_notes`. `video-prompt --json` puts conflicts in `warnings` and all of them in `notes`.
`video-set` records them per item as `prompt_notes`.

## The check

`tests/gen/test_prompt_assembly.py` draws every prompt in the option table as text, with no
generation: five views, both facings, no item / one / two on one wrist / one on each wrist, a
caller's text that names the key, the turn or the item's side or does not, every state, both clip
models, a caller's own motion, the facing correction, the start still and the hand-offs between
verbs. Each prompt is checked for a sentence said twice, words that turn the subject both ways, and
more than one key background. A new option is a new row in that file's tables.
