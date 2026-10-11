#!/usr/bin/env python3
import os
import sys
import json
import time
import subprocess
from datetime import datetime
from pathlib import Path

# Google API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

CONFIG_DIR = Path.home() / ".config/youtube-upload"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_FILE = CONFIG_DIR / "token.json"
CLIENT_SECRETS_FILE = CONFIG_DIR / "client_secrets.json"
CONFIG_FILE = CONFIG_DIR / "config.json"

def notify(title, body="", icon="video-x-generic", expire_ms=3000):
    subprocess.Popen(
        ["notify-send", "-a", "YouTube Uploader", "-i", icon, "-t", str(expire_ms), title, body],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def load_config():
    defaults = {
        "privacy_status": "unlisted",  # "public", "unlisted", or "private"
        "category_id": "20",           # 20 = Gaming
        "tags": ["Quake Live", "Quake", "FPS", "Gameplay", "Steam Recording"]
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                defaults.update(json.load(f))
        except Exception:
            pass
    return defaults

def get_authenticated_service():
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as e:
            print("Token load error:", e)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(TOKEN_FILE, "w") as token:
                    token.write(creds.to_json())
            except Exception as e:
                print("Token refresh failed:", e)
                creds = None

        if not creds:
            if not CLIENT_SECRETS_FILE.exists():
                notify(
                    "Setup Required",
                    "Missing client_secrets.json in ~/.config/youtube-upload/",
                    icon="dialog-warning",
                    expire_ms=6000
                )
                print("ERROR: client_secrets.json not found in", CONFIG_DIR)
                print("Please create an OAuth Client ID (Desktop App) in Google Cloud Console and save it as:")
                print(f"  {CLIENT_SECRETS_FILE}")
                sys.exit(1)

            notify("Authentication Required", "Please approve access in your browser for account: youtube", expire_ms=5000)
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
            notify("Authentication Successful!", "Saved credentials for YouTube.", expire_ms=3000)

    return build("youtube", "v3", credentials=creds)

def extract_action_thumbnail(video_path: str) -> str:
    """Extracts a high-action frame around 60% of the clip duration."""
    thumb_path = f"/tmp/thumb_{int(time.time())}.jpg"
    duration = 10.0
    try:
        # Probe duration
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True,
            text=True,
            check=True
        )
        duration = float(res.stdout.strip())
    except Exception:
        pass

    seek_time = max(1.0, duration * 0.6)
    
    # Extract representative action frame using thumbnail filter
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seek_time:.2f}",
        "-i", video_path,
        "-vf", "thumbnail=30",
        "-frames:v", "1",
        "-q:v", "2",
        thumb_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return thumb_path

def generate_metadata(video_path: str, config: dict):
    basename = Path(video_path).stem
    # Example format: Quake_Live_20261011_133841
    title_date = datetime.now().strftime("%b %d, %Y - %H:%M")
    
    if "Quake_Live" in basename:
        parts = basename.split("_")
        if len(parts) >= 4:
            try:
                dt = datetime.strptime(f"{parts[2]}_{parts[3]}", "%Y%m%d_%H%M%S")
                title_date = dt.strftime("%b %d, %Y - %H:%M")
            except Exception:
                pass
        title = f"Quake Live - Action Clip ({title_date})"
    else:
        title = f"{basename} ({title_date})"

    description = (
        f"Quake Live Gameplay Clip\n"
        f"Captured in 1440p 60fps on Linux\n"
        f"Date: {title_date}\n\n"
        f"#QuakeLive #FPS #Gameplay #SteamDeck #PCGaming"
    )

    return title, description

def upload_video(video_path: str):
    if not os.path.exists(video_path):
        notify("Upload Failed", f"File not found: {video_path}", icon="dialog-error")
        return

    config = load_config()
    notify("🚀 Extracting Thumbnail...", Path(video_path).name, icon="video-x-generic", expire_ms=2000)
    
    thumb_path = None
    try:
        thumb_path = extract_action_thumbnail(video_path)
    except Exception as e:
        print("Warning: Thumbnail extraction failed:", e)

    youtube = get_authenticated_service()
    title, description = generate_metadata(video_path, config)

    notify("⏳ Uploading to youtube...", f"{title}", icon="emblem-synchronizing", expire_ms=4000)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": config.get("tags", []),
            "categoryId": str(config.get("category_id", "20"))
        },
        "status": {
            "privacyStatus": config.get("privacy_status", "unlisted"),
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_path, chunksize=10*1024*1024, resumable=True)
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            print(f"Uploading: {progress}%")

    video_id = response.get("id")
    video_url = f"https://youtu.be/{video_id}"
    print("Video uploaded successfully. Video ID:", video_id)

    # Set custom thumbnail if available
    if thumb_path and os.path.exists(thumb_path):
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumb_path)
            ).execute()
            print("Thumbnail uploaded successfully!")
        except Exception as e:
            print("Failed to upload thumbnail:", e)
        finally:
            if os.path.exists(thumb_path):
                os.remove(thumb_path)

    # Copy link to clipboard
    try:
        subprocess.run(["wl-copy", video_url], check=True)
    except Exception:
        pass

    notify("✅ YouTube Upload Complete!", f"{title}\nLink copied: {video_url}", icon="dialog-ok", expire_ms=6000)
    print("Export complete:", video_url)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: youtube-upload-tool.py <path_to_video>")
        sys.exit(1)
    upload_video(sys.argv[1])
