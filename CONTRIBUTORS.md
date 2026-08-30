# Contributors

The following external contributors have work that is incorporated into, or materially informed, the current `sprite-gen` codebase.

## [@devswha](https://github.com/devswha)

- [PR #1](https://github.com/aldegad/sprite-gen/pull/1): protected subject colors during chroma cleanup and improved automatic key selection.

## [@bokjk](https://github.com/bokjk)

- [PR #2](https://github.com/aldegad/sprite-gen/pull/2): made machine-readable manifest paths use portable POSIX separators. The accepted change was ported into the reorganized package implementation.

## [@Dongkyu-ES](https://github.com/Dongkyu-ES)

- [PR #4](https://github.com/aldegad/sprite-gen/pull/4): made CLI help tests deterministic in colored shell environments.
- [PR #5](https://github.com/aldegad/sprite-gen/pull/5): added Aseprite JSON, Phaser-compatible, and Flame-compatible exports.
- [PR #6](https://github.com/aldegad/sprite-gen/pull/6): introduced character and effect subject profiles. The accepted implementation uses resolution-aware sparse-frame floors.

## [@napkn34](https://github.com/napkn34)

- [PR #7](https://github.com/aldegad/sprite-gen/pull/7): fixed Windows provider CLI resolution and UTF-8 subprocess I/O.
- [PR #9](https://github.com/aldegad/sprite-gen/pull/9): added the Windows `LockFileEx` backend for shared readers and exclusive publishers.

## [@monibu1548](https://github.com/monibu1548)

- [PR #11](https://github.com/aldegad/sprite-gen/pull/11): made vertical centering work in the pixel-unfake row-placement path and exposed per-frame vertical grounding through the CLI.

## [@seunghan91](https://github.com/seunghan91)

- [PR #3](https://github.com/aldegad/sprite-gen/pull/3): proposed lowering the default chroma key threshold from `96` to `80` after a 705-case offline sweep. The PR was not merged, but its benchmark prompted a real-pipeline comparison that confirmed `96` as the canonical default.

## [@smnchoi](https://github.com/smnchoi)

- [Issue #15](https://github.com/aldegad/sprite-gen/issues/15): reproduced the root-skill installer failure and identified the explicit `--name sprite-gen` argument required for the documented Codex installation command.

Thank you for testing the project in real environments, documenting failures clearly, and contributing fixes or experiments that inform `sprite-gen`.
