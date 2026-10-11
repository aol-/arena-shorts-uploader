#!/usr/bin/env python3
import os
import sys
import re
import json
import time
import random
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

# Google API imports (loaded lazily if credentials present)
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

CONFIG_DIR = Path.home() / ".config/youtube-upload"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_FILE = CONFIG_DIR / "token.json"
CLIENT_SECRETS_FILE = CONFIG_DIR / "client_secrets.json"
CONFIG_FILE = CONFIG_DIR / "config.json"

TEMPLATES_WITH_ENEMY_AND_WEAPON = [
    "{player} ABSOLUTELY DELETES {enemy} WITH THE {weapon}! 🎯💀 [Quake Live 1440p]",
    "{player} HITS THE NASTIEST {weapon} FRAG ON {enemy}! 🚀🔥 (Quake Live 1440p)",
    "{player} CAUGHT {enemy} LACKING! SICK {weapon} PLAY 💀⚡ [Quake Live]",
    "{player} OUTPLAYS {enemy}! 200 IQ {weapon} PREDICTION 🎯🧠 (Quake Live)",
    "THE CLEANEST {weapon} HIT YOU'LL SEE TODAY! {player} vs {enemy} 💥🔥",
    "{player} SENDS {enemy} TO THE SHADOW REALM! {weapon} FRAG 💀🚀 (Quake Live)",
    "PURE DISRESPECT! {player} ENDS {enemy} WITH THE {weapon} 🪚💀 [Quake Live 1440p]",
    "NO MERCY! {player} HITS A GOD-TIER {weapon} SHOT ON {enemy} 🎯🔥 (Quake Live)",
    "{player} PREDICTED THE MOVEMENT! UNREAL {weapon} FRAG ON {enemy} 🚀💥",
    "{player} WITH THE FILTHY {weapon} FLICK ON {enemy}! 🎯⚡ [Quake Live 1440p]"
]

TEMPLATES_WITH_ENEMY_ONLY = [
    "{player} OUTPLAYS {enemy} IN CLAN ARENA! 💀🔥 [Quake Live 1440p]",
    "{player} SENDS {enemy} PACKING! INSANE FRAG 🚀💥 (Quake Live)",
    "{player} CAUGHT {enemy} SLEEPING! CLEAN CLAN ARENA FRAG 🎯💀",
    "{player} WITH THE CLUTCH PLAY AGAINST {enemy}! 🔥⚡ [Quake Live 1440p]",
    "{player} SHOWS NO MERCY TO {enemy}! UNREAL ARENA DUEL 💀🎯"
]

TEMPLATES_WITH_WEAPON_ONLY = [
    "{player} HITS A DISGUSTING {weapon} FRAG! 🚀💥 [Quake Live 1440p]",
    "{player} WITH THE GOD-TIER {weapon} FLICK! 🎯🔥 (Quake Live)",
    "{player} MELTS OPPONENTS WITH THE {weapon}! ⚡💀 [Quake Live 1440p]",
    "THE CLEANEST {weapon} SHOT! {player} CLUTCH 💥🔥 (Quake Live)",
    "{player} SHOWS HOW TO USE THE {weapon}! PURE FILTH 🎯💀 [Quake Live 1440p]",
    "{player} WITH THE INSANE {weapon} PREDICTION! 🚀🔥 (Quake Live)"
]

TEMPLATES_SPECIAL_FEATS = {
    "Back-to-Back Double Rails": [
        "{player} HITS BACK-TO-BACK DOUBLE RAILS?! 🎯💥 2 SINGLES INTO 2 COLLATERALS! [Quake Live 1440p]",
        "4 SHOTS, 6 RAILS?! {player} UNREAL COLLATERAL STREAK vs {enemy} 💀🔥 [Quake Live]",
        "{player} 2 SINGLES INTO 2 DOUBLE RAILS IN A ROW! 🎯💀 PURE RAILGUN FILTH (Quake Live)",
        "{player} WITH THE DISGUSTING DOUBLE COLLATERAL RAILGUN STREAK! 🎯⚡ [Quake Live 1440p]",
        "BACK-TO-BACK DOUBLE RAILS IN THE TUNNEL! 🎯💥 {player} DELETES {enemy} (Quake Live)"
    ],
    "Double Rail / Collateral": [
        "{player} HITS THE DISGUSTING DOUBLE RAIL COLLATERAL! 🎯💥 [Quake Live 1440p]",
        "ONE SHOT, TWO KILLS! {player} INSANE DOUBLE RAIL on {enemy} 💀⚡ (Quake Live)",
        "{player} WITH THE FILTHY PIERCING DOUBLE RAIL! 🎯🔥 [Quake Live 1440p]",
        "COLLATERAL RAILGUN DAMAGE! {player} DELETES {enemy} 💀🎯 (Quake Live)"
    ],
    "Air Rocket / Direct": [
        "{player} WITH THE NASTY MIDAIR AIR-ROCKET PREDICTION! 🚀🎯 (Quake Live 1440p)",
        "DELETED OUT OF THE SKY! {player} SICK AIR ROCKET on {enemy} 🚀💥 [Quake Live]",
        "{player} HITS A DISGUSTING MIDAIR DIRECT HIT! 🚀🔥 (Quake Live)"
    ],
    "Midair Railgun": [
        "{player} SNIPES {enemy} OUT OF MIDAIR! GOD-TIER RAIL FLICK 🎯⚡ [Quake Live 1440p]",
        "SKEET SHOOTING IN QUAKE LIVE! {player} MIDAIR RAIL ON {enemy} 🎯💀 (1440p)"
    ],
    "Clutch / Wipeout": [
        "{player} 1vX CLUTCH IN CLAN ARENA! UNREAL WIPEOUT 💀🔥 [Quake Live 1440p]",
        "HE WON THAT?! {player} IMPOSSIBLE CLAN ARENA CLUTCH vs {enemy} 🚀🎯 (Quake Live)"
    ]
}

TEMPLATES_GENERAL = [
    "{player} GOES FULL BEAST MODE IN CLAN ARENA! 🔥💀 [Quake Live 1440p]",
    "{player} WITH THE UNREAL FRAG HIGHLIGHT! 🚀🎯 (Quake Live)",
    "THE FASTEST SHOTS IN THE LOBBY! {player} QUAKE LIVE CLUTCH 💥⚡",
    "{player} SHOWS NO MERCY! INSANE ARENA PLAY 💀🔥 [Quake Live 1440p]",
    "PEAK QUAKE LIVE GAMEPLAY! {player} HIGHLIGHT 🚀💥 [1440p 60FPS]"
]

def notify(title, body="", icon="video-x-generic", expire_ms=3000):
    subprocess.Popen(
        ["notify-send", "-a", "YouTube Uploader", "-i", icon, "-t", str(expire_ms), title, body],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def load_config():
    defaults = {
        "account_name": "youtube",
        "email": "you@example.com",
        "chrome_profile": "Default",
        "player_name": "Player",
        "privacy_status": "public",  # "public", "unlisted", or "private"
        "category_id": "20",         # 20 = Gaming
        "shorts_mode": True,         # Automatically format <=60s clips as 9:16 YouTube Shorts
        "shorts_style": "crop",      # "crop" (full-screen vertical trim) or "blur" (blurred background)
        "dual_upload": True,         # Also upload original 16:9 widescreen 1440p version alongside Shorts
        "tags": ["Quake Live", "Quake", "FPS", "Gameplay", "Steam Recording"]
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                defaults.update(json.load(f))
        except Exception:
            pass
    return defaults

IGNORE_WORDS = {
    'bridge', 'upper floor', 'lower floor', '2nd floor', 'rocket launcher', 'courtyard', 
    'courtyard 2nd', 'shotgun', 'railgun', 'rocket stairwell', 'hyway', 'red team', 'blue team',
    'fight!', 'fight', 'round begins in', 'round', 'begins', 'fog pit', 'upper cou', 'hallway',
    'fps', '250fps', '250f', 'mega', 'quad', 'stairs', 'the', 'and', 'red', 'blue', 'draw',
    'k/d', 'score', 'ping', 'time', 'dmg', 'acc', 'lead', 'tied', 'red wins the round',
    'matchinprogress', 'players', 'community server', 'community server - overkill',
    'player stats', 'player', 'dmg weap'
}

def clean_player_name(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r'\s*\d+fps.*$', '', s, flags=re.I)
    s = re.sub(r'^(red|blue)\s+team\s*', '', s, flags=re.I)
    s = re.sub(r'^\d+/\d+$', '', s)
    s = re.sub(r'^\d+:\d+$', '', s)
    s = re.sub(r'^[^\w\u4e00-\u9fff™]+|[^\w\u4e00-\u9fff™]+$', '', s)
    if s.lower() in IGNORE_WORDS or s.isdigit():
        return ''
    if re.match(r'^\d+(\.\d+)?[kK]$', s) or re.match(r'^\d+dmg$', s, re.I):
        return ''
    if re.match(r'^k/?d$', s, re.I):
        return ''
    if s.lower() in ['player', 'you']:
        return ''
    return s.strip()

def analyze_video_gameplay(video_path: str) -> dict:
    """
    Automatically analyzes the video using computer vision & OCR
    to detect enemy names, weapon used, and combat feats with zero user input.
    Prioritizes:
      1. Center Frag Announcement ('You fragged <Victim>.')
      2. Top-Left Killfeed ('Player [icon] <Victim>')
      3. In-Combat Crosshair Nameplate during damage
    Completely avoids bystander names from the server scoreboard/roster.
    """
    result = {"enemy": "", "weapon": "Railgun", "feat": ""}
    try:
        import cv2
        from collections import Counter
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            return result
        duration = total_frames / fps

        # Sample 10 frames across the clip weighted towards combat/kill action
        percentages = [0.35, 0.45, 0.55, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

        frag_victims = Counter()
        killfeed_victims = Counter()
        combat_names = Counter()
        weapon_scores = Counter()
        has_double_rail = False
        has_air_rocket_medal = False

        for pct in percentages:
            ts = duration * pct
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
            ret, frame = cap.read()
            if not ret:
                continue
            h, w, _ = frame.shape

            # 1. Authoritative Frag Announcement ('You fragged <Player>.') in center-upper screen
            frag_roi = frame[int(h * 0.12):int(h * 0.35), int(w * 0.30):int(w * 0.70)]
            res_f, _ = engine(frag_roi)
            for it in res_f or []:
                t = it[1].strip()
                m = re.search(r'you\s*fragged\s+([A-Za-z0-9_\u4e00-\u9fff™\.\-]+)', t, re.I)
                if m:
                    cn = clean_player_name(m.group(1))
                    if cn:
                        frag_victims[cn] += 20

            # 2. Killfeed in top-left ('Player [weapon icon] <Victim>')
            kf_roi = frame[int(h * 0.01):int(h * 0.12), int(w * 0.05):int(w * 0.45)]
            res_kf, _ = engine(kf_roi)
            for it in res_kf or []:
                t = it[1].strip()
                m = re.search(r'aol[_\.\s]+\s*([A-Za-z0-9_\u4e00-\u9fff™\.\-]+)', t, re.I)
                if m:
                    cn = clean_player_name(m.group(1))
                    if cn:
                        killfeed_victims[cn] += 10

            # 3. Quake Live Midair Award Medal at top-center (green wreath + white wings)
            award_roi = frame[int(h * 0.07):int(h * 0.18), int(w * 0.45):int(w * 0.55)]
            hsv_award = cv2.cvtColor(award_roi, cv2.COLOR_BGR2HSV)
            mask_wreath = cv2.inRange(hsv_award, (40, 100, 100), (85, 255, 255))
            mask_wings = cv2.inRange(hsv_award, (0, 0, 200), (180, 50, 255))
            if cv2.countNonZero(mask_wreath) > 25 and cv2.countNonZero(mask_wings) > 15:
                has_air_rocket_medal = True

            # 4. Center combat zone for damage numbers and target overhead nameplates
            center_roi = frame[int(h * 0.25):int(h * 0.75), int(w * 0.25):int(w * 0.75)]
            res_c, _ = engine(center_roi)

            frame_damage = False
            for it in res_c or []:
                t = it[1].strip()
                if t.isdigit() and int(t) in range(15, 120):
                    frame_damage = True
                elif "80" in t and len(t) >= 4:
                    frame_damage = True
                    has_double_rail = True
                else:
                    cn = clean_player_name(t)
                    if cn:
                        combat_names[cn] += 5 if pct >= 0.60 else 2

            # 5. Weapon check from bottom-center active weapon icon
            bc = frame[int(h * 0.90):int(h * 0.99), int(w * 0.42):int(w * 0.50)]
            hsv = cv2.cvtColor(bc, cv2.COLOR_BGR2HSV)
            mask_green = cv2.inRange(hsv, (35, 100, 100), (85, 255, 255))
            mask_red1 = cv2.inRange(hsv, (0, 120, 120), (10, 255, 255))
            mask_orange = cv2.inRange(hsv, (11, 140, 140), (25, 255, 255))
            mask_yellow = cv2.inRange(hsv, (26, 120, 120), (34, 255, 255))
            mask_cyan = cv2.inRange(hsv, (85, 120, 120), (105, 255, 255))

            current_wep = None
            if cv2.countNonZero(mask_green) > 30:
                current_wep = "Railgun"
            elif cv2.countNonZero(mask_orange) > 30:
                current_wep = "Shotgun"
            elif cv2.countNonZero(mask_red1) > 30:
                current_wep = "Rocket Launcher"
            elif cv2.countNonZero(mask_yellow) > 30:
                current_wep = "Lightning Gun"
            elif cv2.countNonZero(mask_cyan) > 30:
                current_wep = "Gauntlet"

            if current_wep:
                pts = 1
                if pct >= 0.60:
                    pts += 3
                if frame_damage:
                    pts += 10
                weapon_scores[current_wep] += pts

        cap.release()

        # Tiered victim selection:
        # Tier 1: Player explicitly fragged by Player
        # Tier 2: Killfeed confirmation
        # Tier 3: In-combat crosshair nameplate during sustained damage
        top_enemies = []
        if frag_victims:
            top_enemies = [e[0] for e in frag_victims.most_common(2)]
        elif killfeed_victims:
            top_enemies = [e[0] for e in killfeed_victims.most_common(2)]
        elif combat_names:
            top_enemies = [e[0] for e in combat_names.most_common(2) if e[1] >= 4]

        wep = weapon_scores.most_common(1)[0][0] if weapon_scores else "Railgun"

        feat = ""
        if wep == "Rocket Launcher" and has_air_rocket_medal:
            feat = "Air Rocket / Direct"
        elif wep == "Railgun" and has_double_rail:
            feat = "Back-to-Back Double Rails"
        elif wep == "Gauntlet":
            feat = "Humiliation / Gauntlet"

        result["enemy"] = " & ".join(top_enemies) if top_enemies else ""
        result["weapon"] = wep
        result["feat"] = feat
    except Exception as e:
        print("Video analysis warning:", e)
    return result

def prompt_frag_details(video_path: str, player_name: str = "Player"):
    """Pops up a dialog asking for enemy, weapon, special play, or custom title."""
    p = Path(video_path)
    cmd = [
        "zenity", "--forms",
        f"--title=Export to YouTube ({player_name})",
        f"--text=<b>Clip: {p.name}</b>\nAdd frag details to build an exciting title:\n<i>(Leave blank to auto-generate)</i>",
        "--add-entry=Enemy Fragged (e.g. bong™, SERVAL)",
        "--add-combo=Weapon Used",
        "--combo-values=Railgun|Rocket Launcher|Lightning Gun|Shotgun|Gauntlet|Plasma Gun|Grenade Launcher|BFG",
        "--add-combo=Special Highlight / Feat",
        "--combo-values=Auto / Standard Frag|Back-to-Back Double Rails|Double Rail / Collateral|Air Rocket / Direct|Midair Railgun|Clutch / Wipeout",
        "--add-entry=Custom Title Override (optional)",
        "--ok-label=Export & Upload",
        "--cancel-label=Cancel",
        "--width=520"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return None, None, None, None
    parts = res.stdout.strip().split("|")
    enemy = parts[0].strip() if len(parts) > 0 else ""
    weapon = parts[1].strip() if len(parts) > 1 else ""
    special_play = parts[2].strip() if len(parts) > 2 else ""
    custom_title = parts[3].strip() if len(parts) > 3 else ""
    return enemy, weapon, special_play, custom_title

def extract_action_thumbnail(video_path: str, output_path: str = None) -> str:
    """Extracts a high-action frame around 60% of the clip duration."""
    if not output_path:
        output_path = f"/tmp/thumb_{int(time.time())}.jpg"
    duration = 10.0
    try:
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
    
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seek_time:.2f}",
        "-i", video_path,
        "-vf", "thumbnail=30",
        "-frames:v", "1",
        "-q:v", "2",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return output_path

def convert_to_shorts_format(video_path: str, output_path: str = None, style: str = "crop") -> str:
    """
    Converts 16:9 gameplay footage into 9:16 vertical YouTube Shorts format.
    Style B ('crop'):
      - 1440x2560 (2K Vertical @ 60FPS) master encode.
      - Lanczos upscaling with unsharp-mask edge sharpening.
      - Encoded via NVENC Preset P7 High Quality at 45-60 Mbps.
      - Forces YouTube to allocate premium high-bitrate VP09/AV01 1440p codecs.
    """
    if not output_path:
        p = Path(video_path)
        output_path = f"/tmp/{p.stem}_shorts_{int(time.time())}.mp4"

    if style == "crop":
        vf = "crop=ih*9/16:ih:(iw-ow)/2:0,scale=1440:2560:flags=lanczos,unsharp=5:5:0.6:3:3:0.3,setsar=1"
        cmd_nvenc = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", vf,
            "-c:v", "h264_nvenc",
            "-preset", "p7",
            "-tune", "hq",
            "-cq", "12",
            "-b:v", "45M",
            "-maxrate", "60M",
            "-c:a", "copy",
            output_path
        ]
        cmd_cpu = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "16",
            "-c:a", "copy",
            output_path
        ]
    else:
        filter_complex = (
            "[0:v]scale=180:320:force_original_aspect_ratio=increase,"
            "crop=180:320,avgblur=8,scale=1080:1920:flags=lanczos,setsar=1[bg];"
            "[0:v]scale=1080:608,setsar=1[fg];"
            "[bg][fg]overlay=0:656"
        )
        cmd_nvenc = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-filter_complex", filter_complex,
            "-c:v", "h264_nvenc",
            "-preset", "p7",
            "-tune", "hq",
            "-cq", "14",
            "-c:a", "copy",
            output_path
        ]
        cmd_cpu = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-filter_complex", filter_complex,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "copy",
            output_path
        ]

    try:
        res = subprocess.run(cmd_nvenc, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return output_path
    except Exception as e:
        print("NVENC conversion warning:", e)

    subprocess.run(cmd_cpu, check=True)
    return output_path

def clean_quake_string(s: str) -> str:
    s = re.sub(r'\^[0-9a-zA-Z]', '', s)
    s = re.sub(r'[\u263a\u263b]', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def get_quake_server_info() -> dict:
    """
    Retrieves the connected server details (name, IP, map, mode, players)
    from Quake Live configs and live Steam A2S query protocol.
    """
    info = {
        'server_name': '',
        'server_ip': '',
        'map_name': '',
        'game_mode': '',
        'players': []
    }
    
    server_addr = ''
    # 1. Try repconfig.cfg
    rep_cfg = Path.home() / ".local/share/Steam/steamapps/common/Quake Live/YOUR_STEAM_ID/baseq3/repconfig.cfg"
    if rep_cfg.exists():
        try:
            with open(rep_cfg, 'r', encoding='latin1', errors='ignore') as f:
                for line in f:
                    m = re.search(r'cl_currentServerAddress\s+\"([^\"]+)\"', line)
                    if m and m.group(1).strip():
                        server_addr = m.group(1).strip()
                        break
        except Exception:
            pass

    # 2. Try running process if repconfig didn't find it
    if not server_addr:
        try:
            p_out = subprocess.check_output(['ps', '-eo', 'args'], text=True)
            m = re.search(r'quakelive_steam\.exe.*?(\d+\.\d+\.\d+\.\d+:\d+)', p_out)
            if m:
                server_addr = m.group(1).strip()
        except Exception:
            pass

    if not server_addr:
        return info

    info['server_ip'] = server_addr

    # Query A2S (Source/Steam query protocol used by Quake Live)
    try:
        import socket
        host, port_str = server_addr.split(':')
        port = int(port_str)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.5)

        # A2S_INFO
        req_info = b'\xff\xff\xff\xffTSource Engine Query\x00'
        sock.sendto(req_info, (host, port))
        resp, _ = sock.recvfrom(4096)
        if len(resp) >= 9 and resp[4:5] == b'A':
            sock.sendto(req_info + resp[5:9], (host, port))
            resp, _ = sock.recvfrom(4096)

        idx = 6
        null_pos = resp.find(b'\x00', idx)
        info['server_name'] = clean_quake_string(resp[idx:null_pos].decode('utf-8', errors='replace'))
        idx = null_pos + 1
        null_pos = resp.find(b'\x00', idx)
        info['map_name'] = clean_quake_string(resp[idx:null_pos].decode('utf-8', errors='replace'))
        idx = null_pos + 1
        null_pos = resp.find(b'\x00', idx)
        idx = null_pos + 1
        null_pos = resp.find(b'\x00', idx)
        info['game_mode'] = clean_quake_string(resp[idx:null_pos].decode('utf-8', errors='replace'))

        # A2S_PLAYER
        sock.sendto(b'\xff\xff\xff\xff\x55\xff\xff\xff\xff', (host, port))
        resp_p, _ = sock.recvfrom(4096)
        if len(resp_p) >= 9 and resp_p[4:5] == b'A':
            sock.sendto(b'\xff\xff\xff\xff\x55' + resp_p[5:9], (host, port))
            resp_p, _ = sock.recvfrom(4096)

        idx = 5
        num_players = resp_p[idx]
        idx += 1
        players = []
        for _ in range(num_players):
            if idx >= len(resp_p):
                break
            idx += 1
            null_pos = resp_p.find(b'\x00', idx)
            p_name = clean_quake_string(resp_p[idx:null_pos].decode('utf-8', errors='replace'))
            idx = null_pos + 9
            if p_name and p_name not in players and p_name.lower() != 'Player':
                players.append(p_name)
        info['players'] = players
    except Exception as e:
        print("A2S server query notice:", e)

    return info

def generate_metadata(video_path: str, config: dict, enemy: str = "", weapon: str = "", special_play: str = "", custom_title: str = "", is_shorts: bool = False, server_info: dict = None):
    player = config.get("player_name", "Player")
    dt_str = datetime.now().strftime("%B %d, %Y")

    if server_info is None:
        try:
            server_info = get_quake_server_info()
        except Exception:
            server_info = {}

    if custom_title:
        title = custom_title
    elif special_play and special_play in TEMPLATES_SPECIAL_FEATS:
        title = random.choice(TEMPLATES_SPECIAL_FEATS[special_play]).format(
            player=player, enemy=enemy or "OPPONENTS", weapon=(weapon or "RAILGUN").upper()
        )
    elif enemy and weapon:
        title = random.choice(TEMPLATES_WITH_ENEMY_AND_WEAPON).format(
            player=player, enemy=enemy, weapon=weapon.upper()
        )
    elif enemy:
        title = random.choice(TEMPLATES_WITH_ENEMY_ONLY).format(
            player=player, enemy=enemy
        )
    elif weapon:
        title = random.choice(TEMPLATES_WITH_WEAPON_ONLY).format(
            player=player, weapon=weapon.upper()
        )
    else:
        title = random.choice(TEMPLATES_GENERAL).format(player=player)

    if is_shorts:
        if "#Shorts" not in title and "#shorts" not in title:
            if len(title) + 8 <= 100:
                title = f"{title} #Shorts"
            else:
                title = f"{title[:91]} #Shorts"

    frag_details = []
    if special_play and special_play != "Auto / Standard Frag":
        frag_details.append(f"🔥 Highlight Play: {special_play}")
    if enemy:
        frag_details.append(f"🎯 Target Eliminated: {enemy}")
    if weapon:
        frag_details.append(f"💥 Weapon Used: {weapon}")

    frag_section = "\n".join(frag_details) + "\n\n" if frag_details else "GGs in the chat!\n\n"
    shorts_footer = "\n\n#Shorts #QuakeLive #Gaming #FPS #Clip" if is_shorts else ""

    server_section = []
    if server_info.get("server_name"):
        server_section.append(f"🌐 Server: {server_info['server_name']}")
    if server_info.get("server_ip"):
        server_section.append(f"🔌 Server IP: {server_info['server_ip']}")
    if server_info.get("map_name"):
        server_section.append(f"🗺️ Map: {server_info['map_name']}")
    if server_info.get("game_mode"):
        server_section.append(f"🏆 Mode: {server_info['game_mode']}")
    if server_info.get("players"):
        lobby_str = ", ".join(server_info["players"][:12])
        if len(server_info["players"]) > 12:
            lobby_str += f" (+{len(server_info['players']) - 12} more)"
        server_section.append(f"👥 Lobby Players: {lobby_str}")

    server_block = "\n".join(server_section) + "\n" if server_section else ""

    description = f"""{title}

{player} bringing the heat in Quake Live!
{frag_section}🎮 Game: Quake Live
👤 Player: {player}
{server_block}📺 Resolution: {"1440x2560 @ 60FPS 2K (YouTube Short)" if is_shorts else "2560x1440 @ 60FPS"}
📅 Date: {dt_str}

Drop a like and subscribe for more Quake Live highlights and arena frags! 🔥

#QuakeLive #Quake #AFPS #FragClip #Gaming #PCGaming #RocketJump #Railgun #FPS #CompetitiveGaming{shorts_footer}"""

    extra_tags = ["Shorts", "YouTube Shorts", "#Shorts"] if is_shorts else []
    server_tags = []
    if server_info.get("server_name"):
        server_tags.append(server_info["server_name"])
    if server_info.get("map_name"):
        server_tags.append(server_info["map_name"])

    tags = list(set(config.get("tags", []) + [
        player, "Quake Live", "Quake", "Arena FPS", "Fragmovie", "Gameplay", "Clutch"
    ] + ([enemy] if enemy else []) + ([weapon] if weapon else []) + ([special_play] if special_play else []) + extra_tags + server_tags))

    return title, description, tags

def handle_browser_upload(video_path: str, config: dict, enemy: str = "", weapon: str = "", special_play: str = "", custom_title: str = ""):
    """Fallback if token or credentials are not yet saved."""
    p = Path(video_path)
    thumb_path = p.with_name(f"{p.stem}_thumb.jpg")
    try:
        extract_action_thumbnail(video_path, output_path=str(thumb_path))
    except Exception as e:
        print("Thumbnail error:", e)

    title, description, tags = generate_metadata(video_path, config, enemy, weapon, special_play, custom_title)

    try:
        subprocess.run(["wl-copy", str(video_path)])
    except Exception:
        pass

    email = config.get("email", "you@example.com")
    profile = config.get("chrome_profile", "Default")

    msg = (
        f"<b>Export Video to YouTube</b>\n\n"
        f"<b>File:</b> {p.name}\n"
        f"<b>Account:</b> {email}\n"
        f"<b>Generated Title:</b> {title}\n"
        f"<b>Action Thumbnail:</b> Saved as <i>{thumb_path.name}</i>\n\n"
        f"<i>Video path has been copied to your clipboard.</i>\n\n"
        "Click <b>Open YouTube Studio</b> to upload now, or <b>Setup Background API</b> for 100% automated headless background uploads."
    )

    cmd = [
        "zenity", "--question",
        "--title=Export to YouTube (YouTube)",
        f"--text={msg}",
        "--ok-label=Open in YouTube Studio",
        "--cancel-label=Setup Background API",
        "--width=520"
    ]
    res = subprocess.run(cmd)
    chrome_bin = shutil.which("google-chrome-stable") or shutil.which("google-chrome") or "/opt/google/chrome/chrome"

    if res.returncode == 0:
        notify(
            "YouTube Studio",
            f"Opening studio for {email}...\nPath copied to clipboard!",
            icon="video-x-generic",
            expire_ms=4000
        )
        subprocess.Popen([
            chrome_bin,
            f"--profile-directory={profile}",
            "https://studio.youtube.com/channel/videos/upload?d=pt"
        ])
    else:
        setup_msg = (
            "<b>How to enable 100% Headless Background Uploads:</b>\n\n"
            "1. Google requires a one-time OAuth key to allow scripts to upload on your behalf.\n"
            "2. In Google Cloud Console, enable the <b>YouTube Data API v3</b>.\n"
            "3. Create an <b>OAuth Client ID (Desktop app)</b>.\n"
            "4. Download the JSON and save it to:\n"
            f"   <b>{CLIENT_SECRETS_FILE}</b>\n\n"
            "Opening Google Cloud Console in Chrome now..."
        )
        subprocess.Popen([
            chrome_bin,
            f"--profile-directory={profile}",
            "https://console.cloud.google.com/apis/credentials"
        ])
        subprocess.run([
            "zenity", "--info",
            "--title=Background API Setup",
            f"--text={setup_msg}",
            "--width=550"
        ])

def get_authenticated_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

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
                return None

            notify("Authentication Required", "Please approve access in your browser for account: youtube", expire_ms=5000)
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
            notify("Authentication Successful!", "Saved credentials for YouTube.", expire_ms=3000)

    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds)

def _execute_video_upload(youtube, file_path: str, title: str, description: str, tags: list, category_id: str, privacy: str, thumb_path: str = None) -> tuple:
    from googleapiclient.http import MediaFileUpload
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(category_id)
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(file_path, chunksize=10*1024*1024, resumable=True)
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media
    )

    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                print(f"Uploading ({Path(file_path).name}): {progress}%")
    except Exception as e:
        if "uploadLimitExceeded" in str(e):
            notify("⚠️ YouTube Daily Upload Limit Reached", "YouTube limits standard channels to 10 uploads/day.\nYour quota will reset in ~24 hours.", icon="dialog-warning", expire_ms=8000)
            print("YouTube daily upload limit reached (10 uploads/24h). Quota will reset in ~24 hours.")
            return None, None
        raise e

    if not response or "id" not in response:
        return None, None

    video_id = response.get("id")
    video_url = f"https://youtu.be/{video_id}"
    print(f"Upload complete for {Path(file_path).name}. Video ID: {video_id}")

    if thumb_path and os.path.exists(thumb_path):
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumb_path)
            ).execute()
            print("Thumbnail uploaded successfully!")
        except Exception as e:
            print("Thumbnail upload warning:", e)
        finally:
            if os.path.exists(thumb_path):
                os.remove(thumb_path)

    return video_id, video_url

def upload_video(video_path: str, enemy: str = None, weapon: str = None, special_play: str = None, custom_title: str = None, prompt: bool = False, shorts: bool = None, shorts_style: str = None, dual_upload: bool = None):
    if not os.path.exists(video_path):
        notify("Upload Failed", f"File not found: {video_path}", icon="dialog-error")
        return

    config = load_config()
    player_name = config.get("player_name", "Player")

    if prompt:
        e, w, s, c = prompt_frag_details(video_path, player_name)
        if e is None and w is None and s is None and c is None:
            print("Upload cancelled by user.")
            return
        enemy = e or enemy or ""
        weapon = w or weapon or ""
        special_play = s or special_play or ""
        custom_title = c or custom_title or ""
    else:
        # Zero-input mode: automatically analyze video footage
        notify("🔍 Analyzing Video...", f"{Path(video_path).name}\nDetecting enemy frags & weapons...", icon="video-x-generic", expire_ms=3000)
        auto_data = analyze_video_gameplay(video_path)
        enemy = enemy or auto_data.get("enemy", "")
        weapon = weapon or auto_data.get("weapon", "")
        special_play = special_play or auto_data.get("feat", "")
        custom_title = custom_title or ""

    dur = 15.0
    try:
        dur_res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True
        )
        dur = float(dur_res.stdout.strip())
    except Exception:
        pass

    # Shorts decision: if shorts is True, or if shorts is None and shorts_mode config is True and dur <= 60s
    is_shorts = (shorts is True) or (shorts is None and config.get("shorts_mode", True) and dur <= 60.0)
    shorts_style = shorts_style or config.get("shorts_style", "crop")
    should_dual = dual_upload if dual_upload is not None else config.get("dual_upload", True)

    # Check credentials
    if not TOKEN_FILE.exists() and not CLIENT_SECRETS_FILE.exists():
        handle_browser_upload(video_path, config, enemy, weapon, special_play, custom_title)
        return

    youtube = get_authenticated_service()
    if not youtube:
        handle_browser_upload(video_path, config, enemy, weapon, special_play, custom_title)
        return

    privacy = config.get("privacy_status", "public")
    cat_id = str(config.get("category_id", "20"))
    server_info = get_quake_server_info()

    uploaded_links = []

    if is_shorts:
        # 1. Upload Vertical YouTube Short
        style_desc = "full-screen vertical trim" if shorts_style == "crop" else "blurred background"
        notify("📱 Creating YouTube Short (9:16)...", f"{Path(video_path).name}\nFormatting {style_desc} with NVENC...", icon="video-x-generic", expire_ms=3000)
        
        shorts_target = None
        try:
            shorts_target = convert_to_shorts_format(video_path, style=shorts_style)
        except Exception as e:
            print("Shorts conversion failed:", e)

        if shorts_target and os.path.exists(shorts_target):
            s_title, s_desc, s_tags = generate_metadata(video_path, config, enemy, weapon, special_play, custom_title, is_shorts=True, server_info=server_info)
            detail_line = f"Target: {enemy} | Weapon: {weapon}" if enemy else f"Weapon: {weapon}"
            notify("⏳ Uploading YouTube Short...", f"{s_title}\n{detail_line}\nVisibility: {privacy.upper()}", icon="emblem-synchronizing", expire_ms=4000)
            
            s_thumb = None
            try:
                s_thumb = extract_action_thumbnail(shorts_target)
            except Exception:
                pass

            s_id, _ = _execute_video_upload(youtube, shorts_target, s_title, s_desc, s_tags, cat_id, privacy, s_thumb)
            if s_id:
                s_url = f"https://www.youtube.com/shorts/{s_id}"
                uploaded_links.append(s_url)

            if os.path.exists(shorts_target):
                try:
                    os.remove(shorts_target)
                except Exception:
                    pass

        # 2. Dual upload: Also upload the original 16:9 widescreen 1440p version
        if should_dual:
            notify("🖥️ Uploading 1440p Widescreen Version...", f"Original 2560x1440 60FPS for {Path(video_path).name}...", icon="emblem-synchronizing", expire_ms=4000)
            w_title, w_desc, w_tags = generate_metadata(video_path, config, enemy, weapon, special_play, custom_title, is_shorts=False, server_info=server_info)
            
            w_thumb = None
            try:
                w_thumb = extract_action_thumbnail(video_path)
            except Exception:
                pass

            w_id, w_url = _execute_video_upload(youtube, video_path, w_title, w_desc, w_tags, cat_id, privacy, w_thumb)
            if w_url:
                uploaded_links.append(w_url)

    else:
        # Standard Widescreen 16:9 Upload only
        w_title, w_desc, w_tags = generate_metadata(video_path, config, enemy, weapon, special_play, custom_title, is_shorts=False, server_info=server_info)
        detail_line = f"Target: {enemy} | Weapon: {weapon}" if enemy else f"Weapon: {weapon}"
        notify("⏳ Uploading Video to YouTube...", f"{w_title}\n{detail_line}\nVisibility: {privacy.upper()}", icon="emblem-synchronizing", expire_ms=5000)

        w_thumb = None
        try:
            w_thumb = extract_action_thumbnail(video_path)
        except Exception:
            pass

        w_id, w_url = _execute_video_upload(youtube, video_path, w_title, w_desc, w_tags, cat_id, privacy, w_thumb)
        if w_url:
            uploaded_links.append(w_url)

    # Clipboard and completion notifications
    if uploaded_links:
        primary_link = uploaded_links[0]
        try:
            subprocess.run(["wl-copy", "\n".join(uploaded_links)], check=True)
        except Exception:
            pass

        subprocess.Popen(["pw-play", "/usr/share/sounds/freedesktop/stereo/complete.oga"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if len(uploaded_links) > 1:
            notify("✅ Dual YouTube Upload Complete! (PUBLIC)", f"📱 Short: {uploaded_links[0]}\n🖥️ 1440p: {uploaded_links[1]}", icon="dialog-ok", expire_ms=8000)
        else:
            notify("✅ YouTube Upload Complete! (PUBLIC)", f"Link copied: {primary_link}", icon="dialog-ok", expire_ms=7000)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Quake Live YouTube Uploader for YouTube")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--enemy", default=None, help="Enemy player name(s)")
    parser.add_argument("--weapon", default=None, help="Weapon used")
    parser.add_argument("--feat", default=None, help="Special feat / highlight")
    parser.add_argument("--title", default=None, help="Custom YouTube title")
    parser.add_argument("--prompt", "--manual", action="store_true", help="Prompt interactive GUI dialog instead of auto-analyzing")
    parser.add_argument("--shorts", action="store_true", default=None, help="Force formatting and uploading as a 9:16 vertical YouTube Short")
    parser.add_argument("--horizontal", "--widescreen", dest="shorts", action="store_false", help="Force 16:9 widescreen upload instead of Short")
    parser.add_argument("--shorts-style", choices=["crop", "blur"], default=None, help="Shorts layout: 'crop' (full vertical trim) or 'blur' (blurred background)")
    parser.add_argument("--dual", dest="dual_upload", action="store_true", default=None, help="Upload both 9:16 Short and 16:9 widescreen versions")
    parser.add_argument("--no-dual", dest="dual_upload", action="store_false", help="Upload only one version")
    args = parser.parse_args()

    upload_video(
        video_path=args.video,
        enemy=args.enemy,
        weapon=args.weapon,
        special_play=args.feat,
        custom_title=args.title,
        prompt=args.prompt,
        shorts=args.shorts,
        shorts_style=args.shorts_style,
        dual_upload=args.dual_upload
    )
