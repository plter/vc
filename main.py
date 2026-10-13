#!/usr/bin/env python3
"""Voice conversion pipeline (Python port of main.sh).

Splits the source audio at detected pauses, runs voice conversion on each
segment via audiocpp_cli, then concatenates the converted segments back
into a single output file.

Usage: python3 main.py <source_audio_file>
Set KEEP_WORK=1 to preserve the temporary working directory for debugging.
"""

import os
import re
import shutil
import subprocess
import sys

# Configuration section, modify these variables according to your environment
AUDIOCPP_CLI = "/tcvc/apps/audiocpp/audiocpp_cli"
MODEL_PATH = "/tcvc/models/tone-color-vc-f32.gguf"
REF_VOICE = "/tcvc/audioref/audio.wav"
OUTPUT_FILE = "vc_out.wav"

# Split configuration
SILENCE_NOISE = "-35dB"        # Volume threshold below which audio counts as silence
SILENCE_MIN_DURATION = 0.6     # Minimum pause length (seconds) to trigger a split
MIN_SEGMENT_DURATION = 1.0     # Segments shorter than this (seconds) get merged with neighbors
WORK_DIR = "_vc_work"          # Temporary working directory (removed on exit unless KEEP_WORK=1)

SAMPLE_RATE = 24000            # Target sample rate (Hz), mono, 16-bit PCM

SIL_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
SIL_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def die(message):
    """Print an error and exit; the finally block in main() still cleans up."""
    print("ERROR: " + message)
    sys.exit(1)


def run(cmd):
    """Run a command inheriting stdout/stderr; return its exit code."""
    try:
        return subprocess.run(cmd).returncode
    except OSError as exc:
        print("ERROR: cannot execute %s: %s" % (cmd[0], exc))
        return 127


def probe_duration(path):
    """Return the media duration in seconds, or None on failure."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
    except OSError as exc:
        print("ERROR: cannot execute ffprobe: %s" % exc)
        return None
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def detect_cut_points(wav_path):
    """Run silencedetect and return one cut point per pause (its midpoint),

    so half of each pause stays at the end/start of the neighboring segments.
    Values are floats end to end: no locale/format quirks, no leading-zero
    problems, and out-of-order or unpaired lines are simply ignored.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-i", wav_path,
             "-af", "silencedetect=noise=%s:d=%g" % (SILENCE_NOISE, SILENCE_MIN_DURATION),
             "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, errors="replace")
    except OSError as exc:
        die("cannot execute ffmpeg: %s" % exc)

    if proc.returncode != 0:
        print("WARNING: silencedetect exited with code %d; continuing with "
              "the cut points found so far" % proc.returncode)

    cuts = []
    start = None
    for line in proc.stdout.splitlines():
        m = SIL_START_RE.search(line)
        if m:
            start = float(m.group(1))
            continue
        m = SIL_END_RE.search(line)
        if m and start is not None:
            cuts.append((start + float(m.group(1))) / 2.0)
            start = None
    return sorted(cuts)


def build_segments(cut_points, total_duration, min_duration):
    """Build monotonic (start, end) segment boundaries from cut points.

    Cuts that would produce a segment shorter than min_duration are dropped,
    which merges the pause into the neighboring segment. Pure function, so it
    is trivially unit-testable.
    """
    segments = []
    prev = 0.0
    for cut in cut_points:
        if (cut > prev
                and cut - prev >= min_duration
                and total_duration - cut >= min_duration):
            segments.append((prev, cut))
            prev = cut
    if total_duration > prev:
        segments.append((prev, total_duration))
    return segments


def extract_segments(src_wav, segments, seg_dir):
    """Cut each (start, end) segment out of src_wav; return the file list."""
    files = []
    for index, (start, end) in enumerate(segments):
        seg_file = os.path.join(seg_dir, "seg_%04d.wav" % index)
        # -ss before -i: fast input seek (sample-accurate for PCM wav) instead
        # of decoding from the start of the file for every segment
        rc = run(["ffmpeg", "-y",
                  "-ss", "%.3f" % start,
                  "-i", src_wav,
                  "-t", "%.3f" % (end - start),
                  "-ar", str(SAMPLE_RATE), "-ac", "1", "-sample_fmt", "s16",
                  seg_file])
        if rc != 0:
            die("ffmpeg failed to extract segment %d (%.3f - %.3f)"
                % (index, start, end))
        files.append(seg_file)
    return files


def convert_segments(seg_files, conv_dir):
    """Run audiocpp_cli voice conversion on each segment; return outputs."""
    converted = []
    count = len(seg_files)
    for i, seg_file in enumerate(seg_files):
        base = os.path.splitext(os.path.basename(seg_file))[0]
        out_file = os.path.join(conv_dir, base + "_vc.wav")
        print("  (%d/%d) Converting %s ..." % (i + 1, count, base))
        rc = run([AUDIOCPP_CLI,
                  "--task", "vc",
                  "--model", MODEL_PATH,
                  "--log",
                  "--audio", seg_file,
                  "--voice-ref", REF_VOICE,
                  "--out", out_file])
        if rc != 0:
            die("audiocpp_cli execution failed on segment %s" % base)
        converted.append(out_file)
    return converted


def concat_segments(converted_files, list_file, output_file):
    """Merge converted segments into the final output via ffmpeg concat."""
    with open(list_file, "w", encoding="utf-8") as f:
        for path in converted_files:
            f.write("file '%s'\n" % path.replace("'", "'\\''"))
    rc = run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
              "-i", list_file, "-c", "copy", output_file])
    if rc != 0:
        die("ffmpeg concat failed")


def write_debug_artifacts(cuts, segments):
    """Persist cut points / boundaries for inspection (see KEEP_WORK=1)."""
    with open(os.path.join(WORK_DIR, "cut_points.txt"), "w", encoding="utf-8") as f:
        f.write("".join("%.3f\n" % c for c in cuts))
    with open(os.path.join(WORK_DIR, "boundaries.txt"), "w", encoding="utf-8") as f:
        f.write("".join("%.3f %.3f %.3f\n" % (s, e, e - s) for s, e in segments))


def main():
    prog = os.path.basename(sys.argv[0])
    if len(sys.argv) != 2:
        print("Usage: %s <source_audio_file>" % prog)
        print("Example: %s input.mp3" % prog)
        sys.exit(1)

    source_file = sys.argv[1]
    if not os.path.isfile(source_file):
        die("Source file not found -> %s" % source_file)

    # Start from a clean working directory
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    seg_dir = os.path.join(WORK_DIR, "segments")
    conv_dir = os.path.join(WORK_DIR, "converted")
    os.makedirs(seg_dir)
    os.makedirs(conv_dir)

    tmp_raw_wav = os.path.join(WORK_DIR, "_tmp_source_24k.wav")

    try:
        print("[1/4] Convert source audio to standard wav: %dHz, mono, 16bit PCM"
              % SAMPLE_RATE)
        if run(["ffmpeg", "-y", "-i", source_file,
                "-ar", str(SAMPLE_RATE), "-ac", "1", "-sample_fmt", "s16",
                tmp_raw_wav]) != 0:
            die("ffmpeg audio conversion failed")

        print("[2/4] Detect pauses and split audio into segments")
        total_duration = probe_duration(tmp_raw_wav)
        if total_duration is None:
            die("Failed to probe audio duration")

        cut_points = detect_cut_points(tmp_raw_wav)
        segments = build_segments(cut_points, total_duration, MIN_SEGMENT_DURATION)
        write_debug_artifacts(cut_points, segments)

        seg_files = extract_segments(tmp_raw_wav, segments, seg_dir)
        print("Split into %d segment(s)" % len(seg_files))
        if not seg_files:
            die("No audio segments produced")

        print("[3/4] Run voice conversion on each segment via audiocpp_cli")
        converted = convert_segments(seg_files, conv_dir)

        print("[4/4] Merge converted segments into %s" % OUTPUT_FILE)
        concat_segments(converted,
                        os.path.join(WORK_DIR, "concat_list.txt"),
                        OUTPUT_FILE)

        print("SUCCESS: Voice conversion finished. Output file: %s "
              "(%d segment(s) processed)" % (OUTPUT_FILE, len(seg_files)))
    finally:
        # Always remove the temporary working directory on exit
        if os.environ.get("KEEP_WORK", "0") != "1":
            shutil.rmtree(WORK_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
