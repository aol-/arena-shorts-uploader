# Arena Shorts Uploader (`arena-shorts-uploader`)

AI-assisted YouTube Shorts formatter, gameplay highlight analyzer, NVENC montage builder, and quota-aware upload queue for Linux arena FPS clips (Quake Live).

## Components

- **`shorts_uploader.py`**:
  - Extracts killfeed, scoreboard, weapon icons, and color-accurate Quake chat text (`^1`–`^8` mapped from frame HSV pixels) via `RapidOCR` + `OpenCV`.
  - Renders 1440x2560 @ 60fps vertical Shorts with optional HUD overlays, climax slow-motion zoom (`0.25x`), audio loudness normalization (`-15 LUFS`), and respawn/chime tail trimming via NVIDIA NVENC (`h264_nvenc`).
  - Queries live Steam A2S server metadata (`map`, `mode`, `server_name`) dynamically from `repconfig.cfg` or running process.
  - Persistent queue (`--process-queue`, `--queue-status`) catches YouTube `uploadLimitExceeded` / `quotaExceeded`, stages clips into `exported/queued/`, and drains FIFO automatically once slots reopen without exhausting API quota.
- **`montage_builder.py`**:
  - Consolidates all un-montaged clips from `uploaded/` into a smooth NVENC `xfade`/`acrossfade` montage, trimming post-death tail footage and ducking/mixing background music.

## Quick Setup

```bash
pip install opencv-python rapidocr-onnxruntime google-api-python-client google-auth-oauthlib
install -Dm755 shorts_uploader.py montage_builder.py upload-to-youtube.sh ~/.local/bin/
mkdir -p ~/.config/youtube-upload && cp config.example.json ~/.config/youtube-upload/config.json
# Place your Google Desktop OAuth client_secrets.json in ~/.config/youtube-upload/
install -Dm644 systemd/youtube-upload-queue.* ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now youtube-upload-queue.timer
```

## License

GPL-3.0-or-later — [labs.yuup.com](https://yuup.com/labs)
