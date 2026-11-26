# FFmpeg for PlayerWorks

This is a fork of [FFmpeg](https://github.com/FFmpeg/FFmpeg), kept for the video player PlayerWorks (and
FileWorks, which hosts its player). It is FFmpeg at commit `094a2f8a2a` (2026-09-16) with a few changes to
one part only: the **MP4/MOV demuxer** (`libavformat/mov.c`, `libavformat/isom.h`). Everything else is
FFmpeg as it was. FFmpeg's own README is in [README.FFmpeg.md](README.FFmpeg.md).

The branch is **`playerworks`**. PlayerWorks builds its libmpv (LGPL, FFmpeg without `--enable-gpl`) from a
fixed commit of it.

## Why

PlayerWorks plays films from network shares (SMB through FileWorks, HTTP), where every read and every seek
costs a round trip. Two kinds of MP4 behaved badly there:

- **Ordinary MP4s** (one index, the `moov`): after reading the index, the demuxer went on walking every
  remaining top-level box to the end of the file, a seek per box. Over a network a large film took a long
  time to open; on Android some never started.
- **Fragmented MP4s without a fragment index** (no `sidx`, no `mfra`; common for films saved from streaming
  sites and for screen recordings): the demuxer read every fragment header up to the end of the file before
  returning the first packet, and a far seek had to read every fragment before the target.

## What changed

| Commit | Change |
|---|---|
| `87739df68e` | A seekable file that is not fragmented stops once `moov` and the first `mdat` are read, instead of walking the rest of the file. |
| `607646bf01` | A seekable fragmented file without a fragment index is read one fragment at a time as it is played; where each fragment's data ends is kept (`MOVFragmentIndexItem.data_end`). |
| `cbfd64363e` | A seek in such a file **estimates** where the wanted time lies (from the fragments already read, at first from the duration and the file size), finds the first `moof` after that point by its signature, reads only its header (its `tfdt` gives its time) and narrows down. A far seek then takes a few small reads. |
| `f8723e00ff` | The estimate, once narrowed to the fragments around the target, looks where the fragment before ends instead of halving further (it could run out of steps and land a few fragments early); it uses the longer of the `mvhd` duration and the format's. |
| `256df0c2c8` | A change to `mov_read_trun()`'s current-sample adjustment, made for a seek problem. |
| `788f87bc82` | **Reverted** `256df0c2c8`: when playback reaches an unread fragment and the demuxer reads it to fill the gap, its samples must become current; with the change that fragment was skipped (a 6 s jump after a seek). |
| `162f15bd64` | The problem `256df0c2c8` was meant for, solved in the estimate instead: the fragments it reads for one stream no longer move another stream's current sample (after a seek, aligning the audio had moved the video back a fragment). |

## How it was tested

The branch `playerworks-ci` adds a GitHub Actions job (`.github/workflows/playerworks-test.*`, not part of
the changes) that builds FFmpeg at the base commit and with the changes, makes MP4 test files (plain,
fragmented without an index, with `mfra`, 6-second fragments with a keyframe every 2 s, and fragmented files
with a real duration in `mvhd`), and compares the two builds packet by packet:

- reading each file in full: identical;
- seeks to the start and into the middle of fragments, with `ffmpeg -ss`, with `ffprobe -read_intervals`
  after 2 s of play, and with a small program seeking the way mpv does (`av_seek_frame`, `AVSEEK_FLAG_BACKWARD`):
  the same landing and the same 450 packets after it;
- what each reads: opening an unindexed fragmented file and playing a second, about 1.6 MB and 39 seeks
  instead of 7.5 MB and 219; a far seek, about half the bytes and a third of the seeks.

Known open point (2026-11-26): in PlayerWorks, far seeks in one real film (fragmented, 6 s fragments, a
correct duration) over a slow link land a fragment late and then go back to the skipped fragment. The
FFmpeg-level tests above do not reproduce it, the libmpv builds from before these fixes do the same, and it
is being looked into on the player's side (mpv's reading) as well.

## History

- 2026-11-24: the changes started as an edit script inside PlayerWorks' libmpv build recipes, fixing MP4s
  from network shares that would not start on Android.
- 2026-11-25: moved into this fork as commits in FFmpeg's style; a test job compares them with FFmpeg; seek
  fixes found by it (`f8723e00ff`, `256df0c2c8`); PlayerWorks' recipes switched to the fork.
- 2026-11-26: a skip after far seeks traced to `256df0c2c8`, reverted and solved differently (`162f15bd64`);
  PlayerWorks' libmpv release `libmpv-2026-11-26` is built from it.

## Licence

As FFmpeg: the LGPL version 2.1 or later for these files (see [COPYING.LGPLv2.1](COPYING.LGPLv2.1) and
[README.FFmpeg.md](README.FFmpeg.md)). The changes may be offered to FFmpeg upstream later.
