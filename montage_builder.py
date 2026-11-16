#!/usr/bin/env python3
"""
Quake Live Video Montage Generator
Consolidates all un-montaged Quake Live clips into a seamless, high-energy frag montage:
  - Cuts off clips right after the featured player (Player) dies before transitioning
  - Crossfades video and audio smoothly between clips using FFmpeg xfade/acrossfade
  - Mixes background techno music underneath crisp gameplay SFX
  - Hardware-accelerated 1440p 60FPS encoding via NVIDIA NVENC
"""

import os
import sys

# Limit ONNX / OpenMP / MKL thread explosion during clip analysis to keep system responsive
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
os.environ["NUMEXPR_NUM_THREADS"] = "2"

# Monkey-patch onnxruntime SessionOptions to prevent OCR from spawning threads across all cores
try:
    import onnxruntime as ort
    _orig_ort_init = ort.SessionOptions.__init__
    def _patched_ort_init(self, *args, **kwargs):
        _orig_ort_init(self, *args, **kwargs)
        self.intra_op_num_threads = 2
        self.inter_op_num_threads = 2
    ort.SessionOptions.__init__ = _patched_ort_init
except Exception:
    pass

import re
import glob
import time
import json
import random
import shutil
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

# Paths
DEFAULT_EXPORT_DIR = Path("~/Videos/SteamClips/exported")
DEFAULT_UPLOADED_DIR = DEFAULT_EXPORT_DIR / "uploaded"
CONFIG_DIR = Path.home() / ".config/youtube-upload"
CONFIG_FILE = CONFIG_DIR / "config.json"
MUSIC_DIR = CONFIG_DIR / "music"
MUSIC_DIR.mkdir(parents=True, exist_ok=True)

CURATED_TRACKS = [
    ("Raving_Energy.mp3", "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Raving%20Energy.mp3"),
    ("Volatile_Reaction.mp3", "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Volatile%20Reaction.mp3"),
    ("Laserpack.mp3", "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Laserpack.mp3"),
    ("Future_Gladiator.mp3", "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Future%20Gladiator.mp3"),
    ("Take_the_Lead.mp3", "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Take%20the%20Lead.mp3"),
]

def notify(title, body="", icon="video-x-generic", expire_ms=4000):
    try:
        subprocess.Popen(
            ["notify-send", "-a", "Quake Montage", "-i", icon, "-t", str(expire_ms), title, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

def play_chime():
    try:
        chime_path = "/usr/share/sounds/freedesktop/stereo/complete.oga"
        if os.path.exists(chime_path):
            subprocess.Popen(["pw-play", chime_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def copy_to_clipboard(text):
    try:
        subprocess.run(["wl-copy", text], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def load_config():
    defaults = {
        "player_name": "Player",
        "transition_type": "fade",
        "transition_duration": 0.5,
        "music_volume": 0.38,
        "gameplay_volume": 0.85,
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                defaults.update(json.load(f))
        except Exception:
            pass
    return defaults

def ensure_music_track(selected_music=None):
    """
    Returns the path to a high-energy background techno track.
    If none exists locally, downloads a curated track or synthesizes one.
    """
    if selected_music and os.path.isfile(selected_music):
        return selected_music

    # Check local music directory
    music_files = sorted(list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav")) + list(MUSIC_DIR.glob("*.ogg")))
    if music_files:
        # Prefer configured or featured track, Nectarine Demoscene Radio high-rated tracks, then curated tracks
        for pref in ["Cru_L_T_-_Krull.mp3", "Dubmood_Paradox_Crackchip_2.mp3", "Traven_Orange_Juice_Theme.mp3", "Raving_Energy.mp3", "Volatile_Reaction.mp3", "Future_Gladiator.mp3"]:
            p = MUSIC_DIR / pref
            if p.exists():
                return str(p)
        return str(random.choice(music_files))

    # Attempt to download curated royalty-free techno tracks
    print("Downloading royalty-free background techno track...")
    for filename, url in CURATED_TRACKS:
        dest = MUSIC_DIR / filename
        try:
            cmd = ["curl", "-fsSL", "-o", str(dest), url]
            res = subprocess.run(cmd, timeout=15)
            if res.returncode == 0 and dest.exists() and dest.stat().st_size > 100000:
                print(f"Downloaded techno track: {filename}")
                return str(dest)
        except Exception as e:
            print(f"Failed to download {filename}: {e}")

    # Fallback: synthesize a 135BPM techno beat using built-in wave module
    synth_dest = MUSIC_DIR / "Synthesized_Techno_135BPM.wav"
    try:
        import wave, struct, math
        sr = 44100
        dur = 120.0
        n_samples = int(dur * sr)
        frames = bytearray()
        print("Synthesizing offline driving techno beat (135 BPM)...")
        for i in range(n_samples):
            t = i / sr
            beat = (t * (135.0 / 60.0)) % 1.0
            k_env = max(0.0, 1.0 - beat * 4.0)
            k_val = math.sin(2 * math.pi * (50 + 100 * k_env) * beat) * k_env
            offbeat = ((t + 0.222) * (135.0 / 60.0)) % 1.0
            h_env = max(0.0, 1.0 - offbeat * 10.0)
            h_val = (random.random() * 2 - 1) * h_env * 0.35
            sixteenth = (t * (135.0 / 60.0 * 4.0)) % 1.0
            b_env = max(0.0, 1.0 - sixteenth * 3.0)
            b_val = math.sin(2 * math.pi * 55.0 * t) * b_env * 0.4
            val = max(-1.0, min(1.0, k_val * 0.75 + b_val + h_val))
            frames.extend(struct.pack('<h', int(val * 32767)))

        with wave.open(str(synth_dest), 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(frames)
        return str(synth_dest)
    except Exception as e:
        print(f"Synthesis failed: {e}")
        return None

def find_clips(input_files=None, search_dir=None, days=None):
    """
    Finds Quake Live clips to include in the montage.
    If input_files is provided, uses those.
    Otherwise includes all un-montaged clips from search_dir (defaults to 'uploaded'
    subfolder), optionally filtered to `days` if explicitly specified.
    """
    if input_files:
        valid_files = [f for f in input_files if os.path.isfile(f) and f.lower().endswith(('.mp4', '.mkv', '.mov'))]
        if valid_files:
            return sorted(valid_files)

    if search_dir is None:
        # Prioritize the 'uploaded' subfolder where clips uploaded to YouTube Shorts are stored
        if DEFAULT_UPLOADED_DIR.exists():
            search_dir = DEFAULT_UPLOADED_DIR
        else:
            search_dir = DEFAULT_EXPORT_DIR
    elif isinstance(search_dir, str):
        search_dir = Path(search_dir)

    if not search_dir.exists():
        search_dir = Path.cwd()

    now = time.time()
    cutoff_sec = (days * 86400.0) if (days is not None and days > 0) else None

    def collect_valid(directory: Path) -> list[str]:
        found = []
        for f in sorted(directory.glob("*.mp4")):
            fname_low = f.name.lower()
            # Skip existing montages, shorts derivatives, and thumbnails
            if "montage" in fname_low or "_short" in fname_low or fname_low.endswith("_thumb.jpg"):
                continue
            if cutoff_sec is not None:
                st = f.stat()
                latest_ts = max(st.st_mtime, st.st_ctime)
                if (now - latest_ts) > cutoff_sec:
                    continue
            found.append(str(f))
        return found

    selected = collect_valid(search_dir)

    # If uploaded directory had no eligible clips, fallback to main export dir
    if not selected and search_dir == DEFAULT_UPLOADED_DIR and DEFAULT_EXPORT_DIR.exists():
        selected = collect_valid(DEFAULT_EXPORT_DIR)

    # Sort chronologically by filename / timestamp
    selected.sort()
    return selected

_GLOBAL_OCR_ENGINE = None

def get_ocr_engine():
    global _GLOBAL_OCR_ENGINE
    if _GLOBAL_OCR_ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _GLOBAL_OCR_ENGINE = RapidOCR()
        except Exception:
            pass
    return _GLOBAL_OCR_ENGINE

def analyze_clip_death(video_path, featured_player="Player", engine=None):
    """
    Analyzes video using OCR to detect when the featured player dies.
    Returns:
      (status, cut_duration, total_duration, reason)
    If the player died, cut_duration is clamped to death_time + 0.5s.
    If the player survived, cut_duration is total_duration.
    """
    try:
        import cv2

        if engine is None:
            engine = get_ocr_engine()
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return "UNKNOWN", 0.0, 0.0, "Could not open clip"
        
        dur = total_frames / fps
        if dur < 2.5:
            cap.release()
            return "SURVIVED", dur, dur, "Clip very short"

        death_events = []
        frag_events = []
        win_events = []
        respawn_events = []

        # Sample every ~0.6 seconds starting from 0.5s
        step_frames = int(fps * 0.6)
        for fno in range(int(fps * 0.5), total_frames, step_frames):
            t = fno / fps
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                break
            h, w, _ = frame.shape
            
            # Check upper-center area where "Fragged by" and "Following" appear
            roi = frame[int(h * 0.10):int(h * 0.45), int(w * 0.15):int(w * 0.75)]
            res, _ = engine(roi)
            for _, txt, _ in (res or []):
                txt_clean = txt.strip()
                if re.search(r'fragged\s+by', txt_clean, re.I) or re.search(r'you\s*were\s*fragged', txt_clean, re.I) or re.search(r'you\s*died', txt_clean, re.I):
                    death_events.append((t, txt_clean))
                elif re.search(r'\bfollowing\s+', txt_clean, re.I):
                    death_events.append((t, txt_clean))
                elif re.search(r'you\s*fragged', txt_clean, re.I):
                    frag_events.append((t, txt_clean))
                elif re.search(r'wins\s+the\s+round', txt_clean, re.I) or re.search(r'roundlimit\s+hit', txt_clean, re.I) or re.search(r'match\s+summary', txt_clean, re.I):
                    win_events.append((t, txt_clean))
                elif re.search(r'round\s*begins\s+in', txt_clean, re.I) or re.search(r'round\s*starts\s+in', txt_clean, re.I) or re.search(r'begins\s+in', txt_clean, re.I) or re.search(r'spectators:', txt_clean, re.I):
                    respawn_events.append((t, txt_clean))

        cap.release()

        # Find the relevant death event (ignoring stale pre-round deaths from the first 2s)
        relevant_death = None
        for (t, txt) in death_events:
            # If a player gets frags > 2.0s after this death event, it was from the previous round
            if any(tf > t + 2.0 for (tf, _) in frag_events):
                continue
            if relevant_death is None or t < relevant_death[0]:
                relevant_death = (t, txt)

        first_win = win_events[0] if win_events else None
        first_respawn = None
        for (t, txt) in respawn_events:
            if t < 2.5:
                continue
            if first_respawn is None or t < first_respawn[0]:
                first_respawn = (t, txt)

        if relevant_death:
            # Cut off right after death (+0.5s so the frag landing is visible, but spectator camera is cut)
            cut_t = min(dur, relevant_death[0] + 0.5)
            cut_t = max(2.0, cut_t)
            return "DIED", cut_t, dur, relevant_death[1]
        elif first_respawn:
            # Cut off right before respawn countdown / scoreboard begins
            cut_t = min(dur, max(2.0, first_respawn[0] - 0.3))
            return "RESPAWN_CUT", cut_t, dur, f"Cut before {first_respawn[1]}"
        elif first_win:
            cut_t = min(dur, max(2.0, first_win[0] + 2.5))
            return "WIN_CUT", cut_t, dur, f"Cut after {first_win[1]}"
        else:
            return "SURVIVED", dur, dur, "Player survived to round end"

    except Exception as e:
        print(f"OCR analysis error on {video_path}: {e}")
        # Default to full clip
        try:
            dur_res = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True
            )
            d = float(dur_res.stdout.strip())
            return "SURVIVED", d, d, "Fallback duration"
        except Exception:
            return "SURVIVED", 10.0, 10.0, "Default duration"

def build_montage(
    clips,
    output_path=None,
    music_track=None,
    featured_player="Player",
    transition_type="fade",
    transition_dur=0.5,
    music_vol=0.38,
    gameplay_vol=0.85
):
    """
    Renders the consolidated Quake Live montage with hardware NVENC acceleration.
    """
    if not clips:
        print("No clips found to consolidate.")
        notify("Quake Montage", "No un-montaged clips found.", icon="dialog-warning")
        return None

    notify(
        "🎬 Creating Quake Live Montage...",
        f"Processing {len(clips)} clip(s)\nDetecting player death cutoffs & smoothing transitions...",
        icon="video-x-generic",
        expire_ms=5000
    )

    print(f"\n=======================================================")
    print(f"🎬 QUAKE LIVE MONTAGE GENERATOR")
    print(f"Total clips found: {len(clips)}")
    print(f"Featured player: {featured_player}")
    print(f"Transition: {transition_type} ({transition_dur}s)")
    print(f"=======================================================\n")

    clip_cuts = []
    total_saved_sec = 0.0

    print("--- Analyzing Player Deaths & Trimming Spectator Footage ---")
    ocr_engine = get_ocr_engine()
    for idx, cpath in enumerate(clips, 1):
        cname = Path(cpath).name
        print(f"[{idx}/{len(clips)}] Analyzing {cname}...", end=" ", flush=True)
        status, cut_t, total_t, reason = analyze_clip_death(cpath, featured_player, engine=ocr_engine)
        saved = total_t - cut_t
        total_saved_sec += saved
        clip_cuts.append((cpath, cut_t, total_t, status, reason))

        if status == "DIED":
            print(f"💥 DIED ({reason}) -> Cut to {cut_t:.1f}s / {total_t:.1f}s (cut {saved:.1f}s spectator)")
        else:
            print(f"🔥 SURVIVED -> Full clip {cut_t:.1f}s")

    print(f"\nTotal dead spectator footage eliminated: {total_saved_sec:.1f}s!\n")

    # Select background music
    music_file = ensure_music_track(music_track)
    if music_file:
        print(f"🎵 Background Techno Track: {Path(music_file).name}")
    else:
        print("⚠️ No music track available, using game audio only.")

    # Calculate transition offsets and filter graph
    inputs = []
    v_filters = []
    a_filters = []

    for i, (cpath, cut_t, _, _, _) in enumerate(clip_cuts):
        inputs.extend(["-ss", "0", "-t", f"{cut_t:.3f}", "-i", cpath])
        v_filters.append(f"[{i}:v]settb=AVTB,fps=60,format=yuv420p[v{i}];")
        a_filters.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}];")

    music_idx = len(clip_cuts)
    if music_file:
        inputs.extend(["-stream_loop", "-1", "-i", music_file])

    # Chain xfades
    chain = []
    last_v = "v0"
    last_a = "a0"
    running_dur = clip_cuts[0][1]

    for i in range(1, len(clip_cuts)):
        next_v = f"v{i}"
        next_a = f"a{i}"
        out_v = f"vm{i}"
        out_a = f"am{i}"
        offset = max(0.0, running_dur - transition_dur)
        chain.append(f"[{last_v}][{next_v}]xfade=transition={transition_type}:duration={transition_dur}:offset={offset:.3f}[{out_v}];")
        chain.append(f"[{last_a}][{next_a}]acrossfade=d={transition_dur}[{out_a}];")
        last_v = out_v
        last_a = out_a
        running_dur = offset + clip_cuts[i][1]

    total_montage_dur = running_dur
    mins = int(total_montage_dur // 60)
    secs = int(total_montage_dur % 60)
    print(f"Estimated Montage Duration: {mins}m {secs:02d}s ({total_montage_dur:.1f}s)")

    # Audio mixing filter
    if music_file:
        fade_out_st = max(0.0, total_montage_dur - 2.0)
        chain.append(
            f"[{music_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={music_vol},"
            f"afade=t=in:ss=0:d=1.0,afade=t=out:st={fade_out_st:.2f}:d=2.0[bgm];"
        )
        chain.append(f"[{last_a}]volume={gameplay_vol}[gameplay];")
        chain.append(f"[gameplay][bgm]amix=inputs=2:duration=first:dropout_transition=2[a_final]")
        final_a = "a_final"
    else:
        final_a = last_a

    # Determine output path
    if not output_path:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = DEFAULT_EXPORT_DIR / f"Quake_Live_Montage_{timestamp_str}.mp4"
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    filter_complex_str = "".join(v_filters) + "".join(a_filters) + "".join(chain)

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-threads", "4",
        *inputs,
        "-filter_complex", filter_complex_str,
        "-map", f"[{last_v}]",
        "-map", f"[{final_a}]",
        "-c:v", "h264_nvenc",
        "-preset", "p4",
        "-tune", "hq",
        "-cq", "19",
        "-b:v", "35M",
        "-maxrate", "45M",
        "-bufsize", "50M",
        "-c:a", "aac",
        "-b:a", "256k",
        "-shortest",
        str(output_path)
    ]

    print("\n--- Encoding Montage with NVIDIA NVENC (1440p 60FPS) ---")
    notify(
        "⚡ Rendering Montage (NVENC)...",
        f"Encoding {len(clips)} clips into 1440p 60FPS\nOutput: {output_path.name}",
        icon="emblem-synchronizing",
        expire_ms=6000
    )

    t0 = time.time()
    res = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
    t1 = time.time()

    if res.returncode != 0:
        print("❌ FFmpeg encoding failed!")
        print("Stderr details:")
        print(res.stderr[-1000:])
        notify("❌ Montage Failed", "FFmpeg error during encoding. Check console output.", icon="dialog-error")
        return None

    render_time = t1 - t0
    fps_avg = (total_montage_dur * 60.0) / max(0.1, render_time)
    file_size_mb = output_path.stat().st_size / (1024 * 1024)

    print(f"\n=======================================================")
    print(f"✅ MONTAGE COMPLETE!")
    print(f"Output File:     {output_path}")
    print(f"Duration:        {mins}m {secs:02d}s")
    print(f"File Size:       {file_size_mb:.1f} MB")
    print(f"Render Time:     {render_time:.1f}s ({fps_avg:.0f} FPS average)")
    print(f"Clips Included:  {len(clips)}")
    print(f"Dead Trimmed:    {total_saved_sec:.1f}s saved")
    print(f"=======================================================\n")

    copy_to_clipboard(str(output_path))
    play_chime()

    # Move processed clips to a dedicated montage subfolder to prevent clutter and duplicate inclusion
    moved_count = 0
    try:
        montage_stem = output_path.stem
        # Create subfolder inside the source directory, e.g. uploaded/montage_Quake_Live_Montage_...
        first_clip_dir = Path(clips[0]).parent
        archive_dir = first_clip_dir / f"montaged_{montage_stem}"
        archive_dir.mkdir(parents=True, exist_ok=True)
        for c in clips:
            c_p = Path(c)
            if c_p.exists():
                dest = archive_dir / c_p.name
                shutil.move(str(c_p), str(dest))
                moved_count += 1
        print(f"📦 Archived {moved_count} clip(s) to: {archive_dir}")
    except Exception as e:
        print(f"Archive warning: {e}")

    notify(
        "✅ Quake Live Montage Complete!",
        f"{mins}m {secs:02d}s montage ({len(clips)} clips) ready!\nSaved to: {output_path.name}\nArchived {moved_count} clips to subfolder.",
        icon="dialog-ok",
        expire_ms=8000
    )

    return str(output_path)

def main():
    parser = argparse.ArgumentParser(description="Quake Live Video Montage Generator")
    parser.add_argument("files", nargs="*", help="Specific video files to include (default: all un-montaged clips)")
    parser.add_argument("--days", type=int, default=None, help="Optional lookback window in days (default: open-ended / all clips)")
    parser.add_argument("--dir", default=None, help="Directory to scan for Quake Live clips (default: exported/uploaded)")
    parser.add_argument("--player", default=None, help="Featured player name to detect deaths (default from config: Player)")
    parser.add_argument("--music", default=None, help="Path to custom background music track")
    parser.add_argument("--nectarine", action="store_true", help="Use top-rated demoscene track from Nectarine Radio")
    parser.add_argument("--music-vol", type=float, default=None, help="Background music volume multiplier (default: 0.38)")
    parser.add_argument("--game-vol", type=float, default=None, help="Gameplay sound effects volume multiplier (default: 0.85)")
    parser.add_argument("--transition", default=None, help="Transition style (fade, dissolve, fadeblack, etc., default: fade)")
    parser.add_argument("--transition-dur", type=float, default=None, help="Transition duration in seconds (default: 0.5)")
    parser.add_argument("--output", "-o", default=None, help="Output montage MP4 file path")
    args = parser.parse_args()

    config = load_config()

    if args.nectarine:
        nectarine_candidates = [
            MUSIC_DIR / "Dubmood_Paradox_Crackchip_2.mp3",
            MUSIC_DIR / "Traven_Orange_Juice_Theme.mp3"
        ]
        valid_necta = [str(p) for p in nectarine_candidates if p.exists()]
        if valid_necta:
            args.music = valid_necta[0]

    featured_player = args.player or config.get("player_name", "Player")
    music_vol = args.music_vol if args.music_vol is not None else config.get("music_volume", 0.38)
    game_vol = args.game_vol if args.game_vol is not None else config.get("gameplay_volume", 0.85)
    transition = args.transition or config.get("transition_type", "fade")
    trans_dur = args.transition_dur if args.transition_dur is not None else config.get("transition_duration", 0.5)

    # Check Nautilus environment variable if no files were passed as args
    input_files = args.files
    nautilus_env = os.environ.get("NAUTILUS_SCRIPT_SELECTED_FILE_PATHS", "")
    if not input_files and nautilus_env:
        input_files = [line.strip() for line in nautilus_env.splitlines() if line.strip()]

    search_dir = Path(args.dir) if args.dir else None
    clips = find_clips(input_files=input_files, search_dir=search_dir, days=args.days)

    if not clips:
        loc = search_dir or DEFAULT_UPLOADED_DIR
        window_msg = f" in the last {args.days} days" if args.days else ""
        print(f"No clips found in {loc}{window_msg}.")
        notify("Quake Montage", f"No un-montaged Quake Live clips found{window_msg}.", icon="dialog-warning")
        sys.exit(1)

    result_path = build_montage(
        clips=clips,
        output_path=args.output,
        music_track=(args.music or config.get("music_track") or config.get("music")),
        featured_player=featured_player,
        transition_type=transition,
        transition_dur=trans_dur,
        music_vol=music_vol,
        gameplay_vol=game_vol
    )

    if not result_path:
        sys.exit(1)

if __name__ == "__main__":
    main()
