#!/usr/bin/env bash
set -euo pipefail

BUCKET="${BUCKET:?BUCKET env var must be set}"
VIDEO_ID="$1"
PREFIX="finevideo/sports/${VIDEO_ID}"
N_FRAMES=16
WORK=$(mktemp -d)

# 1) 원본 영상 다운로드
aws s3 cp "s3://${BUCKET}/${PREFIX}/video.mp4" "${WORK}/video.mp4"

# 2) 균일 샘플링
DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "${WORK}/video.mp4")
FPS=$(echo "scale=6; ${N_FRAMES} / ${DURATION}" | bc)
mkdir -p "${WORK}/frames"
ffmpeg -y -i "${WORK}/video.mp4" \
  -vf "fps=${FPS},scale=448:448:force_original_aspect_ratio=decrease,pad=448:448:(ow-iw)/2:(oh-ih)/2" \
  -frames:v ${N_FRAMES} -q:v 2 \
  "${WORK}/frames/frame_%03d.jpg"

# 3) 결과를 S3에 업로드
aws s3 cp "${WORK}/frames/" "s3://${BUCKET}/${PREFIX}/frames/" --recursive

# 4) 정리
rm -rf "${WORK}"
