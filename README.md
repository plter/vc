# vc

**English** | [简体中文](README.zh-CN.md)

A one-command **Tone Color Voice Conversion** CLI tool, packaged as a Docker image.

`vc` converts any audio file into the timbre (tone color) of a reference voice, running entirely on CPU. It bundles [audio.cpp](https://github.com/0xShug0/audio.cpp) with the [Tone-Color-VC](https://www.modelscope.cn/models/audio-cpp/audio.cpp-gguf) GGUF model and `ffmpeg`, so you don't need to install Python, PyTorch, or anything else.

## How It Works

```
input audio (m4a / mp3 / wav / ...)
        │
        ▼
[1/4] ffmpeg          → resample to 24 kHz, mono, 16-bit PCM wav
        │
        ▼
[2/4] split           → detect pauses (ffmpeg silencedetect) and cut the
                        audio at the midpoint of each pause
        │
        ▼
[3/4] audiocpp_cli    → voice conversion (--task vc) with the
                        Tone-Color-VC model + built-in reference voice,
                        one segment at a time
        │
        ▼
[4/4] ffmpeg concat   → merge the converted segments back together
        │
        ▼
vc_out.wav
```

Splitting at detected pauses lets `vc` process long recordings piece by piece
instead of feeding the whole file to the model at once. Each cut falls in the
middle of a pause, so half of it stays on each side and the seams sound
natural; pieces shorter than 1 second are automatically merged with their
neighbors.

## Quick Start (Prebuilt Image)

Run the following command in the directory that contains your source audio file:

**Linux / macOS / Git Bash:**

```shell
docker run --rm -ti -v `pwd`:/work xtiqin/vc Untitled.m4a
```

**Windows PowerShell:**

```powershell
docker run --rm -ti -v ${PWD}:/work xtiqin/vc Untitled.m4a
```

**Windows CMD:**

```bat
docker run --rm -ti -v %cd%:/work xtiqin/vc Untitled.m4a
```

> **Note (Apple Silicon / ARM machines):** the bundled audio.cpp binary and model are built for `linux/amd64`. Add `--platform linux/amd64` to the command; it will run under emulation and be slower.

When it finishes, the converted audio is written to `vc_out.wav` in the same directory (i.e. the directory mounted to `/work`).

### Arguments

The container accepts **exactly one argument**: the path of the source audio file, relative to the mounted `/work` directory. Any input format supported by `ffmpeg` works (m4a, mp3, wav, flac, ogg, ...).

## Build the Image Yourself

```shell
git clone https://github.com/plter/vc.git
cd vc
docker build -t vc .
```

The build will:

1. Install `ffmpeg` and the Chinese locale on `ubuntu:24.04`
2. Download the audio.cpp CLI (v0.9.1, CPU portable, x64) from GitHub Releases
3. Download the `tone-color-vc-f32.gguf` model from ModelScope

Then run it the same way as the prebuilt image:

```shell
docker run --rm -ti -v `pwd`:/work vc Untitled.m4a
```

## Changing the Reference Voice

The target voice (the voice your audio will be converted **to**) is `audioref/audio.wav`, which is baked into the image at build time. To use your own target voice:

1. Replace `audioref/audio.wav` with your own recording (a clean single-speaker clip, ideally ~10 seconds, WAV preferred)
2. Rebuild the image (see [Build the Image Yourself](#build-the-image-yourself))

## Project Layout

| Path | Description |
| --- | --- |
| `Dockerfile` | Image build: ubuntu 24.04 + ffmpeg + audio.cpp + Tone-Color-VC model |
| `main.py` | Container entrypoint: ffmpeg preprocessing → pause detection & splitting → per-segment audiocpp_cli inference → concat |
| `audioref/audio.wav` | Built-in reference voice (conversion target timbre) |
| `LICENSE` | MIT License |

## Tuning & Debugging

Split behavior (silence threshold, minimum pause length, minimum segment
length, etc.) is configured via the constants at the top of `main.py`.

To keep the temporary working directory (`_vc_work/`, written to the mounted
`/work` directory) for inspection — it contains the raw segments, converted
segments, detected cut points and segment boundaries — run with `KEEP_WORK=1`:

```shell
docker run --rm -ti -e KEEP_WORK=1 -v `pwd`:/work xtiqin/vc Untitled.m4a
```

## Credits

- [audio.cpp](https://github.com/0xShug0/audio.cpp) — high-performance audio inference engine
- [Tone-Color-VC GGUF model](https://www.modelscope.cn/models/audio-cpp/audio.cpp-gguf) — hosted on ModelScope
- [ffmpeg](https://ffmpeg.org/) — audio preprocessing, pause detection and segment concatenation

## Disclaimer

This tool is provided for learning and research purposes. Voice conversion involves personality rights (voice rights) and, in many jurisdictions, legal restrictions. **Only convert voices you have explicit permission to use.** Do not use the output for fraud, impersonation, misinformation, or any purpose that harms others. You are solely responsible for how you use this tool.

## License

[MIT](LICENSE) © 2026 梦宇 (Muyu)
