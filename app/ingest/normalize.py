"""Ingest normalization — Phase 2 (stub).

Every uploaded asset gets transcoded to ONE uniform spec so that segments
from different assets are interchangeable at playout time:

    ffmpeg -y -i INPUT \
      -c:v libx264 -preset veryfast -pix_fmt yuv420p \
      -vf scale=1280:720,fps=25 \
      -force_key_frames "expr:gte(t,n_forced*4)" -x264-params scenecut=0 \
      -c:a aac -ar 48000 -b:a 128k \
      -f segment -segment_time 4 -segment_format mpegts \
      OUTPUT_DIR/%05d.ts

Then ffprobe each segment for its exact duration and register the asset in
the catalog. Runs as a Celery task; segments land in MinIO/S3.

See scripts/make_demo_assets.py for the working Phase-0 version of this
pipeline (synthetic sources instead of uploads).
"""
