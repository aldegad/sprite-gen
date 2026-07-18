# Motion Continuity QA (BLOCKING) — sprite-gen reference

> `SKILL.md` 허브에서 분리한 시나리오 상세. 추출·아틀라스 QA 를 통과한 행을 **모션으로서** 판정할 때 이 문서를 따른다. Motion Continuity 는 BLOCKING 이다 — 판정 기준 전체가 여기 있다.

Static identity QA is not enough. A row can have the right frame count, clean alpha, and consistent identity and still animate as garbage. Review motion **as motion**:

- Build a per-state contact sheet and an animated preview, then watch the loop:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/preview_animation.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This writes `qa/<state>-contact.png` (frames left-to-right) and `qa/<state>.gif` (played at the state `fps`).
The GIF is exported through the clean transparent GIF path (dedicated transparent index + disposal method 2), while the contact sheet uses a checker background for inspection.

## 판정 기준

- **Cyclic locomotion (walk / run):** the motion must read as continuous locomotion, not static bobbing. Review body rhythm, limb motion, foot contact stability, and whether the loop communicates the requested direction and speed.
- **Experimental locomotion boundary:** walk/run/frontwalk/45-frontwalk are not simple default pass states. They may be generated, but the report must call them experimental unless motion continuity passes cleanly.
- **Loop seam:** for `loop: true` states, the last frame must flow back into the first. A visible jump at the wrap is a fail.
- **Non-loop gestures:** for `loop: false` states such as attack, jump, hurt, or wave, judge start/middle/end readability instead of loop seam. Do not force a non-loop gesture into a loop just because it has multiple frames.
- **Humanoid caution:** humanoid joints (knees, elbows, hips, hands) are where diffusion drifts most. Review **every** frame for broken anatomy, extra/missing limbs, and limb-length changes. Humanoids need stricter per-frame review than blob/creature sprites — do not skim.
- **Independent second opinion (recommended for humanoids):** hand `qa/<state>.gif` (or the contact sheet) to a fresh independent vision-model pass (e.g. a codex vision session) and ask specifically: "does this read as continuous `<state>` motion; is the loop seamless; is the identity stable across frames; are there anatomy or jitter problems?" Trust a second judge over a single reviewer for motion calls.

## 실패 시

If a row fails motion continuity (static bobbing, jitter, anatomy break, identity drift, or a hard loop seam), **regenerate that row**. Do not repair motion by drawing or re-timing frames locally.

Record the per-state motion verdict in `qa-notes.md`.

## Related

- [`../SKILL.md`](../SKILL.md) — QA 자동 체크 목록 + report 포맷
- [`states-and-frames.md`](states-and-frames.md) — simple/experimental 상태 구분
- [`locomotion-curation.md`](locomotion-curation.md) — 부분 프레임만 쓸 만할 때의 selected-cycle 경로
