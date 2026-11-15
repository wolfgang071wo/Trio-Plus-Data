# Part block A: what the pedal learned

Block A (16,384 bytes per part, at `0x930 + 16384 * (part - 1)` in a
`.tlsd`) holds the pedal's analysis of the guitar part it was taught, plus
the band settings. Block B (10,752 bytes) is almost empty: it starts with
`present (0) / empty (1)`, the region length in KB, and the length again
once audio is recorded.

Status: **research**. Everything below was derived from the sample files in
this repository and one real card. Field names are interpretations.

## Settings (confirmed with the controlled series `tlsd_sequence_in_a`)

| Offset | Meaning | Values |
|---|---|---|
| 15560 | bass mode | 0 active, 1 simple root, 3 root riff |
| 15564 | genre | 0 Blues, 1 R&B, 2 Rock, ... |
| 16160 | style | 0 = style 1, 1 = 2, 2 = 3 |
| 16244 | alt time | 0 off, 1 on |
| 16144, 16152 | change with genre (Rock 2/5, Blues and R&B 0/0) | |

Changing tempo with alt time on (sample 04) changes no other byte: tempo is
not stored, it follows from the loop length.

## Four analysis sections

Four sections of 3,736 bytes start at 516, 4252, 7988 and 11724. They look
like four interpretations of the same playing:

| Section | beats per bar (`+8`) | tempo |
|---|---|---|
| 0 | 4 | as estimated |
| 1 | 3 | as estimated |
| 2 | 4 | half |
| 3 | 3 | half |

Layout inside a section (u32, little-endian):

| Offset | Meaning (interpretation) |
|---|---|
| +0 | 1 in one section, 0 in the others: probably the chosen interpretation |
| +4 | number of bars |
| +8 | beats per bar (4 or 3) |
| +12 | small value 0..11, per song; perhaps a key estimate |
| +28.. | beat grid (0, 4, 8, ... or 0, 3, 6, ...) |
| +408.. | float32 tempo estimates, one per bar |
| +796.. | 128 x segment start, in beats, ascending; entries after the last valid one are stale |
| +1308.. | 128 x segment root, pitch class 0 = C ... 11 = B |
| +1820.. | 128 x chord-type code (0, 4, 6, 10, 14, 15, 20 seen) |

Evidence:

* Root at section+1308 is 0 in all C test files, 2 in all D files and 9 in
  all A files (`Song C1..C3`, `Song D1..D2`, `tlsd_sequence_in_a`).
* For the A series (2 bars at 108.9 BPM) section 2 reads "2 bars, 4/4,
  109.4 BPM, A"; section 0 reads "4 bars at about 218".
* In `tlsd_sample_data/01.tlsd` the roots form a plausible E-major song
  (E, B, A, C).

## Open questions

* The chord-type codes. Needed: test parts with one known chord each
  (A major, A minor, A5 power chord, A7, Am7, Asus4), all with the same
  length and settings.
* How many segments are valid (no count field identified yet; positions
  stop ascending after the last one).
* The meaning of +12 and of the integer table at 15544..16240.

`tools/decode_chords.py SONG.tlsd` prints the current decoding.
