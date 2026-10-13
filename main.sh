#!/bin/bash

# Configuration section, modify these variables according to your environment
AUDIOCPP_CLI="/tcvc/apps/audiocpp/audiocpp_cli"
MODEL_PATH="/tcvc/models/tone-color-vc-f32.gguf"
REF_VOICE="/tcvc/audioref/audio.wav"
OUTPUT_FILE="vc_out.wav"

# Split configuration
SILENCE_NOISE="-35dB"        # Volume threshold below which audio counts as silence
SILENCE_MIN_DURATION="0.6"   # Minimum pause length (seconds) to trigger a split
MIN_SEGMENT_DURATION="1.0"   # Segments shorter than this (seconds) get merged with neighbors
WORK_DIR="_vc_work"          # Temporary working directory (removed on exit)

# Check input argument count, only accept exactly 1 argument
if [ $# -ne 1 ]; then
    echo "Usage: $0 <source_audio_file>"
    echo "Example: $0 input.mp3"
    exit 1
fi

SOURCE_FILE="$1"

# Verify source file exists
if [ ! -f "$SOURCE_FILE" ]; then
    echo "ERROR: Source file not found -> $SOURCE_FILE"
    exit 1
fi

# Always remove the temporary working directory on exit
cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR/segments" "$WORK_DIR/converted"

TMP_RAW_WAV="$WORK_DIR/_tmp_source_24k.wav"

echo "[1/4] Convert source audio to standard wav: 24000Hz, mono, 16bit PCM"
ffmpeg -y -i "$SOURCE_FILE" -ar 24000 -ac 1 -sample_fmt s16 "$TMP_RAW_WAV"
if [ $? -ne 0 ]; then
    echo "ERROR: ffmpeg audio conversion failed"
    exit 1
fi

echo "[2/4] Detect pauses and split audio into segments"

# Get total duration of the normalized wav
TOTAL_DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$TMP_RAW_WAV")
if [ -z "$TOTAL_DURATION" ]; then
    echo "ERROR: Failed to probe audio duration"
    exit 1
fi

# Detect silences and output one cut point per pause (midpoint of the silence),
# so half of each pause stays at the end/start of the neighboring segments
ffmpeg -i "$TMP_RAW_WAV" \
    -af silencedetect=noise=$SILENCE_NOISE:d=$SILENCE_MIN_DURATION \
    -f null - 2>&1 \
| awk '
    /silence_start:/ { for (i = 1; i <= NF; i++) if ($i == "silence_start:") start = $(i+1) }
    /silence_end:/   { for (i = 1; i <= NF; i++) if ($i == "silence_end:")   { end = $(i+1); printf "%.3f\n", (start + end) / 2 } }
' > "$WORK_DIR/cut_points.txt"

# Build "start end" segment boundaries from the cut points, dropping cuts that
# would produce segments shorter than MIN_SEGMENT_DURATION
awk -v total="$TOTAL_DURATION" -v min="$MIN_SEGMENT_DURATION" '
    BEGIN { prev = 0 }
    {
        cut = $1 + 0
        if (cut - prev >= min && total - cut >= min) {
            printf "%.3f %.3f\n", prev, cut
            prev = cut
        }
    }
    END { printf "%.3f %.3f\n", prev, total }
' "$WORK_DIR/cut_points.txt" > "$WORK_DIR/boundaries.txt"

# Extract each segment
SEGMENT_INDEX=0
while read -r SEG_START SEG_END; do
    SEG_FILE=$(printf "%s/segments/seg_%04d.wav" "$WORK_DIR" "$SEGMENT_INDEX")
    ffmpeg -y -i "$TMP_RAW_WAV" -ss "$SEG_START" -to "$SEG_END" \
        -ar 24000 -ac 1 -sample_fmt s16 "$SEG_FILE"
    if [ $? -ne 0 ]; then
        echo "ERROR: ffmpeg failed to extract segment $SEGMENT_INDEX ($SEG_START - $SEG_END)"
        exit 1
    fi
    SEGMENT_INDEX=$((SEGMENT_INDEX + 1))
done < "$WORK_DIR/boundaries.txt"

SEGMENT_COUNT=$SEGMENT_INDEX
echo "Split into $SEGMENT_COUNT segment(s)"
if [ "$SEGMENT_COUNT" -eq 0 ]; then
    echo "ERROR: No audio segments produced"
    exit 1
fi

echo "[3/4] Run voice conversion on each segment via audiocpp_cli"
CONCAT_LIST="$WORK_DIR/concat_list.txt"
: > "$CONCAT_LIST"

i=0
for SEG_FILE in "$WORK_DIR"/segments/seg_*.wav; do
    BASE=$(basename "$SEG_FILE" .wav)
    CONVERTED_FILE="$WORK_DIR/converted/${BASE}_vc.wav"
    echo "  ($((i + 1))/$SEGMENT_COUNT) Converting $BASE ..."
    "$AUDIOCPP_CLI" \
        --task vc \
        --model "$MODEL_PATH" \
        --log \
        --audio "$SEG_FILE" \
        --voice-ref "$REF_VOICE" \
        --out "$CONVERTED_FILE"
    if [ $? -ne 0 ]; then
        echo "ERROR: audiocpp_cli execution failed on segment $BASE"
        exit 1
    fi
    echo "file '$CONVERTED_FILE'" >> "$CONCAT_LIST"
    i=$((i + 1))
done

echo "[4/4] Merge converted segments into $OUTPUT_FILE"
ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$OUTPUT_FILE"
if [ $? -ne 0 ]; then
    echo "ERROR: ffmpeg concat failed"
    exit 1
fi

echo "SUCCESS: Voice conversion finished. Output file: $OUTPUT_FILE ($SEGMENT_COUNT segment(s) processed)"
