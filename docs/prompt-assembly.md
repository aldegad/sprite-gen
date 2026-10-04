# Prompt assembly — the caller's text, then the engine's pieces

A prompt the engine sends is the caller's own text and then the engine's sentences. Every engine
sentence is a **piece** with a topic, and every piece goes on through one place
(`sprite_gen/gen/prompt_parts.py`, `Prompt.add`). The caller's text is never edited.

**A piece goes on only where its condition holds.** The arm sentences go on only for a `--handed`
item on a wrist, a hand, a forearm or an elbow (`handedness.limb`, one table).

## Without `--handed`, 2.22.0's prompts

The prompts a character with no handed item is drawn and filmed from are 2.22.0's, byte for byte:
clips (built-in or `--motion`, every model and pin), `video-prompt`, stills (view, facing,
reference, key line, layout guide, the `--facing-fix regen` regeneration), `video --direction side`
and the mid-step redraw. `tests/gen/test_prompt_freeze.py` compares every prompt string in that table
with `tests/fixtures/prompts-v2.22.0.json.gz`, drawn from the 2.22.0 tag's source by
`tests/gen/prompt_freeze_table.py`. The notes and warnings printed beside a prompt are not part of
that comparison. A change to these prompts is made only after a before-and-after comparison on the
app's default clip model shows it is better.

`--handed` adds its piece and changes nothing else: the prompt is the one without it, with the handed
sentences put in (`tests/gen/test_prompt_assembly.py`).

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

A facing correction (`--facing-fix regen`) regenerates with the correction ("CORRECTION REQUIRED: …")
said after the first prompt (`facing.prepare_correction`).

**A clip** (`video-prompt`, `video-set`, `batch.clip_prompt_parts`): the motion paragraph (the state's
own, `MOTION_TEXT` / `VIEW_MOTION_TEXT`, or the caller's `--motion` with `HOLD_TEXT`, `GAIT_HOLD_TEXT`
and `REPEAT_TEXT`), the view, frame, camera, background, design and rhythm rules (`VIEW_TEXT` in
`COMMON_TEXT` / `PINNED_LOOP_TEXT` / `ACTION_COMMON_TEXT`) and a Lite model's walk sentences
(`LITE_WALK_TEXT`, `LITE_HEAD_TEXT`), as 2.22.0 put them together; then the handed piece
(`handedness.text(clip=True)`). `sprite-gen video --direction side` adds the side view's hold after
the prompt (`video.side_view_prompt`).

The handed piece of a clip is one sentence per wrist (or other part), not per item: two items on one
wrist share it, and with an item on each wrist neither sentence calls the other wrist bare. In a
side view's walk or run with an item on a wrist, a hand, a forearm or an elbow it says the arms swing
(`handedness.ARMS_SWING_TEXT`, the one place that sentence lives) and that the far arm's item shows
each time that arm swings forward
([handedness](video-pipeline.md#handedness--an-item-on-one-side)). An item anywhere else — an ear,
the head, a tail, the body, a leg, a shoulder — gets only where it is, and no arm sentence.

**A front or back walk's start still** (`batch.walk_start_prompt`): the mid-step redraw sentence,
the key background line, then the still's handed sentences.

## What "already said" means

- **A key background** (`prompt_parts.ALREADY_SAID`): a still's prompt that names one leaves the key
  line out. It is read from the hex code (`#FF00FF`, `00ff00`) or the key's name right before
  "background", "backdrop", "chroma key", "key" or "screen" (`chroma.named_key_background`), as
  in 2.22.0.
- **The handed piece** (`prompt_parts.ONCE_ONLY`): a sentence the prompt already carries, word for
  word, is dropped from it. A start-still prompt handed back to `gen --direction --handed` keeps one
  copy of the handed sentences.

Every other piece is said as 2.22.0 said it, even where the prompt already says it.

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
`video-set` records them per item as `prompt_notes`. The prompt itself is the same with or without
them.

## Known faults kept for now

2.22.0's prompts have faults the freeze keeps until a before-and-after comparison says to change
them, most urgent first:

1. A key background is read only as above: "no green screen" reads as asking for green (a
   `--transparent --ref` run then gets no key line and may come back on a light ground), and "a
   background of pure magenta" or "magenta-colored background" is not read (the key line goes on a
   second time).
2. With `--ref --direction --facing` the facing piece says the turn again after the view sentence,
   as a full profile ("The subject must be facing left (toward the left edge of the image)"),
   against a three-quarter view's 45 degrees.
3. A `--motion` paragraph that quotes an engine rule is followed by the rule again.
4. A `--facing-fix regen` regeneration carries the facing sentence twice: the first and the
   correction.
5. `video --direction side` adds its hold to a prompt that already has it, and to one that faces the
   other way.
6. A walk or run says "in place" and its view twice: the side and diagonal gait sentences end
   "without moving across the screen" as the frame sentence does, and a front, back or diagonal one
   says its view in the gait sentence and again in the view sentence.

## The checks

`tests/gen/test_prompt_freeze.py` holds every prompt string without `--handed` to 2.22.0's.
`tests/gen/test_prompt_assembly.py` draws every prompt in the option table as text, with no
generation: five views, both facings, no item / one / two on one wrist / one on each wrist / one on an
ear, a caller's text that names the key, the turn or the item's side or does not, every state, both
clip models, a caller's own motion, the start still and the hand-offs between verbs. Each is checked
for the prompt without `--handed` plus the handed piece and nothing else, a handed sentence said
twice, a part called bare and dressed at once, an arm sentence where no wrist item is or no step
swings it, and the notes. A new option is a new row in that file's tables.
