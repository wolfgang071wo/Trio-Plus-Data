# Trio-Plus-Data

> **Fork note.** This fork adds `trioplus`, a small library and a
> non-interactive command line built on the format knowledge collected in
> this repository. The original tool and GUI are unchanged and documented
> further below.

## `trioplus`: library and command line

Pure Python 3.9+, no dependencies, runs on Linux, macOS, Windows and a
Raspberry Pi.

```sh
pip install .            # installs the `trio` command
python -m trioplus ...   # or run it without installing
```

### Commands

```sh
trio info SONG.tlsd ...                  # parts, track lengths, band settings
trio info --json SONG.tlsd               # same, machine readable

trio extract SONG.tlsd ... -o wavs/      # one WAV per recorded track
trio extract SONG.tlsd --tracks main --trim

trio build -o New.tlsd 1=verse.tlsd:2 2=chorus.tlsd:1   # DST=FILE:SRC
trio rename SONG.tlsd "Riff in A" -o Riff.tlsd

trio card TRIOPLUS.DAT                   # recorded parts on the SD card file
trio card TRIOPLUS.DAT --extract songs/  # write them out as .tlsd, no TRIO Manager needed
trio scan IMAGE --extract songs/         # find .tlsd songs embedded in any file
```

`card` reads the `TRIOPLUS.DAT` container on the SD card directly; the
layout is documented in `trioplus/card.py`. Audio and both settings blocks
of every part come out byte-identical to a TRIO Manager export. Song n owns
audio slots (n-1)*5 to (n-1)*5+4, so songs are written with their number and
name (`01 Song 01.tlsd`, ...); `--song N` limits the export. Both commands
only read their input.

`build` starts from an empty song and fills the listed parts, so it also
covers reordering, copying and deleting parts. Every command accepts
`--json`. Inputs are never modified: outputs must be new files, and existing
files are only replaced with `--force`.

### App (web interface)

```sh
trio gui                       # or: python -m trioplus.gui
trio gui E:\TRIOPLUS.DAT       # open a card right away
trio gui --host 0.0.0.0        # reachable from a tablet in the same network
```

A small local server opens the TRIO Tool in the browser: the 12 songs of a
card or any .tlsd files, waveforms of every track, playback, WAV and .tlsd
export, and a builder for new songs made of parts from any song. Windows
users can download `TrioTool.exe` from the releases; it is built by
`.github/workflows/windows-exe.yml`. Fonts: Barlow (SIL Open Font License,
see `trioplus/gui/static/fonts/OFL.txt`).

### Library

```python
from trioplus import Song

song = Song.load("05.tlsd")
for part in song.parts:
    if part.trained:
        print(part.number, part.describe()["tracks"])

new = Song.empty()
new.set_part(1, song.part(4))
new.name = "Outro first"
new.save("Outro first.tlsd")
```

### File format

| Region | Offset | Size |
|---|---|---|
| Global header | `0x00000` | 2,352 B |
| Part block A ×5 (looper/band settings) | `0x00930` | 16,384 B each |
| Part block B ×5 (learned band part) | `0x14930` | 10,752 B each |
| Audio area, one region per trained part | `0x21B30` | sum of part lengths |

* Part lengths (u32 at 1300, 1304, …, 1316) are **region sizes in bytes**,
  not samples. A region interleaves two tracks in 32 KB chunks: odd chunks
  are the first recording (`main`), even chunks the overdub slot
  (`overdub`). Each track is 16-bit mono PCM at 44.1 kHz, so a track lasts
  roughly `part_length / 4 / 44100` seconds.
* Bookkeeping fields rewritten on save: file size (u32 at 544), file size −
  1192 (632, 1320), part ends − 1192 (1248, 1256, 1264, 1272).
* Song name: NUL-terminated ASCII at 1332, 128-byte field.
* The 10,752-byte block B size matches the header values at 1232/1236
  (10,752 and 5 × 10,752), just as 1216/1220 hold 16,384 and 5 × 16,384 for
  block A. `trioplus` therefore copies both whole blocks when moving a part.
  The original tool copies only the first 20 bytes of block B.
* Band fields in block A (`bass_mode`, `genre`, `style`, `alt_time`) are
  taken from [freimann/trio-updater](https://github.com/freimann/trio-updater)
  and are provisional.

Open question, as already noted in the 2017-05-24 entry below: which of the
two interleaved tracks the pedal is currently playing (it might keep one as
an undo layer). In sample `05.tlsd` the two tracks correlate strongly, which
fits "one slot holds the mix, the other the previous state". `trioplus`
exports both and names them by slot, not by meaning.

### Tests

```sh
pip install pytest && pytest
```

Every sample file in this repository round-trips byte for byte, and part
detection and extracted audio are checked against the original tool.

### Writing to the pedal

Files built with `trio build` are structurally valid and pass the tests,
but they have **not yet been tested on a pedal**. Keep a copy of your SD
card before you load them.

---

## Original README

current version: release 0.80 as of 2018-01-09<br>
<i> current development under 'Trio+Tool' branch</i><br>
<i> updating the Trio+ hardware with 'TrioUpdaterInstaller_v2.0.2.exe' from Digitech website might be necessary</i><br>
<i> this is a Python 3.x script - it will NOT run under Python 2.x </i>

CONTENTS:<br>
00 <b>Status-miniblog<br></b>
01 TLDR<br>
02 Project Status<br>
03 Working<br>
04 Not working<br>
05 Getting started<br>
06 Going further<br>

<br><b>00 miniblog:</b><br>
<b>2018-01-09</b><br>
Released version 0.80. This release features a graphical user interface (GUI) based on Tk (comes included with python). The release includes all source files as well as a windows binary (.exe). If you're on linux, you can compile a binary from source. I have added a readme on some issues with that. If I find the time and motivation, I will polish the linux version and release an updated source along with a binary. Since you will need windows anyway to generate the .tlsd files with the Trio Manager from Digitech, I thought a windows release will do for the moment.<br>I will be happy to fix bugs - if you find any - but consider the tool more or less feature complete. I don't think it will be possible to extract midi data from the .tlsd files. I suppose these are generated 'on the fly' within the pedal. Nonetheless, there are some suspicious areas in the binary files that look somewhat like drum pattern. We'll see... As always: If you are interested, your help on reverse engineering the binary files would be more than welcome!<br>
Cheers!<br><br>

2017-12-20<<br>
Ho ho ho - christmas time is here and so I have uploaded the new release v0.75 of The Trio+ Tool. Now you can upload wave-audio to parts and show info on your .tlsd files. Info has been enhanced with part-duration in seconds. 'pyaudio' is required to enable upload-mix-preview. Will run without it but is much more convenient with pyaudio installed on your system. Automatic install of pyaudio has been tested under win and linux. Next up I will try to compile native binaries for win and linux ('.exe' files) with the help of 'pyinstaller'. Anybody out there willing to help with compiling for mac?!<br>Merry christmas and happy new year folks!<br>
Cheers!<br><br>

2017-11-27<br>
Been working on 'upload audio' feature. Was looking into ways to make a preview of the results since you might want to adjust the level of the uploaded audio to match the recorded guitar level within the song. There is the 'pyaudio' module which looks suitable. This implies that the user would have to install this module on their platform. At the moment I am able to test a guided install of pyaudio on Linux and Windows. From a bit of google search it looks as MacOs might be a bit troublesome. I cannot run tests on MacOs. However I will implement the 'upload' feature in a way that even without pyaudio the user will be able to use the feature - but then without previewing the results. So this might then be a bit trial and error to match the audio levels. As for now, the audio from the song will be read to memory and the mixing/preview is coded. I will now have to write the code to patch the mixed audio back in the .tlsd file and do a bit of testing. Also the implementation of the guided pyaudio installation is heavy time consuming but it will be an important part of the overall function so I feel this need to be done. As an experienced python user you might think it was easy as 'pip -install pyaudio' but this holds true only for windows. I was also experimenting with 'pyinstaller' to release a native Linux/Windows executable. Looks promising. This would of course be a more hassle-free approach since it would bundle the dependency on pyaudio. However you cannot cross-compile with pyinstaller thus no luck for a MacOs executable. Maybe a memeber of the community could provide a MacOs compiled version once the new release is out. As for now be patient and<br>
Cheers!<br><br>

2017-11-08<br>
There still seems to be interest in the project. Just received an email asking if there was progress on the 'upload audio' feature. Have had no time for coding the past months but will take this as an incentive to re-start working on this. So hang on please ;-)<br>
Cheers!<br><br>

2017-08-03<br>
Version 0.74 has been released today. I was requested to add a feature (song creation of parts from different songs). Done. Also made the UI a little nicer. Enabled copy/move of 'trained only' parts.<br>... Ahhh and added some 'Yoda says...' messages if user input is incorrect ;-)<br>
Cheers!<br><br>
2017-05-24<br>
Working on the 'upload audio' to a song functionality. Have not worked out how to determine which part of the interleaved audio that has been recorded is the one actually being in use currently. As a last resort, one could ask the user to decide but I am still hoping to figure that out. Maybe I will be pushing some code in the next weeks. Had to attend to other issues the last weeks.<br>
Cheers!<br><br>
2017-04-04<br>
New release (0.73, recommended). Copy, erase, move parts implemented.<br>
Cheers!<br>

<br><b>01 TLDR:</b><br>
* Ultimate goal is to extract audio and midi data from the Digitech Trio Plus pedal's SD card for further use in your preferred DAW.

see: http://digitech.com/en/products/trio-plus

* Written in PYTHON3 (no dependencies)
* Tested on Windows, Linux


<br><b>02 Project Status:</b><br>
good to work with; always get latest release!

<br><b>03 Working:</b><br>
extract audio; copy, move and erase parts; build new .tlsd file from parts of other .tlsd files (i.e. song creation of parts from different songs).<br>

<br><b>04 Not working:</b><br>
upload audio to part;<br>
Any MIDI data support is missing at the moment

<br><b>05 Getting started:</b><br>
download and install the latest Python version (3.x) for your computer from
https://www.python.org/downloads/

Record some stuff with the Trio Plus

Export the songs (the single files) onto your computer using the Digitech Trio Manager
http://digitech.com/en/softwares/

Download the latest RELEASE version of the script. Unpack the .zip file. Place the exported songs (*.tlsd) from the SD card into the unpacked folder and start 'The_Trio+_Tool.py'. Follow the instructions.

-> LINUX users remember: you might need to change the endlines of the script (as it was edited on windows) <-

The subfolder 'Trio_wav_export' will be created and the audio contents of the songs will be written herein

<br><b>06 Going further</b><br>
NEED YOUR HELP to further reverse-engineering the data.<br>
Look at my 'findings.txt' for what I found out so far

Let's have fun!
