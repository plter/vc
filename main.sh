#!/bin/bash

# Configuration section, modify these variables according to your environment
AUDIOCPP_CLI="/tcvc/apps/audiocpp/audiocpp_cli"
MODEL_PATH="/tcvc/models/tone-color-vc-f32.gguf"
REF_VOICE="/tcvc/audioref/audio.wav"
OUTPUT_FILE="vc_out.wav"
TMP_RAW_WAV="_tmp_source_24k.wav"

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

echo "[1/2] Convert source audio to standard wav: 24000Hz, mono, 16bit PCM"
ffmpeg -y -i "$SOURCE_FILE" -ar 24000 -ac 1 -sample_fmt s16 "$TMP_RAW_WAV"
if [ $? -ne 0 ]; then
    echo "ERROR: ffmpeg audio conversion failed"
    exit 1
fi

echo "[2/2] Run voice conversion via audiocpp_cli"
"$AUDIOCPP_CLI" \
--task vc \
--model "$MODEL_PATH" \
--log \
--audio "$TMP_RAW_WAV" \
--voice-ref "$REF_VOICE" \
--out "$OUTPUT_FILE"

RET_CODE=$?

# Clean temporary wav file
rm -f "$TMP_RAW_WAV"

if [ $RET_CODE -eq 0 ]; then
    echo "SUCCESS: Voice conversion finished. Output file: $OUTPUT_FILE"
else
    echo "ERROR: audiocpp_cli execution failed"
    exit $RET_CODE
fi