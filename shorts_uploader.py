#!/usr/bin/env python3
import os
import sys
import re
import json
import time
import fcntl
import random
import shutil
import subprocess
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter
from zoneinfo import ZoneInfo

# Ensure site-packages with cv2, rapidocr_onnxruntime, etc. are available
dictate_site = os.path.expanduser("~/.local/share/dictate-env/lib/python3.14/site-packages")
if os.path.exists(dictate_site) and dictate_site not in sys.path:
    sys.path.insert(0, dictate_site)

# Limit OCR / ONNX / OpenMP thread explosion to keep system smooth
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "4")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "4")

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
OVERRIDES_FILE = CONFIG_DIR / "clip_overrides.json"
QUEUE_DIR = CONFIG_DIR / "queue"
QUEUE_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_STATE_FILE = QUEUE_DIR / "state.json"
QUEUE_LOCK_FILE = CONFIG_DIR / ".queue.lock"
QUEUE_WORKER_LOCK_FILE = CONFIG_DIR / ".queue_worker.lock"
UPLOAD_HISTORY_FILE = CONFIG_DIR / "upload_history.json"
QUEUE_LOG_FILE = CONFIG_DIR / "queue.log"
PT_TZ = ZoneInfo("America/Los_Angeles")
RETRYABLE_UPLOAD_ERRORS = {"uploadLimitExceeded", "quotaExceeded", "transientError"}

TEMPLATES_WITH_ENEMY_AND_WEAPON = [
    "SICK {weapon} PLAY! ⚡💀 {player} vs {enemy}",
    "CLUTCH {weapon} FRAG! 🚀💥 {player} vs {enemy}",
    "INSANE {weapon} PREDICTION! 🎯🧠 {player} vs {enemy}",
    "POINT-BLANK {weapon} ELIMINATION! 💥🔥 {player} vs {enemy}",
    "CLEAN {weapon} DUEL WIN! 🎯⚡ {player} vs {enemy}",
    "NASTY {weapon} FLICK! 🚀💀 {player} vs {enemy}",
    "200 IQ {weapon} OUTPLAY! 🧠💥 {player} vs {enemy}",
    "GOD-TIER {weapon} HIT! 🎯🔥 {player} vs {enemy}"
]

TEMPLATES_WITH_ENEMY_ONLY = [
    "CLUTCH ARENA DUEL! 💀🔥 {player} vs {enemy}",
    "INSANE ROUND-ENDING FRAG! 🚀💥 {player} vs {enemy}",
    "CLEAN CLAN ARENA ELIMINATION! 🎯💀 {player} vs {enemy}",
    "UNREAL ARENA OUTPLAY! 🔥⚡ {player} vs {enemy}"
]

TEMPLATES_WITH_WEAPON_ONLY = [
    "DISGUSTING {weapon} FRAG! 🚀💥 {player}",
    "GOD-TIER {weapon} FLICK! 🎯🔥 {player}",
    "MELTING OPPONENTS WITH {weapon}! ⚡💀 {player}",
    "CLEANEST {weapon} SHOT! 💥🔥 {player}",
    "PURE FILTH WITH THE {weapon}! 🎯💀 {player}",
    "INSANE {weapon} PREDICTION! 🚀🔥 {player}"
]

TEMPLATES_TWO_FRAGS_GENERIC = [
    "DOUBLE FRAG! 💀⚡ {player} vs {enemy}",
    "2 FRAGS! 💀🔥 {player} vs {enemy}",
    "TWO DOWN! 🚀💥 {player} vs {enemy}",
    "NASTY DOUBLE FRAG! 💀⚡ {player} vs {enemy}",
    "CLEAN 2-FRAG HIGHLIGHT! 🎯💀 {player} vs {enemy}"
]

TEMPLATES_SPECIAL_FEATS = {
    "Impressive Medal": [
        "IMPRESSIVE MEDAL VIA CONSECUTIVE RAILS! 🎯⚡ {player} vs {enemy}",
        "IMPRESSIVE! BACK-TO-BACK RAILGUN HITS! 🎯💀 {player} vs {enemy}",
        "GOD-TIER RAIL ACCURACY! IMPRESSIVE MEDAL ⚡🎯 {player} vs {enemy}"
    ],
    "Two Frags in Two Seconds": [
        "TWO FRAGS IN TWO SECONDS! 💀⚡ {player} vs {enemy}",
        "2 FRAGS IN 2 SECONDS! 💀🔥 {player} vs {enemy}",
        "EXCELLENT AWARD! 2 FRAGS IN 2 SECONDS 💀⚡ {player} vs {enemy}"
    ],
    "Excellent Medal": [
        "EXCELLENT MEDAL! 2 FAST FRAGS 💀⚡ {player} vs {enemy}",
        "2 FRAGS IN 2 SECONDS! 💀🔥 {player} vs {enemy}"
    ],
    "Round-Ending Telefrag": [
        "ROUND-ENDING TELEFRAG! 🌀🏆 {player} vs {enemy}",
        "CLUTCH ROUND-ENDING TELEFRAG! 🌀🔥 {player} vs {enemy}",
        "INSANE ROUND-ENDING TELEFRAG! 🌀💀 {player} vs {enemy}"
    ],
    "Telefrag": [
        "DISGUSTING TELEFRAG! 🌀💀 {player} vs {enemy}",
        "WRONG PLACE, WRONG TIME TELEFRAG! 🌀💥 {player} vs {enemy}",
        "UNREAL TIMING TELEFRAG! 🌀⚡ {player} vs {enemy}"
    ],
    "Round-Ending Frag": [
        "CLUTCH ROUND-ENDING FRAG! 🏆🔥 {player} vs {enemy}",
        "SEALING THE ROUND VICTORY! 💥🏆 {player} vs {enemy}",
        "ROUND-ENDING CLUTCH! 🎯🏆 {player} vs {enemy}"
    ],
    "Back-to-Back Double Rails": [
        "BACK-TO-BACK DOUBLE RAILS! 🎯💥 {player} vs {enemy}",
        "4 SHOTS, 6 RAILS! UNREAL COLLATERALS 💀🔥 {player} vs {enemy}",
        "DOUBLE COLLATERAL RAILGUN STREAK! 🎯⚡ {player} vs {enemy}"
    ],
    "3 Rails in a Row": [
        "3 RAILS IN A ROW! SURGICAL ACCURACY 🎯⚡ {player} vs {enemy}",
        "3 RAILS IN A ROW & HEADSHOT FINISH! 🎯💀 {player} vs {enemy}",
        "GOD-TIER ACCURACY! 3 RAILS IN A ROW 🎯🔥 {player} vs {enemy}"
    ],
    "Double Rail / Collateral": [
        "DOUBLE RAIL COLLATERAL! 🎯💥 {player} vs {enemy}",
        "ONE SHOT, TWO KILLS! DOUBLE RAIL 💀⚡ {player} vs {enemy}",
        "PIERCING DOUBLE RAIL! 🎯🔥 {player} vs {enemy}"
    ],
    "Air Rocket / Direct": [
        "MIDAIR AIR-ROCKET PREDICTION! 🚀🎯 {player} vs {enemy}",
        "DELETED OUT OF THE SKY! AIR ROCKET 🚀💥 {player} vs {enemy}",
        "MIDAIR DIRECT HIT! 🚀🔥 {player} vs {enemy}"
    ],
    "Midair Railgun": [
        "MIDAIR RAIL FLICK! SNIPED OUT OF THE SKY 🎯⚡ {player} vs {enemy}",
        "SKEET SHOOTING MIDAIR RAIL! 🎯💀 {player} vs {enemy}"
    ],
    "Clutch / Wipeout": [
        "1vX CLAN ARENA CLUTCH! UNREAL WIPEOUT 💀🔥 {player} vs {enemy}",
        "IMPOSSIBLE CLAN ARENA CLUTCH! 🚀🎯 {player} vs {enemy}"
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
        "account_name": "YouTube",
        "email": "you@example.com",
        "chrome_profile": "Default",
        "player_name": "Player",
        "privacy_status": "public",  # "public", "unlisted", or "private"
        "category_id": "20",         # 20 = Gaming
        "shorts_mode": True,         # Automatically format <=60s clips as 9:16 YouTube Shorts
        "shorts_style": "crop",      # "crop" (full-screen vertical trim) or "blur" (blurred background)
        "dual_upload": False,        # Upload only 9:16 vertical Shorts by default (no widescreen duplicate)
        "cut_on_death": True,        # Stop video when player dies and do not show respawn
        "cut_before_respawn": True,  # Cut ending right before respawn countdown / scoreboard happens
        "cut_recording_chime": True, # Automatically detect and cut Steam Keypad + recording notification chime
        "display_left_chat": True,   # Display left-side chat and announcements at bottom of Shorts
        "death_cutoff_delay": 1.2,   # Extra seconds after death message to show fatal hit and comments
        "audio_loudness_i": -15.0,   # Integrated loudness target (LUFS)
        "audio_true_peak": -1.5,     # True peak ceiling (dBFS)
        "action_slowmo": True,       # Slow down and zoom in on action climax to extend excitement
        "action_slowmo_speed": 0.25, # Slow motion speed multiplier (0.25 = 4x slow motion)
        "action_zoom": True,         # Zoom in dynamically on the combat/frag action
        "action_emojis": True,       # Overlay fitting emojis and highlight banner
        "action_excitement_audio": True, # Layer iconic Quake announcer & impact audio
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
    'k/d', 'score', 'score k/d', 'score k', 'teams are tied', 'ping', 'time', 'dmg', 'acc', 'lead', 'tied',
    'red wins the round', 'matchinprogress', 'players',
    'player stats', 'player', 'player (6/6)', 'dmg weap', 'item to use', 'no item to use', 'item to u',
    'dead and gone', 'dead and',
    'aerowalk', 'verticalvengeance', 'overkill', '12players', 'veap', 'the last s', 'last s',
    'grinder', 'beams', 'blood run', 'the last standing', 'live', 'quake live', 'quake', 'quakelive',
    'dead', 'noitem', 'noitemtouse', 'no item to use', 'team'
}

def clean_player_name(raw: str, server_players: list = None, player_name: str = 'Player') -> str:
    s = raw.strip()
    s = re.sub(r'\s*\d+fps.*$', '', s, flags=re.I)
    s = re.sub(r'^(red|blue)\s+team\s*', '', s, flags=re.I)
    s = re.sub(r'^\d+/\d+$', '', s)
    s = re.sub(r'^\d+:\d+$', '', s)
    # Explicitly preserve valid Quake symbol gamer tags like '^'
    if s.rstrip('.') == '^':
        return '^'
    # Reject scoreboard table headers and UI indicators
    if re.search(r'\b(score\s*k/?d|k/?d|dmg\s*weap|player\s*\(\d+/\d+\))\b', s, re.I):
        return ''
    if 'score' in s.lower() and ('k' in s.lower() or 'd' in s.lower()):
        return ''
    if s.lower().startswith('player') and ('(' in s or '/' in s or 'stats' in s.lower()):
        return ''
    s = re.sub(r'^[^\w\u4e00-\u9fff™\^]+|[^\w\u4e00-\u9fff™\^]+$', '', s)
    if s == '^':
        return '^'
    if not s or s.lower() in IGNORE_WORDS or s.isdigit():
        return ''
    if s.lower() in ['live', 'quake', 'quakelive', 'dead', 'loe', 'oe', 'item', 'team']:
        return ''
    if re.search(r'\b(score\s+k/?d|k/?d|dmg\s+weap|player\s*\(\d+/\d+\))\b', s, re.I):
        return ''
    if re.match(r'^\d+(\.\d+)?[kK]$', s) or re.match(r'^\d+dmg$', s, re.I):
        return ''
    if re.match(r'^k/?d$', s, re.I):
        return ''
    if s.lower() in [player_name.lower(), 'player', 'you']:
        return ''
    if server_players:
        import difflib
        matches = difflib.get_close_matches(s, server_players, n=1, cutoff=0.7)
        if matches:
            return matches[0]
        if len(s) >= 3:
            for sp in server_players:
                if sp.lower().startswith(s.lower()):
                    return sp
    return s.strip()

def analyze_video_gameplay(video_path: str, player_name: str = 'Player') -> dict:
    """
    Automatically analyzes the video using computer vision & OCR
    to detect enemy names, weapon used, and combat feats with zero user input.
    Prioritizes:
      1. Center Frag Announcement ('You fragged <Victim>.')
      2. Top-Left Killfeed ('<Player> [icon] <Victim>')
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

        # Sample 12 frames across the clip weighted towards combat/kill action
        percentages = [0.35, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

        frag_victims = Counter()
        roster_victims = Counter()
        killfeed_victims = Counter()
        combat_names = Counter()
        frag_weapons = Counter()
        weapon_scores = Counter()
        has_double_rail = False
        has_triple_rail = False
        rail_hit_count = 0
        has_air_rocket_medal = False
        has_excellent_medal = False
        is_telefrag = False
        is_game_ending = False
        has_humiliation = False
        player_died = False

        # Pre-fetch server players for fuzzy matching OCR text
        server_players = []
        try:
            s_info = get_quake_server_info()
            server_players = s_info.get("players", [])
        except Exception:
            pass

        for pct in percentages:
            ts = duration * pct
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
            ret, frame = cap.read()
            if not ret:
                continue
            h, w, _ = frame.shape

            # Weapon check from bottom-center active weapon icon (to the left of ammo counter)
            # Scoped to icon area: h: 0.86..0.98, w: 0.43..0.49
            bc = frame[int(h * 0.86):int(h * 0.98), int(w * 0.43):int(w * 0.49)]
            hsv = cv2.cvtColor(bc, cv2.COLOR_BGR2HSV)
            mask_purple = cv2.inRange(hsv, (120, 60, 60), (165, 255, 255))
            mask_green = cv2.inRange(hsv, (35, 100, 100), (85, 255, 255))
            mask_red = cv2.bitwise_or(
                cv2.inRange(hsv, (0, 120, 120), (10, 255, 255)),
                cv2.inRange(hsv, (170, 120, 120), (180, 255, 255))
            )
            mask_orange = cv2.inRange(hsv, (11, 140, 140), (22, 255, 255))
            mask_yellow = cv2.inRange(hsv, (23, 100, 100), (35, 255, 255))
            mask_cyan = cv2.inRange(hsv, (85, 100, 100), (105, 255, 255))

            c_purple = cv2.countNonZero(mask_purple)
            c_green = cv2.countNonZero(mask_green)
            c_red = cv2.countNonZero(mask_red)
            c_yellow = cv2.countNonZero(mask_yellow)
            c_orange = cv2.countNonZero(mask_orange)
            c_cyan = cv2.countNonZero(mask_cyan)

            current_wep = None
            if c_purple > 200 and c_purple > c_red:
                current_wep = "Plasma Gun"
            elif c_green > 200 and c_green > c_red:
                current_wep = "Railgun"
            elif c_yellow > 200 and c_yellow > c_red:
                current_wep = "Lightning Gun"
            elif c_cyan > 200 and has_humiliation:
                current_wep = "Gauntlet"
            elif c_orange > 200 and c_orange > c_red:
                current_wep = "Shotgun"
            elif c_red > 200:
                current_wep = "Rocket Launcher"

            # 1. Authoritative Frag Announcement ('You fragged <Player>.') in center-upper screen
            frag_roi = frame[int(h * 0.12):int(h * 0.35), int(w * 0.20):int(w * 0.80)]
            res_f, _ = engine(frag_roi)
            frame_frag_found = False
            for it in res_f or []:
                t = it[1].strip()
                if re.search(r'fragged\s+by|following|you\s*died|you\s*were\s*fragged', t, re.I):
                    player_died = True
                if re.search(r'excellent', t, re.I) or re.search(r'two\s+frags', t, re.I):
                    has_excellent_medal = True
                if re.search(r'match\s*summary', t, re.I) or re.search(r'roundlimit', t, re.I) or re.search(r'wins\s+by\s+a\s+score', t, re.I) or re.search(r'wins\s+the\s+round', t, re.I):
                    is_game_ending = True
                m = re.search(r'(?:you\s*)?fragged\s+([A-Za-z0-9_\u4e00-\u9fff™\.\-\^ ]+?)(?:\.|$)', t, re.I)
                if m:
                    cn = clean_player_name(m.group(1).strip(), server_players)
                    if cn:
                        frag_victims[cn] += 20
                        frame_frag_found = True

            # 2. Top-right scoreboard roster check for newly dead enemies (<Player> DEAD)
            roster_roi = frame[0:int(h * 0.12), int(w * 0.75):w]
            res_rost, _ = engine(roster_roi)
            rost_texts = [it[1].strip() for it in (res_rost or [])]
            for idx, r_txt in enumerate(rost_texts):
                if re.search(r'\bdead\b', r_txt, re.I):
                    candidate = re.sub(r'\bdead\b', '', r_txt, flags=re.I).strip()
                    if not candidate and idx > 0:
                        candidate = rost_texts[idx - 1]
                    cn = clean_player_name(candidate, server_players)
                    if cn:
                        roster_victims[cn] += 20
                        frame_frag_found = True

            # 3. Killfeed in top-left ('<Player> [weapon icon] <Victim>')
            kf_roi = frame[int(h * 0.01):int(h * 0.15), int(w * 0.15):int(w * 0.45)]
            res_kf, _ = engine(kf_roi)
            kf_all_text = " ".join([it[1].strip() for it in (res_kf or [])])
            p_tok = re.sub(r"[^a-z0-9]", "", player_name.lower())
            if not re.search(r'joined\s+the|disconnected|spectator', kf_all_text, re.I) and (not p_tok or p_tok in re.sub(r"[^a-z0-9]", "", kf_all_text.lower())):
                for it in res_kf or []:
                    t = it[1].strip()
                    if re.search(r'fragged\s+by|following', t, re.I):
                        player_died = True
                    if re.search(r'telefrag', t, re.I):
                        is_telefrag = True
                    cn = clean_player_name(t, server_players)
                    if cn:
                        killfeed_victims[cn] += 10
                        frame_frag_found = True

            if frame_frag_found and current_wep:
                frag_weapons[current_wep] += 100

            # 3. Quake Live Medals at top-center (Midair wreath)
            # Note: Medal detection strictly requires verified OCR text to prevent false positives from loose HSV masks
            award_roi = frame[int(h * 0.05):int(h * 0.22), int(w * 0.40):int(w * 0.60)]
            hsv_award = cv2.cvtColor(award_roi, cv2.COLOR_BGR2HSV)
            mask_wreath = cv2.inRange(hsv_award, (40, 100, 100), (85, 255, 255))
            mask_wings = cv2.inRange(hsv_award, (0, 0, 200), (180, 50, 255))
            if cv2.countNonZero(mask_wreath) > 25 and cv2.countNonZero(mask_wings) > 15:
                has_air_rocket_medal = True

            # 4. Center combat zone for damage numbers, medals, and target overhead nameplates
            center_roi = frame[int(h * 0.25):int(h * 0.75), int(w * 0.25):int(w * 0.75)]
            res_c, _ = engine(center_roi)

            frame_damage = False
            for it in res_c or []:
                t = it[1].strip()
                if re.search(r'fragged\s+by|following|you\s*died', t, re.I):
                    player_died = True
                if re.search(r'humiliation', t, re.I):
                    has_humiliation = True
                if re.search(r'telefrag', t, re.I):
                    is_telefrag = True
                if re.search(r'match\s*summary', t, re.I) or re.search(r'roundlimit', t, re.I) or re.search(r'wins\s+by\s+a\s+score', t, re.I) or re.search(r'wins\s+the\s+round', t, re.I):
                    is_game_ending = True
                if t.isdigit() and int(t) in range(15, 120):
                    frame_damage = True
                    if t == "80":
                        rail_hit_count += 1
                elif "80" in t:
                    frame_damage = True
                    rail_hit_count += 1
                    if len(t) >= 4:
                        has_double_rail = True
                else:
                    cn = clean_player_name(t, server_players)
                    if cn:
                        combat_names[cn] += 5 if pct >= 0.60 else 2

            if current_wep and not player_died:
                pts = 1
                if pct >= 0.60:
                    pts += 3
                if frame_damage:
                    pts += 10
                weapon_scores[current_wep] += pts

        cap.release()

        # Multi-victim detection (captures both players in two-frag clips):
        top_enemies = []
        # Tier 1: Players explicitly fragged ('You fragged <Victim>.')
        for e, _ in frag_victims.most_common(2):
            if e and e not in top_enemies:
                top_enemies.append(e)

        # Tier 2: Roster confirmation for eliminated opponents (<Victim> DEAD)
        if len(top_enemies) < 2 and roster_victims:
            for e, _ in roster_victims.most_common(2):
                if e and e not in top_enemies:
                    top_enemies.append(e)
                    if len(top_enemies) >= 2:
                        break

        # Tier 3: Killfeed confirmation for additional victims
        if len(top_enemies) < 2 and killfeed_victims:
            for e, _ in killfeed_victims.most_common(2):
                if e and e not in top_enemies:
                    top_enemies.append(e)
                    if len(top_enemies) >= 2:
                        break

        # Tier 4: In-combat crosshair nameplate during sustained combat/damage (single duel opponent if no frag confirmed)
        if not top_enemies and combat_names:
            for e, score in combat_names.most_common(5):
                if e and score >= 3:
                    top_enemies.append(e)
                    break

        if frag_weapons:
            wep = frag_weapons.most_common(1)[0][0]
        elif weapon_scores:
            wep = weapon_scores.most_common(1)[0][0]
        else:
            wep = "Railgun"

        if player_died:
            is_game_ending = False

        if rail_hit_count >= 3:
            has_triple_rail = True
        elif rail_hit_count >= 2:
            has_double_rail = True

        feat = ""
        if is_telefrag:
            wep = "Telefrag"
            feat = "Round-Ending Telefrag" if (is_game_ending and not player_died) else "Telefrag"
        elif is_game_ending and not player_died:
            feat = "Round-Ending Frag"
        elif has_excellent_medal and not player_died:
            feat = "Two Frags in Two Seconds"
        elif wep == "Rocket Launcher" and has_air_rocket_medal and not player_died:
            feat = "Air Rocket / Direct"
        elif wep == "Railgun" and has_triple_rail and not player_died:
            feat = "3 Rails in a Row"
        elif wep == "Railgun" and has_double_rail and not player_died:
            feat = "Back-to-Back Double Rails"
        elif wep == "Gauntlet" and has_humiliation:
            feat = "Humiliation / Gauntlet"
        elif wep == "Gauntlet" and not has_humiliation:
            wep = "Railgun"

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
        "--add-entry=Enemy Fragged (e.g. SnapWARP & The-Mangler, or blank to auto-detect)",
        "--add-combo=Weapon Used",
        "--combo-values=Railgun|Rocket Launcher|Lightning Gun|Shotgun|Gauntlet|Plasma Gun|Grenade Launcher|BFG",
        "--add-combo=Special Highlight / Feat",
        "--combo-values=Auto / Standard Frag|3 Rails in a Row|Two Frags in Two Seconds|Round-Ending Frag|Round-Ending Telefrag|Back-to-Back Double Rails|Double Rail / Collateral|Air Rocket / Direct|Midair Railgun|Clutch / Wipeout",
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
    """
    Scans video frames to extract an action thumbnail featuring an upclose enemy,
    combat action, damage dealt/produced, and weapon discharge.
    """
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

    try:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        best_t = max(1.0, duration * 0.5)
        best_score = -1.0
        best_frame = None

        # Sample every 0.12s across the middle 80% of the clip
        step = max(1, int(fps * 0.12))
        start_frame = int(fps * 0.8)
        end_frame = max(start_frame + 1, int(total_frames * 0.95))

        for fno in range(start_frame, end_frame, step):
            t = fno / fps
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                break
            h, w, _ = frame.shape

            # Focus on center 50% width and 60% height (crosshair & target duel area)
            center = frame[int(h * 0.20):int(h * 0.80), int(w * 0.25):int(w * 0.75)]
            hsv = cv2.cvtColor(center, cv2.COLOR_BGR2HSV)

            # High-saturation and bright enemy models (bright green, yellow, red, cyan)
            sat = hsv[:, :, 1]
            val = hsv[:, :, 2]
            enemy_mask = (sat > 95) & (val > 115)
            enemy_pixels = int(np.sum(enemy_mask))

            # Edge density (sparks, damage numbers, rail rings, weapon flashes, hit explosions)
            gray = cv2.cvtColor(center, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 60, 160)
            edge_pixels = int(np.sum(edges > 0))

            score = (enemy_pixels * 1.8) + edge_pixels
            if score > best_score:
                best_score = score
                best_t = t
                best_frame = frame

        cap.release()

        if best_frame is not None and best_score > 5000:
            cv2.imwrite(output_path, best_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            print(f"[Thumbnail] Selected enemy close-up action frame at {best_t:.2f}s (score: {int(best_score)}).")
            return output_path

    except Exception as e:
        print("Action thumbnail selection notice:", e)

    # Fallback if scanning fails
    seek_time = max(1.0, duration * 0.55)
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

CHIME_REFS = [
    os.path.expanduser("~/.local/share/Steam/steamui/sounds/desktop_toast_default.wav.orig"),
    os.path.expanduser("~/.local/share/Steam/steamui/sounds/desktop_toast_short.wav.orig"),
    os.path.expanduser("~/.local/share/Steam/steamui/sounds/recording_highlight.wav.orig"),
    os.path.expanduser("~/.local/share/Steam/steamui/sounds/deck_ui_toast.wav.orig"),
]

def detect_recording_chime(video_path: str, dur: float, threshold: float = 1200.0, scan_window: float = 1.8):
    """
    Scans the trailing end of the video audio (last 1.8s) for Steam Keypad + notification/recording chimes.
    Returns (t_event, corr_score, ref_name) if a chime is found, else None.
    """
    if dur < 2.0:
        return None
    start_t = max(0.0, dur - scan_window)
    wav_path = f"/tmp/chime_check_{os.getpid()}_{int(time.time() * 1000)}.wav"
    cmd = [
        "ffmpeg", "-y", "-ss", f"{start_t:.3f}",
        "-i", video_path,
        "-vn", "-ac", "2", "-ar", "44100",
        "-c:a", "pcm_s16le", wav_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode != 0 or not os.path.exists(wav_path):
        return None
    try:
        import wave, struct, math
        with wave.open(wav_path, "rb") as w:
            n_frames = w.getnframes()
            if n_frames < 4410:  # at least 0.1s
                return None
            samples = struct.unpack(f"<{n_frames * 2}h", w.readframes(n_frames))
            mono = [(samples[i] + samples[i+1]) / 2.0 for i in range(0, len(samples), 2)]
            audio_ds = mono[::8]

        detected_events = []
        for ref_path in CHIME_REFS:
            if not os.path.exists(ref_path):
                continue
            with wave.open(ref_path, "rb") as rw:
                r_ch = rw.getnchannels()
                r_sr = rw.getframerate()
                r_frames = rw.getnframes()
                r_samples = struct.unpack(f"<{r_frames * r_ch}h", rw.readframes(r_frames))
                if r_ch == 2:
                    r_mono = [(r_samples[i] + r_samples[i+1]) / 2.0 for i in range(0, len(r_samples), 2)]
                else:
                    r_mono = [float(s) for s in r_samples]
                r_ds = r_mono[::8]
                sub_sr = r_sr // 8
                r_energy = math.sqrt(sum(x * x for x in r_ds)) + 1e-9
                ref_norm = [x / r_energy for x in r_ds]

            L = len(ref_norm)
            if len(audio_ds) < L:
                continue
            best_corr = 0.0
            best_idx = 0
            for i in range(0, len(audio_ds) - L, 4):
                c = sum(audio_ds[i+j] * ref_norm[j] for j in range(0, L, 4))
                if abs(c) > best_corr:
                    best_corr = abs(c)
                    best_idx = i
            if best_corr >= threshold:
                t_event = start_t + (best_idx / sub_sr)
                detected_events.append((t_event, best_corr, os.path.basename(ref_path)))

        if detected_events:
            # Return the highest correlation detected event
            detected_events.sort(key=lambda x: -x[1])
            return detected_events[0]
    except Exception as e:
        print(f"Chime detection warning: {e}")
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass
    return None

def detect_clip_cut_point(video_path: str, player_name: str = "Player", cutoff_delay: float = 0.5, check_chime: bool = True, check_ocr: bool = True) -> tuple:
    """
    Analyzes clip using OCR and audio cross-correlation to detect:
      1. Steam Keypad + recording notification chime (cuts immediately before the chime)
      2. Player death (Fragged by, Following, You were fragged, You died)
      3. Round win / completion (wins the round, roundlimit hit, match summary)
      4. Respawn / scoreboard countdown (Round Begins in, Round starts in, Begins in, Spectators)
    Regardless of whether the player died or survived the round, cuts the ending
    right before respawn / new round / scoreboard / chime happens to edit out erroneous footage.
    Returns:
      (should_cut: bool, cut_duration: float, total_duration: float, reason: str)
    """
    try:
        import cv2

        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return False, 0.0, 0.0, "Could not open clip"

        dur = total_frames / fps
        if dur < 2.5:
            cap.release()
            return False, dur, dur, "Clip very short"

        death_events = []
        frag_events = []
        win_events = []
        respawn_events = []

        if check_ocr:
            from rapidocr_onnxruntime import RapidOCR
            engine = RapidOCR()
            step_frames = max(1, int(fps * 0.4))

            for fno in range(0, total_frames, step_frames):
                t = fno / fps
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ret, frame = cap.read()
                if not ret:
                    break
                h, w, _ = frame.shape

                roi = frame[int(h * 0.05):int(h * 0.50), int(w * 0.15):int(w * 0.85)]
                res, _ = engine(roi)
                for _, txt, _ in (res or []):
                    tc = txt.strip()
                    if re.search(r'fragged\s+by', tc, re.I) or re.search(r'\bfollowing\s+', tc, re.I) or re.search(r'you\s*were\s*fragged', tc, re.I) or re.search(r'you\s*died', tc, re.I):
                        death_events.append((t, tc))
                    elif re.search(r'you\s*fragged', tc, re.I):
                        frag_events.append((t, tc))
                    elif re.search(r'wins\s+the\s+round', tc, re.I) or re.search(r'roundlimit\s+hit', tc, re.I) or re.search(r'match\s+summary', tc, re.I):
                        win_events.append((t, tc))
                    elif re.search(r'round\s*begins\s+in', tc, re.I) or re.search(r'round\s*starts\s+in', tc, re.I) or re.search(r'begins\s+in', tc, re.I) or re.search(r'spectators:', tc, re.I):
                        respawn_events.append((t, tc))

        cap.release()

        # Determine the initial countdown / warmup phase at the start of the clip
        initial_countdown_end = 0.0
        respawn_events.sort(key=lambda x: x[0])
        if respawn_events and respawn_events[0][0] <= 3.5:
            last_rc = respawn_events[0][0]
            for (t, txt) in respawn_events:
                if t - last_rc <= 2.0:
                    last_rc = t
                    initial_countdown_end = t
                else:
                    break

        # Filter win events: ignore stale wins if frags occur later!
        relevant_win = None
        for (t, txt) in win_events:
            if any(tf > t + 1.5 for (tf, _) in frag_events):
                continue
            if relevant_win is None or t < relevant_win[0]:
                relevant_win = (t, txt)

        # Filter deaths: ignore stale deaths from earlier round or warmup
        # Any death before t <= 2.5s is stale text left over from before the clip or pre-round.
        # Also, if there is a round win event later, any death before that win was not the final action.
        relevant_death = None
        for (t, txt) in death_events:
            if t <= max(2.5, initial_countdown_end + 1.0):
                continue
            if any(tf > t + 2.0 for (tf, _) in frag_events):
                continue
            if relevant_win and t < relevant_win[0]:
                continue
            if relevant_death is None or t < relevant_death[0]:
                relevant_death = (t, txt)

        # Filter respawn countdowns: ignore initial pre-round countdown and any events before death/frags/wins
        first_respawn = None
        min_respawn_t = max(10.0, initial_countdown_end + 1.0)
        if relevant_death:
            min_respawn_t = max(min_respawn_t, relevant_death[0])
        if frag_events:
            min_respawn_t = max(min_respawn_t, max(tf for tf, _ in frag_events))
        if relevant_win:
            min_respawn_t = max(min_respawn_t, relevant_win[0])

        for (t, txt) in respawn_events:
            if t < min_respawn_t:
                continue
            if first_respawn is None or t < first_respawn[0]:
                first_respawn = (t, txt)

        cut_t = dur
        reason = "Full clip"
        should_cut = False

        # Keep at least 1.2s (or configured death_cutoff_delay) after death / action / round win to see chat comments
        effective_delay = max(1.2, float(cutoff_delay))

        if relevant_win:
            # Show the win and victory banner / chat reactions, keeping at least 3.5-4.0s after the win
            w_cut = max(2.5, min(dur, round(relevant_win[0] + max(3.5, effective_delay + 2.0), 2)))
            if first_respawn and first_respawn[0] > relevant_win[0]:
                r_cut = max(2.5, min(dur, round(max(first_respawn[0] - 0.2, relevant_win[0] + effective_delay), 2)))
                cut_t = min(w_cut, r_cut)
                reason = f"Round win: {relevant_win[1]}"
                should_cut = True
            else:
                cut_t = w_cut
                reason = f"Round win: {relevant_win[1]}"
                should_cut = True
        elif relevant_death:
            d_cut = max(2.5, min(dur, round(relevant_death[0] + effective_delay, 2)))
            if d_cut < cut_t:
                cut_t = d_cut
                reason = f"Player death: {relevant_death[1]}"
                should_cut = True
        elif first_respawn:
            last_frag_t = max([tf for (tf, _) in frag_events], default=0.0)
            min_post_action = last_frag_t + effective_delay
            r_cut = max(2.5, min(dur, round(max(first_respawn[0] - 0.2, min_post_action), 2)))
            if r_cut < cut_t and r_cut < dur - 0.2:
                cut_t = r_cut
                reason = f"Respawn countdown: {first_respawn[1]}"
                should_cut = True

        if check_chime:
            chime_res = detect_recording_chime(video_path, dur)
            if chime_res:
                t_chime, chime_score, chime_name = chime_res
                chime_cut = max(2.5, round(t_chime - 0.05, 2))
                if chime_cut < cut_t:
                    cut_t = chime_cut
                    reason = f"Recording chime ({chime_name}) at {t_chime:.2f}s"
                    should_cut = True

        return should_cut, cut_t, dur, reason

    except Exception as e:
        print(f"Cut detection warning on {video_path}: {e}")
        return False, 0.0, 0.0, str(e)

# Backward-compatibility alias
detect_clip_death = detect_clip_cut_point

QUAKE_COLOR_MAP = {
    '0': '&H000000&',  # Black
    '1': '&H0000FF&',  # Red
    '2': '&H00FF00&',  # Green
    '3': '&H00FFFF&',  # Yellow
    '4': '&HFF6633&',  # Blue
    '5': '&HFFFF00&',  # Cyan
    '6': '&HFF00FF&',  # Magenta / Pink
    '7': '&HFFFFFF&',  # White
    '8': '&H0080FF&',  # Orange
    '9': '&H7F7F7F&',  # Grey
}

def load_quake_chat_binds() -> dict:
    ql_root = Path.home() / '.local/share/Steam/steamapps/common/Quake Live'
    cfg_paths = sorted(list(ql_root.glob('*/baseq3/repconfig.cfg')) + list(ql_root.glob('*/baseq3/autoexec.cfg')))
    binds = {}
    for p in cfg_paths:
        if p.exists():
            try:
                with open(p, 'r', encoding='latin1', errors='ignore') as f:
                    for line in f:
                        m = re.search(r'bind\s+\S+\s+\"say(?:_team)?\s+(.+?)\"', line, re.I)
                        if m:
                            raw = m.group(1).strip()
                            clean = re.sub(r'\^[0-9a-zA-Z]', '', raw).strip().lower()
                            norm = re.sub(r'[^a-zA-Z0-9]', '', clean)
                            if norm:
                                binds[norm] = raw
            except Exception:
                pass
    return binds

def detect_crop_quake_color(crop) -> str:
    """
    Analyzes pixel HSV values in a cropped text region from Quake Live
    and maps the dominant text color to Quake color code ('0'-'9').
    """
    if crop is None or crop.size == 0:
        return "7"
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    h = hsv[:, :, 0]
    mask = v > 65
    if np.sum(mask) < 5:
        return "7"
    s_vals = s[mask]
    h_vals = h[mask]
    if np.median(s_vals) < 40:
        return "7"
    med_h = np.median(h_vals)
    if med_h <= 12 or med_h >= 165:
        return "1"  # Red
    elif 13 <= med_h <= 34:
        return "3"  # Yellow
    elif 35 <= med_h <= 85:
        return "2"  # Green
    elif 86 <= med_h <= 105:
        return "5"  # Cyan
    elif 106 <= med_h <= 135:
        return "4"  # Blue
    elif 136 <= med_h <= 164:
        return "6"  # Magenta
    return "7"

def enrich_detected_quake_text(raw_ocr_text: str, binds: dict, player_name: str = "Player", roi=None, boxes=None) -> str:
    txt = raw_ocr_text.strip()
    m_player = re.match(r'^' + re.escape(player_name) + r'\s*:\s*(.*)', txt, re.I)
    if m_player:
        p_name = player_name
        msg = m_player.group(2).strip()
        msg_norm = re.sub(r'[^a-zA-Z0-9]', '', msg.lower())
        if binds and msg_norm in binds:
            return f"^7{p_name}: {binds[msg_norm]}"
        if binds:
            for norm_key, raw_bind in binds.items():
                if norm_key in msg_norm or msg_norm in norm_key:
                    return f"^7{p_name}: {raw_bind}"

    m_rw = re.search(r'RED\s*TEAM[A-Za-z]?\s+WINS\s+the\s+round!?(.*)', txt, re.I)
    if m_rw:
        rem = m_rw.group(1).strip()
        rem_str = f" ^7{rem}" if rem else ""
        return f"^1RED TEAM ^3WINS the round!{rem_str}"

    m_bw = re.search(r'BLUE\s*TEAM[A-Za-z]?\s+WINS\s+the\s+round!?(.*)', txt, re.I)
    if m_bw:
        rem = m_bw.group(1).strip()
        rem_str = f" ^7{rem}" if rem else ""
        return f"^4BLUE TEAM ^3WINS the round!{rem_str}"

    if re.search(r'RED\s+TEAM\s+(leads|wins)', txt, re.I):
        return f"^1{txt}"
    if re.search(r'BLUE\s+TEAM\s+(leads|wins)', txt, re.I):
        return f"^4{txt}"
    if re.search(r'(round\s+draw|tie\s+match)', txt, re.I):
        return f"^3{txt}"
    if re.search(r'fight!?', txt, re.I):
        return "^1FIGHT!"
    if re.search(r'round\s+\d+', txt, re.I):
        return f"^3{txt}"

    crop = None
    if roi is not None and boxes:
        all_pts = [p for b in boxes for p in b]
        x_min = max(0, int(min(p[0] for p in all_pts)))
        x_max = min(roi.shape[1], int(max(p[0] for p in all_pts)))
        y_min = max(0, int(min(p[1] for p in all_pts)))
        y_max = min(roi.shape[0], int(max(p[1] for p in all_pts)))
        crop = roi[y_min:y_max, x_min:x_max]

    # Team chat: (Player) (Location): Message
    m_tc = re.match(r'^\(([^)]+)\)\s*\(([^)]+)\)\s*:\s*(.*)', txt)
    if m_tc:
        p_name, loc, msg = m_tc.group(1), m_tc.group(2), m_tc.group(3)
        if crop is not None and crop.shape[1] > 10:
            w_tot = crop.shape[1]
            p_end = int(w_tot * (len(p_name) + 2) / len(txt))
            loc_end = int(w_tot * (len(p_name) + len(loc) + 5) / len(txt))
            c_p = detect_crop_quake_color(crop[:, :p_end])
            c_loc = detect_crop_quake_color(crop[:, p_end:loc_end])
            c_msg = detect_crop_quake_color(crop[:, loc_end:])
        else:
            c_p, c_loc, c_msg = "7", "3", "5"
        c_loc = "3" if c_loc == "7" else c_loc
        c_msg = "5" if c_msg == "7" else c_msg
        return f"^{c_p}({p_name}) ^{c_loc}({loc}): ^{c_msg}{msg}"

    # Public say chat: Sender: Message
    if ":" in txt:
        colon_idx = txt.find(":")
        sender = txt[:colon_idx].strip()
        msg = txt[colon_idx + 1:].strip()
        if crop is not None and crop.shape[1] > 10:
            w_tot = crop.shape[1]
            split_x = int(w_tot * (colon_idx + 1) / len(txt))
            c_sender = detect_crop_quake_color(crop[:, :split_x])
            c_msg = detect_crop_quake_color(crop[:, split_x:])
        else:
            c_sender, c_msg = "7", "2"
        if c_msg == "7":
            c_msg = "2"  # Quake Live default say chat text is green
        return f"^{c_sender}{sender}: ^{c_msg}{msg}"

    c = detect_crop_quake_color(crop) if crop is not None else "7"
    return f"^{c}{txt}"

def format_quake_text_to_ass(raw_text: str, max_line_len: int = 36) -> str:
    """
    Parses Quake caret color codes (^0 to ^9) and produces an ASS formatted dialogue string.
    If longer than max_line_len characters, neatly wraps at word boundaries using \\N.
    Retains active font colors across line breaks, drop shadow, and typography.
    """
    chars_with_colors = []
    current_color = '7'
    i = 0
    while i < len(raw_text):
        if raw_text[i] == '^' and i + 1 < len(raw_text) and raw_text[i+1].isalnum():
            code = raw_text[i+1].lower()
            if code in QUAKE_COLOR_MAP:
                current_color = code
            i += 2
        else:
            chars_with_colors.append((raw_text[i], current_color))
            i += 1

    if not chars_with_colors:
        return ''

    plain_text = ''.join(c for c, _ in chars_with_colors)

    lines_char_slices = []
    if len(plain_text) <= max_line_len:
        lines_char_slices.append((0, len(plain_text)))
    else:
        words = []
        cur_word_start = 0
        for idx, (ch, _) in enumerate(chars_with_colors):
            if ch.isspace():
                if idx > cur_word_start:
                    words.append((cur_word_start, idx))
                cur_word_start = idx + 1
        if cur_word_start < len(chars_with_colors):
            words.append((cur_word_start, len(chars_with_colors)))

        line_start = 0
        last_end = 0
        for w_start, w_end in words:
            if last_end == 0:
                line_start = w_start
                last_end = w_end
            elif (w_end - line_start) <= max_line_len:
                last_end = w_end
            else:
                lines_char_slices.append((line_start, last_end))
                line_start = w_start
                last_end = w_end
        if last_end > line_start:
            lines_char_slices.append((line_start, last_end))

    ass_parts = []
    active_ass_color = None

    for line_idx, (s_idx, e_idx) in enumerate(lines_char_slices):
        if line_idx > 0:
            ass_parts.append(r'\N')
            active_ass_color = None

        for ch, col in chars_with_colors[s_idx:e_idx]:
            col_hex = QUAKE_COLOR_MAP.get(col, '&HFFFFFF&')
            if col_hex != active_ass_color:
                ass_parts.append(f'{{\\c{col_hex}}}')
                active_ass_color = col_hex
            if ch == '{':
                ass_parts.append(r'\{')
            elif ch == '}':
                ass_parts.append(r'\}')
            elif ch == '\\':
                ass_parts.append(r'\\')
            else:
                ass_parts.append(ch)

    return ''.join(ass_parts)

def generate_chat_subtitles(video_path: str, max_duration: float = None, player_name: str = "Player") -> str:
    """
    Scans the lower-left chat and system message region of the video.
    If chat or round announcements are detected (which would otherwise be cropped
    out in a 9:16 vertical YouTube Short), generates a temporary ASS subtitle file
    to display messages with the exact in-game font size, authentic Quake colors,
    and drop shadow, wrapping longer lines to fit the vertical Shorts format.
    Returns path to temporary .ass file, or None if no chat text found.
    """
    try:
        import cv2
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return None

        dur = total_frames / fps
        limit_time = min(dur, max_duration) if max_duration else dur

        step_frames = max(1, int(fps * 0.3))
        detections = []
        binds = load_quake_chat_binds()

        for fno in range(0, total_frames, step_frames):
            t = fno / fps
            if t > limit_time:
                break
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                break
            h, w, _ = frame.shape

            # Left chat zone (lower-left quadrant)
            roi = frame[int(h * 0.70):int(h * 0.96), 0:int(w * 0.35)]
            res, _ = engine(roi)

            # Group items on the same horizontal line
            lines = []
            for box, txt, _ in sorted(res or [], key=lambda item: (min(p[1] for p in item[0]), min(p[0] for p in item[0]))):
                tc = txt.strip()
                if len(tc) < 2:
                    continue
                y_mid = (min(p[1] for p in box) + max(p[1] for p in box)) / 2
                x_min = min(p[0] for p in box)
                merged = False
                for line in lines:
                    if abs(line['y_mid'] - y_mid) < 25:
                        line['items'].append((x_min, tc, box))
                        merged = True
                        break
                if not merged:
                    lines.append({'y_mid': y_mid, 'items': [(x_min, tc, box)]})

            for line in lines:
                sorted_items = sorted(line['items'], key=lambda x: x[0])
                line_txt = ' '.join(t for _, t, _ in sorted_items).strip()
                if len(line_txt) < 3 or line_txt.isdigit() or not re.search(r'[A-Za-z]', line_txt):
                    continue
                # Skip spectator list, warmup, queue status, and chat typing prompt
                if re.match(r'^(spectators|warmup|\[s\]|queue|say\s*team|say\s*teah|say\b|\[\d+\])\b', line_txt, re.I):
                    continue
                boxes = [b for _, _, b in sorted_items]
                enriched = enrich_detected_quake_text(line_txt, binds, player_name, roi=roi, boxes=boxes)
                detections.append((t, enriched))

        cap.release()

        if not detections:
            return None

        # Group detections by unique active messages over time
        active_msgs = {}
        completed_msgs = []

        for t, txt in detections:
            norm_txt = re.sub(r'[^a-zA-Z0-9]', '', txt.lower())
            if not norm_txt:
                continue
            matched_key = None
            for k in active_msgs:
                if norm_txt == k or (len(norm_txt) >= 6 and norm_txt in k) or (len(k) >= 6 and k in norm_txt):
                    matched_key = k
                    break
            if matched_key:
                if t - active_msgs[matched_key]["last"] <= 2.0:
                    active_msgs[matched_key]["last"] = t
                    active_msgs[matched_key]["variants"][txt] += 1
                    continue
                else:
                    completed_msgs.append(active_msgs.pop(matched_key))

            active_msgs[norm_txt] = {"start": t, "last": t, "variants": Counter([txt])}

        for item in active_msgs.values():
            completed_msgs.append(item)

        events = []
        for c in completed_msgs:
            if c["last"] - c["start"] >= 0.2:
                best_txt = c["variants"].most_common(1)[0][0]
                if re.search(r'mapmanager|history\s*\(', best_txt, re.I):
                    continue
                events.append((c["start"], min(limit_time, max(c["start"] + 3.0, c["last"] + 1.5)), best_txt))

        events.sort(key=lambda x: x[0])

        if not events:
            return None

        def format_ass_time(sec: float) -> str:
            h = int(sec // 3600)
            m = int((sec % 3600) // 60)
            s = sec % 60
            return f"{h}:{m:02d}:{s:05.2f}"

        ass_lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1440",
            "PlayResY: 2560",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: Chat, Noto Sans, 84, &H00FFFFFF, &H000000FF, &H00000000, &H80000000, 1, 0, 0, 0, 100, 100, 0, 0, 1, 3, 3, 1, 60, 60, 240, 1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
        ]

        for s_t, e_t, raw_val in events:
            ass_formatted = format_quake_text_to_ass(raw_val, max_line_len=26)
            ass_lines.append(f"Dialogue: 0, {format_ass_time(s_t)}, {format_ass_time(e_t)}, Chat, , 60, 60, 240, , {ass_formatted}")

        ass_path = f"/tmp/shorts_chat_{int(time.time() * 1000)}.ass"
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write("\n".join(ass_lines) + "\n")

        print(f"[Chat Overlay] Generated subtitle overlay for {len(events)} chat event(s).")
        return ass_path

    except Exception as e:
        print(f"Chat subtitle extraction error on {video_path}: {e}")
        return None

QUAKE_PK3_PATH = Path.home() / ".local/share/Steam/steamapps/common/Quake Live/baseq3/pak00.pk3"

def extract_quake_sound_to_temp(rel_path: str, out_name: str = None) -> str:
    """Extracts an audio file directly from Quake Live pak00.pk3 to /tmp."""
    if not QUAKE_PK3_PATH.exists():
        return None
    try:
        import zipfile
        out_file = Path(f"/tmp/{out_name or Path(rel_path).name}")
        if out_file.exists() and out_file.stat().st_size > 100:
            return str(out_file)
        with zipfile.ZipFile(QUAKE_PK3_PATH, "r") as zf:
            if rel_path in zf.namelist():
                out_file.write_bytes(zf.read(rel_path))
                return str(out_file)
    except Exception as e:
        print(f"Quake PK3 sound extraction notice ({rel_path}): {e}")
    return None

def get_action_excitement_metadata(weapon: str = "", feat: str = "", title: str = "") -> dict:
    w = (weapon or "").lower()
    f = (feat or "").lower()
    t = (title or "").lower()

    vo = "sound/vo/holy_shit.ogg"
    emojis = ["😱", "🤯", "💥"]
    banner_main = "INSANE COMBAT PLAY!"
    banner_sub = "QUAKE LIVE HIGHLIGHT"

    if "400" in t or "400" in f or "quad" in t or "quad" in f:
        vo = "sound/vo/holy_shit.ogg"
        emojis = ["😱", "🤯", "💥"]
        banner_main = "400 DAMAGE! (100 x 4)"
        banner_sub = "QUAD-HIT GRENADE"
    elif "midair" in f or "midair" in t or "air rocket" in f or "air rocket" in t:
        vo = "sound/vo/midair1.ogg"
        emojis = ["🚀", "🎯", "💥"]
        banner_main = "MIDAIR PREDICTION!"
        banner_sub = "AIR DIRECT HIT"
    elif "impressive" in f or "impressive" in t or "3 rails" in t or "consecutive rails" in f:
        vo = "sound/vo/impressive1.ogg"
        emojis = ["⚡", "🎯", "💀"]
        banner_main = "IMPRESSIVE ACCURACY!"
        banner_sub = "PINPOINT RAILGUN HIT"
    elif "telefrag" in f or "telefrag" in t or "telefrag" in w:
        vo = "sound/vo/holy_shit.ogg"
        emojis = ["🌀", "💀", "🤯"]
        banner_main = "DISGUSTING TELEFRAG!"
        banner_sub = "WRONG PLACE, WRONG TIME"
    elif "gauntlet" in w or "humiliation" in f or "humiliation" in t:
        vo = "sound/vo/humiliation1.ogg"
        emojis = ["🥊", "💀", "🤣"]
        banner_main = "HUMILIATION!"
        banner_sub = "GAUNTLET MELEE FINISH"
    elif "two frags" in f or "excellent" in f or "excellent" in t:
        vo = "sound/vo/excellent1.ogg"
        emojis = ["💀", "⚡", "🔥"]
        banner_main = "EXCELLENT MEDAL!"
        banner_sub = "TWO FAST FRAGS"
    elif "two direct rockets" in t or "2 direct rockets" in t or "rockets to the face" in t or "two direct rockets" in f or "2 direct rockets" in f or "rockets to the face" in f:
        vo = "sound/vo/holy_shit.ogg"
        emojis = ["🚀", "💥", "💀"]
        banner_main = "2 DIRECT ROCKETS TO THE FACE!"
        banner_sub = "ROCKET LAUNCHER (100 + 100 DMG)"
    elif "plasma" in w or "plasma" in t or "plasma" in f:
        vo = "sound/vo/holy_shit.ogg"
        emojis = ["🟣", "⚡", "💀"]
        banner_main = "MELTED BY PLASMA GUN!"
        banner_sub = "HUNTED DOWN AFTER RUNNING AWAY" if ("run" in t or "run" in f or "hunt" in t) else "PLASMA FINISH"
    elif "rocket" in w or "rocket" in t:
        vo = "sound/vo/holy_shit.ogg"
        emojis = ["🚀", "💥", "🎯"]
        banner_main = "DIRECT ROCKET HIT!"
        banner_sub = "ROCKET LAUNCHER"
    elif "lava" in t or "lava" in f:
        vo = "sound/vo/holy_shit.ogg"
        emojis = ["🌋", "⚡", "💀"]
        banner_main = "KNOCKED INTO LAVA!"
        banner_sub = "ENVIRONMENTAL ELIMINATION"
    elif "clutch" in t or "clutch" in f or "game-ending" in t or "game-ending" in f:
        vo = "sound/vo/holy_shit.ogg"
        emojis = ["🔥", "🏆", "💀"]
        banner_main = "CLUTCH ROUND WIN!"
        banner_sub = "ARENA DUEL VICTORY"
    elif "railgun" in w:
        vo = "sound/vo/impressive1.ogg"
        emojis = ["⚡", "🎯", "💀"]
        banner_main = "CLEAN RAILGUN HIT!"
        banner_sub = "RAILGUN"

    return {
        "vo_path": vo,
        "emojis": emojis,
        "banner_main": banner_main,
        "banner_sub": banner_sub
    }

def render_emoji_image(char: str, size: int = 220) -> Image.Image:
    font_path = "/usr/share/fonts/noto/NotoColorEmoji.ttf"
    if not os.path.exists(font_path):
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype(font_path, 109)
        draw.text((10, 10), char, font=font, embedded_color=True)
        bbox = img.getbbox()
        if bbox:
            cropped = img.crop(bbox)
            return cropped.resize((size, size), Image.Resampling.LANCZOS)
        return img.resize((size, size))
    except Exception as e:
        print(f"Emoji render error ({char}): {e}")
        return None

def detect_action_climax_window(video_path: str, max_duration: float = None) -> tuple:
    try:
        import cv2
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return None
        dur = total_frames / fps
        limit_time = min(dur, max_duration) if max_duration else dur
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        scan_start = max(1.0, min(3.0, limit_time * 0.25))
        scan_end = max(scan_start + 0.5, limit_time - 0.2)
        step = max(1, int(fps * 0.15))

        best_score = 0
        best_t = scan_start + (scan_end - scan_start) * 0.5
        best_cx, best_cy = w // 2, int(h * 0.48)

        for fno in range(int(fps * scan_start), int(fps * scan_end), step):
            t = fno / fps
            cap.set(cv2.CAP_PROP_POS_FRAMES, fno)
            ret, frame = cap.read()
            if not ret:
                break
            roi = frame[int(h*0.20):int(h*0.80), int(w*0.25):int(w*0.80)]
            res, _ = engine(roi)
            score = 0
            boxes = []
            for box, txt, _ in (res or []):
                tc = txt.strip()
                if "100" in tc:
                    score += 50
                    boxes.append(box)
                elif tc.isdigit() and int(tc) in range(15, 120):
                    score += 20
                    boxes.append(box)
                elif re.search(r"fragged\b", tc, re.I):
                    score += 60
                    boxes.append(box)

            # Check top-right roster for enemy elimination confirmation (<Player> DEAD)
            roster_roi = frame[0:int(h * 0.12), int(w * 0.75):w]
            res_r, _ = engine(roster_roi)
            for box, txt, _ in (res_r or []):
                if re.search(r'\bdead\b', txt, re.I):
                    score += 90
            if score > best_score:
                best_score = score
                best_t = t
                if boxes:
                    all_pts = [p for b in boxes for p in b]
                    best_cx = int(min(p[0] for p in all_pts) + max(p[0] for p in all_pts)) // 2 + int(w * 0.25)
                    best_cy = int(min(p[1] for p in all_pts) + max(p[1] for p in all_pts)) // 2 + int(h * 0.20)

        cap.release()
        t_start = max(0.5, best_t - 0.25)
        t_end = min(limit_time - 0.1, best_t + 0.75)
        return t_start, t_end, best_cx, best_cy, best_score
    except Exception as e:
        print(f"Action climax detection notice: {e}")
        return None

def adjust_ass_for_slowmo(ass_path: str, t_start: float, t_end: float, repeat_factor: int) -> str:
    """
    Linearly scales ASS subtitle event timestamps to match slow-motion video expansion.
    """
    if not ass_path or not os.path.exists(ass_path):
        return ass_path
    def time_to_sec(ts):
        parts = ts.strip().split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    def sec_to_time(s):
        s = max(0.0, s)
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        return f"{h}:{m:02d}:{sec:05.2f}"

    def remap_t(t):
        if t <= t_start:
            return t
        elif t <= t_end:
            return t_start + (t - t_start) * repeat_factor
        else:
            return t + (repeat_factor - 1) * (t_end - t_start)

    out_lines = []
    try:
        with open(ass_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("Dialogue:"):
                    parts = line.split(",", 9)
                    if len(parts) >= 10:
                        st = time_to_sec(parts[1])
                        et = time_to_sec(parts[2])
                        parts[1] = sec_to_time(remap_t(st))
                        parts[2] = sec_to_time(remap_t(et))
                        line = ",".join(parts)
                out_lines.append(line)

        adj_path = f"/tmp/adj_{int(time.time()*1000)}_{Path(ass_path).name}"
        with open(adj_path, "w", encoding="utf-8") as f:
            f.writelines(out_lines)
        return adj_path
    except Exception as e:
        print("Subtitle slowmo adjust notice:", e)
        return ass_path

def render_action_slowmo_short(
    video_path: str,
    output_path: str,
    max_duration: float = None,
    subtitles_ass_path: str = None,
    include_hud: bool = True,
    target_lufs: float = -15.0,
    true_peak: float = -1.5,
    weapon: str = "",
    feat: str = "",
    title: str = "",
    slowmo_speed: float = 0.25,
    enable_zoom: bool = True,
    enable_emojis: bool = True,
    enable_audio_sfx: bool = True,
    action_climax_window: tuple = None
) -> bool:
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont

        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return False

        dur = total_frames / fps
        limit_time = min(dur, max_duration) if max_duration else dur
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        climax_data = action_climax_window or detect_action_climax_window(video_path, max_duration=limit_time)
        if not climax_data:
            cap.release()
            return False

        t_start, t_end, cx, cy, score = climax_data
        if t_start >= limit_time - 0.4:
            cap.release()
            return False

        f_start = int(fps * t_start)
        f_end = int(fps * t_end)
        if f_end <= f_start:
            cap.release()
            return False

        repeat_factor = max(2, int(round(1.0 / max(0.1, slowmo_speed))))

        # Crop parameters
        std_w = int(h / 2) # 720
        std_h = int(h * 8 / 9) # 1280
        std_x = (w - std_w) // 2
        std_y = 0

        if enable_zoom:
            zoom_w = int(std_w * 0.75) # 540
            zoom_h = int(zoom_w * 16 / 9) # 960
            zoom_x = max(0, min(w - zoom_w, cx - zoom_w // 2))
            zoom_y = max(0, min(h - zoom_h, cy - zoom_h // 2))
        else:
            zoom_w, zoom_h, zoom_x, zoom_y = std_w, std_h, std_x, std_y

        zoom_cx = zoom_x + zoom_w // 2
        zoom_cy = zoom_y + zoom_h // 2

        out_w, out_h = 1440, 2560
        temp_raw_video = f"/tmp/slowmo_raw_{int(time.time()*1000)}.mp4"
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(temp_raw_video, fourcc, fps, (out_w, out_h))

        meta = get_action_excitement_metadata(weapon, feat, title)
        banner_img = None
        emoji_imgs = []
        if enable_emojis:
            try:
                banner_img = Image.new("RGBA", (1200, 220), (0, 0, 0, 0))
                bdraw = ImageDraw.Draw(banner_img)
                bdraw.rounded_rectangle([(10, 10), (1190, 210)], radius=30, fill=(0, 0, 0, 190), outline=(255, 50, 50, 240), width=6)
                tfont = ImageFont.truetype("/usr/share/fonts/noto/NotoSans-Bold.ttf", 68)
                bdraw.text((600, 75), meta["banner_main"], font=tfont, fill=(255, 230, 40), anchor="mm")
                sfont = ImageFont.truetype("/usr/share/fonts/noto/NotoSans-Bold.ttf", 46)
                bdraw.text((600, 150), meta["banner_sub"], font=sfont, fill=(255, 80, 80), anchor="mm")

                for em in meta["emojis"][:3]:
                    em_img = render_emoji_image(em, size=220)
                    if em_img:
                        emoji_imgs.append(em_img)
            except Exception as e:
                print(f"Banner/emoji render notice: {e}")

        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        frame_idx = 0
        last_action_frame = None
        f_limit = int(fps * limit_time)
        has_aftermath = (f_end < f_limit - int(fps * 0.3))

        while True:
            ret, frame = cap.read()
            if not ret or frame_idx >= f_limit:
                break

            if frame_idx < f_start:
                # 1. Buildup at normal 1.0x speed with HUD
                cropped = frame[std_y:std_y + std_h, std_x:std_x + std_w]
                scaled = cv2.resize(cropped, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)

                if include_hud:
                    top_crop = frame[int(10*h/1440):int(140*h/1440), 0:int(1000*w/2560)]
                    top_scaled = cv2.resize(top_crop, (1200, int(130 * 1200 / 1000)))
                    th, tw, _ = top_scaled.shape
                    scaled[60:60+th, (out_w-tw)//2:(out_w-tw)//2+tw] = top_scaled

                    bot_crop = frame[int(1290*h/1440):int(1435*h/1440), int(700*w/2560):int(1860*w/2560)]
                    bot_scaled = cv2.resize(bot_crop, (1400, int(145 * 1400 / 1160)))
                    bh, bw, _ = bot_scaled.shape
                    scaled[out_h-bh-30:out_h-30, (out_w-bw)//2:(out_w-bw)//2+bw] = bot_scaled

                writer.write(scaled)

            elif frame_idx <= f_end:
                # 2. Action climax slow-mo zoom with emojis & highlight banner
                progress = (frame_idx - f_start) / float(max(1, f_end - f_start))
                cur_w = int(std_w + (zoom_w - std_w) * min(1.0, progress * 3.5))
                cur_h = int(cur_w * 16 / 9)
                cur_cx = int(std_x + std_w//2 + (zoom_cx - (std_x + std_w//2)) * min(1.0, progress * 3.5))
                cur_cy = int(std_y + std_h//2 + (zoom_cy - (std_y + std_h//2)) * min(1.0, progress * 3.5))

                x1 = max(0, min(w - cur_w, cur_cx - cur_w // 2))
                y1 = max(0, min(h - cur_h, cur_cy - cur_h // 2))
                cropped = frame[y1:y1 + cur_h, x1:x1 + cur_w]
                scaled = cv2.resize(cropped, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)

                if enable_emojis and banner_img:
                    pil_frame = Image.fromarray(cv2.cvtColor(scaled, cv2.COLOR_BGR2RGB))
                    pil_frame.paste(banner_img, ((out_w - 1200) // 2, 200), banner_img)
                    offset_y = int(np.sin(progress * np.pi * 3) * 25)
                    if len(emoji_imgs) >= 1:
                        pil_frame.paste(emoji_imgs[0], (80, 1100 + offset_y), emoji_imgs[0])
                    if len(emoji_imgs) >= 2:
                        pil_frame.paste(emoji_imgs[1], (out_w - 80 - 220, 1100 - offset_y), emoji_imgs[1])
                    if len(emoji_imgs) >= 3:
                        pil_frame.paste(emoji_imgs[2], ((out_w - 220) // 2, 1920), emoji_imgs[2])
                    scaled = cv2.cvtColor(np.array(pil_frame), cv2.COLOR_RGB2BGR)

                last_action_frame = scaled
                for _ in range(repeat_factor):
                    writer.write(scaled)

            else:
                # 3. Aftermath at 1.0x normal speed showing round conclusion & chat reactions
                cropped = frame[std_y:std_y + std_h, std_x:std_x + std_w]
                scaled = cv2.resize(cropped, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)

                if include_hud:
                    top_crop = frame[int(10*h/1440):int(140*h/1440), 0:int(1000*w/2560)]
                    top_scaled = cv2.resize(top_crop, (1200, int(130 * 1200 / 1000)))
                    th, tw, _ = top_scaled.shape
                    scaled[60:60+th, (out_w-tw)//2:(out_w-tw)//2+tw] = top_scaled

                    bot_crop = frame[int(1290*h/1440):int(1435*h/1440), int(700*w/2560):int(1860*w/2560)]
                    bot_scaled = cv2.resize(bot_crop, (1400, int(145 * 1400 / 1160)))
                    bh, bw, _ = bot_scaled.shape
                    scaled[out_h-bh-30:out_h-30, (out_w-bw)//2:(out_w-bw)//2+bw] = bot_scaled

                writer.write(scaled)

            frame_idx += 1

        if not has_aftermath and last_action_frame is not None:
            for i in range(36):
                fade = 1.0 - (i / 36.0) * 0.35
                writer.write((last_action_frame * fade).astype(np.uint8))

        cap.release()
        writer.release()

        temp_normal_a = f"/tmp/slowmo_norm_a_{int(time.time()*1000)}.wav"
        temp_slow_a = f"/tmp/slowmo_slow_a_{int(time.time()*1000)}.wav"
        temp_after_a = f"/tmp/slowmo_after_a_{int(time.time()*1000)}.wav"
        temp_mixed_a = f"/tmp/slowmo_mixed_a_{int(time.time()*1000)}.wav"

        subprocess.run([
            "ffmpeg", "-y", "-ss", "0.0", "-to", str(t_start),
            "-i", video_path, "-vn", "-c:a", "pcm_s16le", temp_normal_a
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        subprocess.run([
            "ffmpeg", "-y", "-ss", str(t_start), "-to", str(t_end),
            "-i", video_path, "-vn",
            "-af", "atempo=0.5,atempo=0.5,lowpass=f=3500",
            "-c:a", "pcm_s16le", temp_slow_a
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        inputs = ["-i", temp_normal_a, "-i", temp_slow_a]
        n_concat = 2

        if has_aftermath:
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(t_end), "-to", str(limit_time),
                "-i", video_path, "-vn", "-c:a", "pcm_s16le", temp_after_a
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            inputs.extend(["-i", temp_after_a])
            n_concat = 3

        concat_inputs_str = "".join([f"[{i}:a]" for i in range(n_concat)])
        filter_parts = [f"{concat_inputs_str}concat=n={n_concat}:v=0:a=1[game_full]"]
        in_count = n_concat

        impact_sfx = extract_quake_sound_to_temp("sound/feedback/impact6.ogg", "quake_impact6.ogg")
        announcer_vo = extract_quake_sound_to_temp(meta["vo_path"], f"quake_vo_{Path(meta['vo_path']).name}")

        s_ms = int(t_start * 1000)
        vo_ms = s_ms + 180

        if enable_audio_sfx and impact_sfx and os.path.exists(impact_sfx):
            inputs.extend(["-i", impact_sfx])
            filter_parts.append(f"[{in_count}:a]adelay={s_ms}|{s_ms},volume=1.5[sfx_impact]")
            in_count += 1

        if enable_audio_sfx and announcer_vo and os.path.exists(announcer_vo):
            inputs.extend(["-i", announcer_vo])
            filter_parts.append(f"[{in_count}:a]adelay={vo_ms}|{vo_ms},volume=2.2[sfx_vo]")
            in_count += 1

        mix_inputs = "[game_full]"
        if in_count == n_concat + 1:
            mix_inputs += "[sfx_impact]"
        elif in_count == n_concat + 2:
            mix_inputs += "[sfx_impact][sfx_vo]"
        num_mix = 1 + (1 if (enable_audio_sfx and impact_sfx and os.path.exists(impact_sfx)) else 0) + (1 if (enable_audio_sfx and announcer_vo and os.path.exists(announcer_vo)) else 0)
        filter_parts.append(f"{mix_inputs}amix=inputs={num_mix}:duration=first:dropout_transition=2[mixed]")

        aftermath_dur = (limit_time - t_end) if has_aftermath else (36.0 / fps)
        total_rendered_dur = t_start + (t_end - t_start) * repeat_factor + aftermath_dur
        fade_start = max(1.0, total_rendered_dur - 0.4)
        filter_parts.append(f"[mixed]afade=t=out:st={fade_start:.2f}:d=0.4,loudnorm=I={target_lufs}:LRA=7:TP={true_peak}[outa]")

        cmd_mix = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", ";".join(filter_parts),
            "-map", "[outa]",
            "-c:a", "pcm_s16le", temp_mixed_a
        ]
        subprocess.run(cmd_mix, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        adjusted_ass = None
        if subtitles_ass_path and os.path.exists(subtitles_ass_path):
            adjusted_ass = adjust_ass_for_slowmo(subtitles_ass_path, t_start, t_end, repeat_factor)

        vf_args = []
        if adjusted_ass and os.path.exists(adjusted_ass):
            vf_args = ["-vf", f"ass='{adjusted_ass}'"]

        cmd_nvenc = [
            "ffmpeg", "-y",
            "-i", temp_raw_video,
            "-i", temp_mixed_a,
        ] + vf_args + [
            "-c:v", "h264_nvenc",
            "-preset", "p5",
            "-tune", "hq",
            "-cq", "12",
            "-b:v", "45M",
            "-maxrate", "60M",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "256k",
            "-shortest",
            output_path
        ]
        res = subprocess.run(cmd_nvenc, capture_output=True, text=True)

        for tmp_f in [temp_raw_video, temp_normal_a, temp_slow_a, temp_after_a, temp_mixed_a, adjusted_ass]:
            if tmp_f and os.path.exists(tmp_f):
                try:
                    os.remove(tmp_f)
                except Exception:
                    pass

        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            print(f"[Action Slow-Mo] Rendered enhanced short ({t_start:.2f}s -> {t_end:.2f}s zoomed {repeat_factor}x slow-mo with Quake VO: {Path(meta['vo_path']).stem}).")
            return True

        return False

    except Exception as e:
        print(f"Action slow-mo render notice on {video_path}: {e}")
        return False

def convert_to_shorts_format(video_path: str, output_path: str = None, style: str = "crop", max_duration: float = None, subtitles_ass_path: str = None, include_hud: bool = True, target_lufs: float = -15.0, true_peak: float = -1.5, weapon: str = "", feat: str = "", custom_title: str = "", enable_action_slowmo: bool = True, slowmo_speed: float = 0.25, enable_zoom: bool = True, enable_emojis: bool = True, enable_audio_sfx: bool = True, action_climax_window: tuple = None) -> str:
    """
    Converts 16:9 gameplay footage into 9:16 vertical YouTube Shorts format.
    Style B ('crop'):
      - 1440x2560 (2K Vertical @ 60FPS) master encode.
      - Automatically detects action climax, adds dynamic zoom, deep slow-motion,
        fitting surprise/weapon emojis, and iconic Quake announcer excitement audio.
      - Lanczos upscaling with unsharp-mask edge sharpening.
      - Encoded via NVENC Preset P7 High Quality at 45-60 Mbps.
      - Overlays full Health/Armor HUD at bottom & live Red/Blue scores up top (when include_hud=True).
      - Forces YouTube to allocate premium high-bitrate VP09/AV01 1440p codecs.
    Trims before respawn countdown or player death if max_duration is specified.
    Displays left-side chat/announcement text at bottom if subtitles_ass_path is specified.
    """
    if not output_path:
        p = Path(video_path)
        output_path = f"/tmp/{p.stem}_shorts_{int(time.time())}.mp4"

    # Action slow-mo & excitement processing
    if style == "crop" and enable_action_slowmo:
        success = render_action_slowmo_short(
            video_path=video_path,
            output_path=output_path,
            max_duration=max_duration,
            subtitles_ass_path=subtitles_ass_path,
            include_hud=include_hud,
            target_lufs=target_lufs,
            true_peak=true_peak,
            weapon=weapon,
            feat=feat,
            title=custom_title,
            slowmo_speed=slowmo_speed,
            enable_zoom=enable_zoom,
            enable_emojis=enable_emojis,
            enable_audio_sfx=enable_audio_sfx,
            action_climax_window=action_climax_window
        )
        if success and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return output_path

    trim_args = ["-t", f"{max_duration:.3f}"] if (max_duration is not None and max_duration > 0) else []
    # EBU R128 loudness normalization targeting -15 LUFS (comfortable, non-fatiguing volume)
    # with True Peak limit of -1.5 dBFS to ensure consistent clean audio without distortion
    af_norm = f"loudnorm=I={target_lufs}:LRA=7:TP={true_peak}"
    if trim_args and max_duration > 0.5:
        af_fade = f"afade=t=out:st={max_duration - 0.12:.3f}:d=0.12"
        audio_args = ["-af", f"{af_norm},{af_fade}", "-c:a", "aac", "-b:a", "256k"]
    else:
        audio_args = ["-af", af_norm, "-c:a", "aac", "-b:a", "256k"]
    ass_filter = f",ass='{subtitles_ass_path}'" if subtitles_ass_path and os.path.exists(subtitles_ass_path) else ""

    if style == "crop":
        if include_hud:
            filter_complex = (
                "[0:v]crop=ih/2:ih*8/9:(iw-ow)/2:0,scale=1440:2560:flags=bicubic,setsar=1[base];"
                "[0:v]crop=1000*iw/2560:130*ih/1440:0:10*ih/1440,scale=1200:-1,format=rgba,lumakey=threshold=0.20:tolerance=0.15:softness=0.1,setsar=1[top_scores];"
                "[0:v]crop=1160*iw/2560:145*ih/1440:700*iw/2560:1290*ih/1440,scale=1400:-1,setsar=1[bottom_hud];"
                "[base][top_scores]overlay=(W-w)/2:60[v1];"
                f"[v1][bottom_hud]overlay=(W-w)/2:H-h-25{ass_filter}"
            )
            cmd_nvenc = [
                "ffmpeg", "-y",
                "-threads", "4",
                "-i", video_path,
            ] + trim_args + [
                "-filter_complex", filter_complex,
                "-c:v", "h264_nvenc",
                "-preset", "p5",
                "-tune", "hq",
                "-cq", "12",
                "-b:v", "45M",
                "-maxrate", "60M",
            ] + audio_args + [
                output_path
            ]
            cmd_cpu = [
                "ffmpeg", "-y",
                "-threads", "4",
                "-i", video_path,
            ] + trim_args + [
                "-filter_complex", filter_complex,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "16",
            ] + audio_args + [
                output_path
            ]
        else:
            vf = f"crop=ih*9/16:ih:(iw-ow)/2:0,scale=1440:2560:flags=bicubic,setsar=1{ass_filter}"
            cmd_nvenc = [
                "ffmpeg", "-y",
                "-threads", "4",
                "-i", video_path,
            ] + trim_args + [
                "-vf", vf,
                "-c:v", "h264_nvenc",
                "-preset", "p5",
                "-tune", "hq",
                "-cq", "12",
                "-b:v", "45M",
                "-maxrate", "60M",
            ] + audio_args + [
                output_path
            ]
            cmd_cpu = [
                "ffmpeg", "-y",
                "-threads", "4",
                "-i", video_path,
            ] + trim_args + [
                "-vf", vf,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "16",
            ] + audio_args + [
                output_path
            ]
    else:
        filter_complex = (
            "[0:v]scale=180:320:force_original_aspect_ratio=increase,"
            "crop=180:320,avgblur=8,scale=1080:1920:flags=bicubic,setsar=1[bg];"
            "[0:v]scale=1080:608,setsar=1[fg];"
            f"[bg][fg]overlay=0:656{ass_filter}"
        )
        cmd_nvenc = [
            "ffmpeg", "-y",
            "-threads", "4",
            "-i", video_path,
        ] + trim_args + [
            "-filter_complex", filter_complex,
            "-c:v", "h264_nvenc",
            "-preset", "p5",
            "-tune", "hq",
            "-cq", "14",
        ] + audio_args + [
            output_path
        ]
        cmd_cpu = [
            "ffmpeg", "-y",
            "-i", video_path,
        ] + trim_args + [
            "-filter_complex", filter_complex,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
        ] + audio_args + [
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
    rep_cfgs = sorted((Path.home() / ".local/share/Steam/steamapps/common/Quake Live").glob("*/baseq3/repconfig.cfg"))
    for rep_cfg in rep_cfgs[:1]:
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
            if p_name and p_name not in players:
                players.append(p_name)
        info['players'] = players
    except Exception as e:
        print("A2S server query notice:", e)

    return info

def generate_metadata(video_path: str, config: dict, enemy: str = "", weapon: str = "", special_play: str = "", custom_title: str = "", is_shorts: bool = False, server_info: dict = None, player: str = None, custom_desc: str = ""):
    player = player or config.get("player_name", "Player")
    dt_str = datetime.now().strftime("%B %d, %Y")

    if server_info is None:
        try:
            server_info = get_quake_server_info()
        except Exception:
            server_info = {}

    has_multiple_enemies = " & " in enemy

    if custom_title:
        title = custom_title
    elif special_play and special_play in TEMPLATES_SPECIAL_FEATS:
        title = random.choice(TEMPLATES_SPECIAL_FEATS[special_play]).format(
            player=player, enemy=enemy or "OPPONENTS", weapon=(weapon or "RAILGUN").upper()
        )
    elif has_multiple_enemies:
        title = random.choice(TEMPLATES_TWO_FRAGS_GENERIC).format(
            player=player, enemy=enemy, weapon=(weapon or "RAILGUN").upper()
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

    # Protect player and enemy names from being cut off if title exceeds 100 characters
    max_len = 92 if (is_shorts and "#Shorts" not in title and "#shorts" not in title) else 100
    if len(title) > max_len:
        # If names are at the end ('player vs enemy' or 'player'), preserve them fully
        names_suffix = f"{player} vs {enemy}" if enemy else player
        if title.endswith(names_suffix):
            allowed_prefix_len = max_len - len(names_suffix) - 1
            if allowed_prefix_len > 10:
                prefix = title[:-len(names_suffix)].rstrip(" :!-–")
                title = f"{prefix[:allowed_prefix_len].rstrip()} {names_suffix}"
            else:
                title = title[:max_len]
        else:
            title = title[:max_len]

    if is_shorts and "#Shorts" not in title and "#shorts" not in title:
        title = f"{title} #Shorts"

    if custom_desc:
        description = custom_desc
    else:
        frag_details = []
        if special_play and special_play != "Auto / Standard Frag":
            frag_details.append(f"🔥 Highlight Play: {special_play}")
        if enemy:
            target_label = "🎯 Targets Eliminated" if has_multiple_enemies else "🎯 Target Eliminated"
            frag_details.append(f"{target_label}: {enemy}")
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

    enemy_tags = []
    if enemy:
        enemy_tags.append(enemy)
        if " & " in enemy:
            for en in enemy.split(" & "):
                if en.strip():
                    enemy_tags.append(en.strip())
            enemy_tags.extend(["Two Frags", "Double Kill"])

    player_tags = [player]
    if "(" in player and ")" in player:
        cleaned_pts = player.replace("(", " ").replace(")", " ").split()
        player_tags.extend(cleaned_pts)

    extra_tags = ["Shorts", "YouTube Shorts", "#Shorts"] if is_shorts else []
    server_tags = []
    if server_info.get("server_name"):
        server_tags.append(server_info["server_name"])
    if server_info.get("map_name"):
        server_tags.append(server_info["map_name"])

    tags = list(set(config.get("tags", []) + player_tags + [
        "Quake Live", "Quake", "Arena FPS", "Fragmovie", "Gameplay", "Clutch"
    ] + enemy_tags + ([weapon] if weapon else []) + ([special_play] if special_play else []) + extra_tags + server_tags))

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
        "--title=Export to YouTube",
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

def _read_json_file(path: Path, default):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _write_json_atomic(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


@contextmanager
def _queue_lock():
    QUEUE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_LOCK_FILE, "w", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def _log_queue(msg: str):
    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts_str}] {msg}"
    print(line)
    try:
        QUEUE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(QUEUE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _record_successful_upload(video_id: str, title: str):
    with _queue_lock():
        history = _read_json_file(UPLOAD_HISTORY_FILE, [])
        if not isinstance(history, list):
            history = []
        now_ts = time.time()
        history.append({
            "ts": now_ts,
            "iso": datetime.fromtimestamp(now_ts).astimezone().isoformat(),
            "video_id": video_id,
            "title": title,
        })
        _write_json_atomic(UPLOAD_HISTORY_FILE, history[-100:])


def _next_pt_quota_reset_ts(now_ts: float = None) -> float:
    now_ts = now_ts or time.time()
    pt_now = datetime.fromtimestamp(now_ts, PT_TZ)
    next_mid = datetime(pt_now.year, pt_now.month, pt_now.day, 0, 3, tzinfo=PT_TZ) + timedelta(days=1)
    return next_mid.timestamp()


def _compute_next_retry_at(reason: str, state: dict, now_ts: float = None) -> tuple:
    now_ts = now_ts or time.time()
    pt_day = datetime.fromtimestamp(now_ts, PT_TZ).strftime("%Y-%m-%d")
    prev_day = state.get("limit_hits_pt_day")
    hits_today = int(state.get("limit_hits_today", 0)) if prev_day == pt_day else 0

    if reason == "quotaExceeded":
        return _next_pt_quota_reset_ts(now_ts), pt_day, hits_today + 1

    if reason == "uploadLimitExceeded":
        hits_today += 1
        history = _read_json_file(UPLOAD_HISTORY_FILE, [])
        cutoff_24h = now_ts - 86400
        recent = sorted(
            float(item["ts"]) for item in history
            if isinstance(item, dict) and item.get("ts") and float(item["ts"]) >= cutoff_24h
        )
        candidate_slot_ts = None
        if len(recent) >= 6:
            slot_ts = recent[0] + 86400 + 180
            if (now_ts + 900) <= slot_ts <= (now_ts + 14400):
                candidate_slot_ts = slot_ts

        # Space out probes (2h -> 3h -> 4h) so repeated checks don't exhaust the 10k API unit quota
        backoff_sec = 7200 if hits_today == 1 else (10800 if hits_today == 2 else 14400)
        return (candidate_slot_ts or (now_ts + backoff_sec)), pt_day, hits_today

    return now_ts + 900, pt_day, hits_today


def _read_queue_state() -> dict:
    with _queue_lock():
        state = _read_json_file(QUEUE_STATE_FILE, {})
        return state if isinstance(state, dict) else {}


def _is_limit_cooldown_active(state: dict, now_ts: float = None) -> bool:
    now_ts = now_ts or time.time()
    return (
        state.get("limit_reason") in ("uploadLimitExceeded", "quotaExceeded")
        and float(state.get("next_retry_at", 0) or 0) > now_ts
    )


def _record_limit_state(reason: str, now_ts: float = None) -> dict:
    now_ts = now_ts or time.time()
    with _queue_lock():
        state = _read_json_file(QUEUE_STATE_FILE, {})
        if not isinstance(state, dict):
            state = {}
        next_ts, pt_day, hits_today = _compute_next_retry_at(reason, state, now_ts=now_ts)
        state.update({
            "limit_reason": reason,
            "limit_hit_at": now_ts,
            "limit_hit_iso": datetime.fromtimestamp(now_ts).astimezone().isoformat(),
            "next_retry_at": next_ts,
            "next_retry_iso": datetime.fromtimestamp(next_ts).astimezone().isoformat(),
            "limit_hits_pt_day": pt_day,
            "limit_hits_today": hits_today,
        })
        _write_json_atomic(QUEUE_STATE_FILE, state)
        return state


def _clear_limit_state():
    with _queue_lock():
        state = _read_json_file(QUEUE_STATE_FILE, {})
        if isinstance(state, dict) and any(k in state for k in ("limit_reason", "next_retry_at", "limit_hit_at")):
            for k in ("limit_reason", "limit_hit_at", "limit_hit_iso", "next_retry_at", "next_retry_iso"):
                state.pop(k, None)
            _write_json_atomic(QUEUE_STATE_FILE, state)


def _format_retry_time(ts: float) -> str:
    if not ts:
        return "next scheduled check"
    return datetime.fromtimestamp(ts).astimezone().strftime("%b %d %I:%M %p")


def _get_pending_job_dirs() -> list:
    if not QUEUE_DIR.exists():
        return []
    jobs = []
    for d in QUEUE_DIR.iterdir():
        if not d.is_dir() or d.name.startswith("_") or d.name.startswith("."):
            continue
        jf = d / "job.json"
        if jf.is_file():
            data = _read_json_file(jf, None)
            if isinstance(data, dict) and data.get("pending_parts"):
                jobs.append((float(data.get("created_at", 0.0)), d, data))
    jobs.sort(key=lambda item: item[0])
    return jobs


def _archive_uploaded_sources(
    source_video_path: str,
    queued_source_path: str = None,
    shorts_archive_dir: str = None,
    final_uploaded_dir: str = None,
):
    """
    Moves the uploaded source clip into 'uploaded/' and any companion *_Short.mp4
    files into sibling '../shorts/'. Works whether source starts in 'exported/' or 'exported/queued/'.
    """
    dest_moved_path = None
    try:
        candidate = None
        for raw in (queued_source_path, source_video_path):
            if raw:
                p = Path(raw).resolve()
                if p.is_file():
                    candidate = p
                    break
        if not candidate:
            return None

        base_export_dir = candidate.parent.parent if candidate.parent.name in ("queued", "uploaded") else candidate.parent
        uploaded_dir = Path(final_uploaded_dir) if final_uploaded_dir else (base_export_dir / "uploaded")
        shorts_dir = Path(shorts_archive_dir) if shorts_archive_dir else (base_export_dir.parent / "shorts")

        if candidate.name.lower().endswith("_short.mp4"):
            shorts_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(candidate), str(shorts_dir / candidate.name))
            print(f"Moved Short file out of exported: {shorts_dir / candidate.name}")
            base_stem = re.sub(r'(_telefrag)?_short$', '', candidate.stem, flags=re.I)
            for search_dir in (base_export_dir, base_export_dir / "queued"):
                base_orig = search_dir / f"{base_stem}.mp4"
                if base_orig.is_file():
                    uploaded_dir.mkdir(parents=True, exist_ok=True)
                    dest_target = uploaded_dir / base_orig.name
                    shutil.move(str(base_orig), str(dest_target))
                    dest_moved_path = dest_target
                    print(f"Moved original video to subfolder: {dest_target}")
                    break
        elif candidate.parent != uploaded_dir:
            uploaded_dir.mkdir(parents=True, exist_ok=True)
            dest_target = uploaded_dir / candidate.name
            shutil.move(str(candidate), str(dest_target))
            dest_moved_path = dest_target
            print(f"Moved uploaded video to subfolder: {dest_target}")
            for search_dir in (base_export_dir, base_export_dir / "queued"):
                for comp in search_dir.glob(f"{candidate.stem}*_Short.mp4"):
                    if comp.is_file():
                        shorts_dir.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(comp), str(shorts_dir / comp.name))
                        print(f"Moved companion Short out of exported: {shorts_dir / comp.name}")
    except Exception as move_err:
        print(f"Notice: Could not move uploaded video: {move_err}")
    return dest_moved_path


def _notify_upload_success(uploaded_links: list, dest_moved_path, privacy: str, from_queue: bool = False):
    if not uploaded_links:
        return
    primary_link = uploaded_links[0]
    try:
        subprocess.run(["wl-copy", "\n".join(uploaded_links)], check=True, timeout=2)
    except Exception:
        pass
    try:
        subprocess.Popen(["pw-play", "/usr/share/sounds/freedesktop/stereo/complete.oga"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    prefix = "Queued " if from_queue else ""
    subfolder_note = f"\nMoved clip to: {dest_moved_path.parent.name}/" if dest_moved_path else ""
    vis = str(privacy or "public").upper()
    if len(uploaded_links) > 1:
        notify(f"✅ {prefix}Dual YouTube Upload Complete! ({vis})", f"📱 Short: {uploaded_links[0]}\n🖥️ 1440p: {uploaded_links[1]}{subfolder_note}", icon="dialog-ok", expire_ms=8000)
    else:
        notify(f"✅ {prefix}YouTube Upload Complete! ({vis})", f"Link copied: {primary_link}{subfolder_note}", icon="dialog-ok", expire_ms=7000)


def _enqueue_upload_job(
    original_video_path: str,
    pending_specs: list,
    uploaded_links: list,
    reason: str,
    temp_trimmed_source: str = None,
) -> tuple:
    """
    Persists pre-rendered Short/Widescreen media, thumbnail, and metadata under
    ~/.config/youtube-upload/queue/ and stages the source clip in exported/queued/.
    """
    orig_path = Path(original_video_path).resolve()
    orig_parent_name = orig_path.parent.name
    base_export_dir = orig_path.parent.parent if orig_parent_name in ("queued", "uploaded") else orig_path.parent
    final_uploaded_dir = base_export_dir / "uploaded"
    shorts_archive_dir = base_export_dir.parent / "shorts"
    safe_stem = re.sub(r'[^\w\-]', '_', orig_path.stem)[:48] or "clip"
    job_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{safe_stem}"
    job_dir = QUEUE_DIR / job_id

    with _queue_lock():
        job_dir.mkdir(parents=True, exist_ok=True)
        queued_source = None
        if orig_path.is_file():
            if orig_parent_name == "queued":
                queued_source = orig_path
            elif orig_parent_name != "uploaded":
                queued_dir = orig_path.parent / "queued"
                queued_dir.mkdir(parents=True, exist_ok=True)
                queued_source = queued_dir / orig_path.name
                shutil.move(str(orig_path), str(queued_source))
                print(f"[Queue] Moved source clip -> {queued_source}")

        moved_files = {}
        persisted_parts = []
        for idx, spec in enumerate(pending_specs):
            part = dict(spec)
            src_media = part.get("file_path")
            if src_media:
                src_p = Path(src_media)
                if queued_source and not part.get("delete_media_on_done") and src_p.resolve() == orig_path:
                    part["file_path"] = str(queued_source)
                elif part.get("delete_media_on_done") and src_p.is_file():
                    if src_media in moved_files:
                        part["file_path"] = moved_files[src_media]
                    else:
                        ext = src_p.suffix or ".mp4"
                        dst_media = job_dir / f"part{idx}_{part.get('kind', 'video')}{ext}"
                        shutil.move(str(src_p), str(dst_media))
                        moved_files[src_media] = str(dst_media)
                        part["file_path"] = str(dst_media)

            src_thumb = part.get("thumb_path")
            if src_thumb and os.path.isfile(src_thumb):
                if src_thumb in moved_files:
                    part["thumb_path"] = moved_files[src_thumb]
                else:
                    dst_thumb = job_dir / f"part{idx}_{part.get('kind', 'video')}_thumb.jpg"
                    shutil.move(src_thumb, str(dst_thumb))
                    moved_files[src_thumb] = str(dst_thumb)
                    part["thumb_path"] = str(dst_thumb)

            persisted_parts.append(part)

        if temp_trimmed_source and temp_trimmed_source not in moved_files and os.path.exists(temp_trimmed_source):
            try:
                os.remove(temp_trimmed_source)
            except OSError:
                pass

        now_ts = time.time()
        job_data = {
            "id": job_id,
            "created_at": now_ts,
            "created_iso": datetime.fromtimestamp(now_ts).astimezone().isoformat(),
            "reason": reason,
            "attempts": 1,
            "source_video_path": str(orig_path),
            "queued_source_path": str(queued_source) if queued_source else None,
            "final_uploaded_dir": str(final_uploaded_dir),
            "shorts_archive_dir": str(shorts_archive_dir),
            "uploaded_links": list(uploaded_links),
            "pending_parts": persisted_parts,
        }
        _write_json_atomic(job_dir / "job.json", job_data)

        # Deduplicate older pending jobs pointing to the same clip & destination
        for _, old_dir, old_data in _get_pending_job_dirs():
            if old_dir == job_dir:
                continue
            if (
                old_data.get("final_uploaded_dir") == str(final_uploaded_dir)
                and Path(old_data.get("source_video_path", "")).name == orig_path.name
            ):
                shutil.rmtree(old_dir, ignore_errors=True)

        total_pending = len(_get_pending_job_dirs())

    _log_queue(f"Enqueued {orig_path.name} ({len(persisted_parts)} part(s), reason={reason}). Queue size: {total_pending}")
    return job_dir, total_pending


def _is_transient_auth_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in (
        "name resolution", "temporary failure", "timed out", "connectionerror",
        "max retries exceeded", "connection reset", "network is unreachable",
        "502", "503", "504",
    ))


def _authenticate_youtube(headless: bool = False):
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
                if headless and _is_transient_auth_error(e):
                    return None, "transient"
                creds = None

        if not creds:
            if headless:
                return None, "auth_required"
            if not CLIENT_SECRETS_FILE.exists():
                return None, "missing_secrets"

            notify("Authentication Required", "Please approve access in your browser for YouTube.", expire_ms=5000)
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
            notify("Authentication Successful!", "Saved credentials for YouTube.", expire_ms=3000)

    return build("youtube", "v3", credentials=creds), None


def get_authenticated_service(headless: bool = False):
    service, _ = _authenticate_youtube(headless=headless)
    return service


def _classify_upload_exception(exc: Exception):
    text_parts = [str(exc)]
    content = getattr(exc, "content", None)
    if isinstance(content, (bytes, bytearray)):
        text_parts.append(content.decode("utf-8", errors="ignore"))
    elif content is not None:
        text_parts.append(str(content))
    details = getattr(exc, "error_details", None)
    if details:
        text_parts.append(str(details))
    msg_lower = " ".join(text_parts).lower()

    if "uploadlimitexceeded" in msg_lower:
        return "uploadLimitExceeded"
    if "quotaexceeded" in msg_lower or "dailylimitexceeded" in msg_lower:
        return "quotaExceeded"

    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in (429, 500, 502, 503, 504):
        return "transientError"
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)) or any(
        k in msg_lower for k in (
            "timed out", "temporary failure in name resolution", "name resolution",
            "connection reset", "connection aborted", "broken pipe", "ssl", "eof occurred",
        )
    ):
        return "transientError"
    return None


def _execute_video_upload_with_status(
    youtube,
    file_path: str,
    title: str,
    description: str,
    tags: list,
    category_id: str,
    privacy: str,
    thumb_path: str = None,
) -> tuple:
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

    media = MediaFileUpload(file_path, chunksize=10 * 1024 * 1024, resumable=True)
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
        err_kind = _classify_upload_exception(e)
        if err_kind:
            print(f"[Upload Deferred] {err_kind}: {e}")
            return None, None, err_kind
        raise

    if not response or "id" not in response:
        return None, None, "transientError"

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
                try:
                    os.remove(thumb_path)
                except OSError:
                    pass

    return video_id, video_url, None


def _execute_video_upload(youtube, file_path: str, title: str, description: str, tags: list, category_id: str, privacy: str, thumb_path: str = None) -> tuple:
    vid_id, vid_url, err_kind = _execute_video_upload_with_status(
        youtube, file_path, title, description, tags, category_id, privacy, thumb_path
    )
    if err_kind == "uploadLimitExceeded":
        notify("⚠️ YouTube Daily Upload Limit Reached", "YouTube limits standard channels to 10 uploads/day.\nYour quota will reset in ~24 hours.", icon="dialog-warning", expire_ms=8000)
    elif err_kind == "quotaExceeded":
        notify("⚠️ YouTube API Quota Reached", "YouTube API daily quota exceeded.\nWill retry after midnight PT reset.", icon="dialog-warning", expire_ms=8000)
    return vid_id, vid_url


def _cleanup_temp_files(paths: list):
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def process_upload_queue(force: bool = False) -> int:
    """
    Processes queued YouTube uploads FIFO once any daily upload/API limit cooldown
    has elapsed (or immediately when force=True).
    """
    QUEUE_WORKER_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_WORKER_LOCK_FILE, "w", encoding="utf-8") as worker_lock:
        try:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("[Queue] Another queue worker is already running; skipping.")
            return 0

        jobs = _get_pending_job_dirs()
        if not jobs:
            _clear_limit_state()
            return 0

        state = _read_queue_state()
        now_ts = time.time()
        if not force and float(state.get("next_retry_at", 0) or 0) > now_ts:
            print(
                f"[Queue] {len(jobs)} item(s) waiting ({state.get('limit_reason', 'cooldown')}); "
                f"next retry at {_format_retry_time(state['next_retry_at'])}."
            )
            return 0

        youtube, auth_err = _authenticate_youtube(headless=True)
        if not youtube:
            if auth_err == "transient":
                state = _record_limit_state("transientError")
                _log_queue(f"Network/DNS error refreshing OAuth token; retrying at {_format_retry_time(state['next_retry_at'])}.")
                return 0
            _log_queue(f"Authentication required ({auth_err}); waiting for interactive sign-in.")
            notify(
                "🔐 YouTube Re-Auth Needed",
                f"{len(jobs)} queued clip(s) waiting. Export one clip manually or sign in to refresh token.",
                icon="dialog-warning",
                expire_ms=9000,
            )
            return 1

        _log_queue(f"Processing {len(jobs)} queued upload job(s)...")
        succeeded_jobs = 0

        for _, job_dir, job in jobs:
            pending_parts = list(job.get("pending_parts") or [])
            uploaded_links = list(job.get("uploaded_links") or [])
            last_privacy = "public"

            while pending_parts:
                part = pending_parts[0]
                last_privacy = part.get("privacy", last_privacy)
                media_path = part.get("file_path")

                # Fall back to archived/source clip path if a non-temporary media path was moved
                if (not media_path or not os.path.exists(media_path)) and not part.get("delete_media_on_done"):
                    for cand in (job.get("queued_source_path"), job.get("source_video_path")):
                        if cand and os.path.exists(cand):
                            media_path = cand
                            break

                if not media_path or not os.path.exists(media_path):
                    _log_queue(f"[Queue Warn] Missing media for {job_dir.name}; moving job to _failed.")
                    failed_dir = QUEUE_DIR / "_failed"
                    failed_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(job_dir), str(failed_dir / job_dir.name))
                    pending_parts = None
                    break

                thumb_path = part.get("thumb_path")
                if thumb_path and not os.path.exists(thumb_path):
                    thumb_path = None

                try:
                    vid_id, vid_url, err_kind = _execute_video_upload_with_status(
                        youtube,
                        media_path,
                        part.get("title", "Quake Live Highlight"),
                        part.get("description", ""),
                        part.get("tags") or [],
                        str(part.get("category_id", "20")),
                        part.get("privacy", "public"),
                        thumb_path,
                    )
                except Exception as exc:
                    attempts = int(job.get("attempts", 0)) + 1
                    job["attempts"] = attempts
                    job["last_error"] = str(exc)
                    job["last_attempt_at"] = time.time()
                    _write_json_atomic(job_dir / "job.json", job)
                    _log_queue(f"[Queue Error] Unexpected failure on {job_dir.name} (attempt {attempts}): {exc}")
                    if attempts >= 5:
                        failed_dir = QUEUE_DIR / "_failed"
                        failed_dir.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(job_dir), str(failed_dir / job_dir.name))
                    pending_parts = None
                    break

                if vid_id:
                    _record_successful_upload(vid_id, part.get("title", ""))
                    _clear_limit_state()
                    link = f"https://www.youtube.com/shorts/{vid_id}" if part.get("kind") == "shorts" else vid_url
                    uploaded_links.append(link)
                    pending_parts.pop(0)
                    if part.get("delete_media_on_done"):
                        _cleanup_temp_files([media_path, thumb_path])
                    job["pending_parts"] = pending_parts
                    job["uploaded_links"] = uploaded_links
                    _write_json_atomic(job_dir / "job.json", job)
                    _log_queue(f"[Queue Uploaded] {part.get('kind', 'video')} -> {link}")
                    continue

                if err_kind in RETRYABLE_UPLOAD_ERRORS:
                    job["attempts"] = int(job.get("attempts", 0)) + 1
                    job["reason"] = err_kind
                    job["last_attempt_at"] = time.time()
                    _write_json_atomic(job_dir / "job.json", job)
                    state = _record_limit_state(err_kind)
                    remaining = len(_get_pending_job_dirs())
                    _log_queue(
                        f"[Queue Paused] Hit {err_kind}; {remaining} clip(s) remain queued. "
                        f"Next retry: {_format_retry_time(state['next_retry_at'])}"
                    )
                    return 0

            if pending_parts is None:
                continue

            # Job completed: archive original clip out of queued/ into uploaded/ and remove job artifacts
            dest_moved = _archive_uploaded_sources(
                job.get("source_video_path"),
                queued_source_path=job.get("queued_source_path"),
                shorts_archive_dir=job.get("shorts_archive_dir"),
                final_uploaded_dir=job.get("final_uploaded_dir"),
            )
            shutil.rmtree(job_dir, ignore_errors=True)
            _notify_upload_success(uploaded_links, dest_moved, last_privacy, from_queue=True)
            succeeded_jobs += 1

        if not _get_pending_job_dirs():
            _clear_limit_state()
            _log_queue(f"Queue drain complete ({succeeded_jobs} job(s) uploaded, 0 remaining).")
        else:
            _log_queue(f"Queue pass complete ({succeeded_jobs} job(s) uploaded).")
        return 0


def print_queue_status():
    jobs = _get_pending_job_dirs()
    state = _read_queue_state()
    print(f"Pending queued clips: {len(jobs)}")
    if state.get("limit_reason"):
        print(
            f"Active limit state  : {state['limit_reason']} "
            f"(hit {_format_retry_time(state.get('limit_hit_at'))}, "
            f"next retry {_format_retry_time(state.get('next_retry_at'))})"
        )
    else:
        print("Active limit state  : none")
    for idx, (_, job_dir, data) in enumerate(jobs, 1):
        src_name = Path(data.get("source_video_path", job_dir.name)).name
        parts_str = ", ".join(p.get("kind", "video") for p in data.get("pending_parts", []))
        print(f"  {idx}. {src_name} [{parts_str}] | attempts={data.get('attempts', 0)} | reason={data.get('reason')}")


def upload_video(video_path: str, enemy: str = None, weapon: str = None, special_play: str = None, custom_title: str = None, prompt: bool = False, shorts: bool = None, shorts_style: str = None, dual_upload: bool = None, cut_on_death: bool = None, include_hud: bool = None, player: str = None, custom_desc: str = None, action_slowmo: bool = None, slowmo_speed: float = None, action_zoom: bool = None, action_emojis: bool = None, action_audio: bool = None, start_time: float = None, force_upload: bool = False):
    if not os.path.exists(video_path):
        notify("Upload Failed", f"File not found: {video_path}", icon="dialog-error")
        return

    config = load_config()

    stem = Path(video_path).stem
    overrides = {}
    if OVERRIDES_FILE.exists():
        try:
            with open(OVERRIDES_FILE, "r") as f:
                all_overrides = json.load(f)
                overrides = all_overrides.get(stem, {})
        except Exception as e:
            print("Error loading clip overrides:", e)

    start_time = start_time if start_time is not None else float(overrides.get("start_time", 0.0))
    temp_trimmed_source = None
    original_video_to_move = video_path
    if start_time > 0.05:
        temp_trimmed_source = f"/tmp/{stem}_start_{start_time:.2f}_{int(time.time())}.mp4"
        cmd_trim = [
            "ffmpeg", "-y", "-ss", str(start_time),
            "-i", video_path,
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "14",
            "-c:a", "aac", "-b:a", "320k",
            temp_trimmed_source
        ]
        res_trim = subprocess.run(cmd_trim, capture_output=True, text=True)
        if res_trim.returncode == 0 and os.path.exists(temp_trimmed_source) and os.path.getsize(temp_trimmed_source) > 1000:
            print(f"[Clip Trim] Cut beginning from 0.0s to {start_time:.2f}s as requested.")
            video_path = temp_trimmed_source
        else:
            _cleanup_temp_files([temp_trimmed_source])
            temp_trimmed_source = None

    player_name = player or overrides.get("player") or config.get("player_name", "Player")
    enemy = enemy or overrides.get("enemy")
    weapon = weapon or overrides.get("weapon")
    special_play = special_play or overrides.get("special_play")
    custom_title = custom_title or overrides.get("custom_title")
    custom_desc = custom_desc or overrides.get("custom_desc")

    if prompt:
        e, w, s, c = prompt_frag_details(video_path, player_name)
        if e is None and w is None and s is None and c is None:
            print("Upload cancelled by user.")
            _cleanup_temp_files([temp_trimmed_source])
            return
        enemy = e or enemy or ""
        weapon = w or weapon or ""
        special_play = s or special_play or ""
        custom_title = c or custom_title or ""
    elif not enemy or not weapon:
        notify("🔍 Analyzing Video...", f"{Path(video_path).name}\nDetecting enemy frags & weapons...", icon="video-x-generic", expire_ms=3000)
        auto_data = analyze_video_gameplay(video_path, player_name=player_name)
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

    should_cut = cut_on_death if cut_on_death is not None else config.get("cut_before_respawn", config.get("cut_on_death", True))
    cut_chime_enabled = config.get("cut_recording_chime", True)
    cutoff_delay = float(config.get("death_cutoff_delay", 0.5))

    cut_duration = None
    if should_cut or cut_chime_enabled:
        has_cut, cut_t, total_d, reason = detect_clip_cut_point(
            video_path,
            player_name=player_name,
            cutoff_delay=cutoff_delay,
            check_chime=cut_chime_enabled,
            check_ocr=should_cut
        )
        if has_cut and cut_t < dur - 0.1:
            cut_duration = cut_t
            print(f"[Clip Cut] Ending trimmed to {cut_duration:.2f}s ({reason}) to cut out erroneous post-round/respawn/chime footage.")

    is_shorts = (shorts is True) or (shorts is None and config.get("shorts_mode", True) and dur <= 60.0)
    shorts_style = shorts_style or config.get("shorts_style", "crop")
    should_dual = dual_upload if dual_upload is not None else config.get("dual_upload", True)
    should_include_hud = include_hud if include_hud is not None else config.get("include_hud_overlays", True)

    enable_action_slowmo = action_slowmo if action_slowmo is not None else config.get("action_slowmo", True)
    speed_action_slowmo = slowmo_speed if slowmo_speed is not None else float(config.get("action_slowmo_speed", 0.25))
    enable_zoom = action_zoom if action_zoom is not None else config.get("action_zoom", True)
    enable_emojis = action_emojis if action_emojis is not None else config.get("action_emojis", True)
    enable_audio_sfx = action_audio if action_audio is not None else config.get("action_excitement_audio", True)

    subtitles_ass = None
    if is_shorts and config.get("display_left_chat", True):
        subtitles_ass = generate_chat_subtitles(video_path, max_duration=cut_duration, player_name=player_name)

    if not TOKEN_FILE.exists() and not CLIENT_SECRETS_FILE.exists():
        _cleanup_temp_files([subtitles_ass])
        handle_browser_upload(video_path, config, enemy, weapon, special_play, custom_title)
        _cleanup_temp_files([temp_trimmed_source])
        return

    privacy = config.get("privacy_status", "public")
    cat_id = str(config.get("category_id", "20"))
    server_info = get_quake_server_info()
    upload_parts = []

    if is_shorts:
        style_desc = "full-screen vertical trim" if shorts_style == "crop" else "blurred background"
        cut_notice = f"\nStopping at {cut_duration:.1f}s (cut before respawn)" if cut_duration else ""
        chat_notice = "\nChat text displayed at bottom" if subtitles_ass else ""
        hud_notice = "\nHUD (Scores & Health/Armor) included" if should_include_hud else ""
        slowmo_notice = f"\nAction Climax Slow-Mo ({speed_action_slowmo}x) & SFX" if (shorts_style == "crop" and enable_action_slowmo) else ""
        notify("📱 Creating YouTube Short (9:16)...", f"{Path(video_path).name}{cut_notice}{chat_notice}{hud_notice}{slowmo_notice}\nFormatting {style_desc} with NVENC...", icon="video-x-generic", expire_ms=3500)

        shorts_target = None
        target_lufs = float(config.get("audio_loudness_i", -15.0))
        target_tp = float(config.get("audio_true_peak", -1.5))
        try:
            shorts_target = convert_to_shorts_format(
                video_path,
                style=shorts_style,
                max_duration=cut_duration,
                subtitles_ass_path=subtitles_ass,
                include_hud=should_include_hud,
                target_lufs=target_lufs,
                true_peak=target_tp,
                weapon=weapon or "",
                feat=special_play or "",
                custom_title=custom_title or "",
                enable_action_slowmo=enable_action_slowmo,
                slowmo_speed=speed_action_slowmo,
                enable_zoom=enable_zoom,
                enable_emojis=enable_emojis,
                enable_audio_sfx=enable_audio_sfx
            )
        except Exception as e:
            print("Shorts conversion failed:", e)
        finally:
            _cleanup_temp_files([subtitles_ass])

        if shorts_target and os.path.exists(shorts_target):
            s_title, s_desc, s_tags = generate_metadata(video_path, config, enemy, weapon, special_play, custom_title, is_shorts=True, server_info=server_info, player=player_name, custom_desc=custom_desc)
            s_thumb = None
            try:
                s_thumb = extract_action_thumbnail(shorts_target)
            except Exception:
                pass
            upload_parts.append({
                "kind": "shorts",
                "file_path": shorts_target,
                "thumb_path": s_thumb,
                "title": s_title,
                "description": s_desc,
                "tags": s_tags,
                "category_id": cat_id,
                "privacy": privacy,
                "delete_media_on_done": True,
            })

        if should_dual:
            w_title, w_desc, w_tags = generate_metadata(video_path, config, enemy, weapon, special_play, custom_title, is_shorts=False, server_info=server_info, player=player_name, custom_desc=custom_desc)
            w_thumb = None
            try:
                w_thumb = extract_action_thumbnail(video_path)
            except Exception:
                pass
            upload_parts.append({
                "kind": "widescreen",
                "file_path": video_path,
                "thumb_path": w_thumb,
                "title": w_title,
                "description": w_desc,
                "tags": w_tags,
                "category_id": cat_id,
                "privacy": privacy,
                "delete_media_on_done": bool(temp_trimmed_source and video_path == temp_trimmed_source),
            })
    else:
        w_title, w_desc, w_tags = generate_metadata(video_path, config, enemy, weapon, special_play, custom_title, is_shorts=False, server_info=server_info, player=player_name, custom_desc=custom_desc)
        w_thumb = None
        try:
            w_thumb = extract_action_thumbnail(video_path)
        except Exception:
            pass
        upload_parts.append({
            "kind": "widescreen",
            "file_path": video_path,
            "thumb_path": w_thumb,
            "title": w_title,
            "description": w_desc,
            "tags": w_tags,
            "category_id": cat_id,
            "privacy": privacy,
            "delete_media_on_done": bool(temp_trimmed_source and video_path == temp_trimmed_source),
        })

    if not upload_parts:
        _cleanup_temp_files([temp_trimmed_source])
        return

    # If a daily limit cooldown is already active, queue right away without wasting API quota
    queue_state = _read_queue_state()
    if not force_upload and _is_limit_cooldown_active(queue_state):
        limit_reason = queue_state.get("limit_reason", "uploadLimitExceeded")
        _, total_pending = _enqueue_upload_job(
            original_video_to_move, upload_parts, [], limit_reason, temp_trimmed_source=temp_trimmed_source
        )
        notify(
            f"⏳ Queued for Auto-Upload (#{total_pending})",
            f"{Path(original_video_to_move).name}\nYouTube limit active — auto-retry at {_format_retry_time(queue_state.get('next_retry_at'))}.",
            icon="dialog-information",
            expire_ms=7000,
        )
        return

    youtube = get_authenticated_service()
    if not youtube:
        _cleanup_temp_files([p.get("thumb_path") for p in upload_parts] + [p.get("file_path") for p in upload_parts if p.get("delete_media_on_done")])
        handle_browser_upload(video_path, config, enemy, weapon, special_play, custom_title)
        _cleanup_temp_files([temp_trimmed_source])
        return

    uploaded_links = []
    for idx, part in enumerate(upload_parts):
        if part["kind"] == "shorts":
            detail_line = f"Target: {enemy} | Weapon: {weapon}" if enemy else f"Weapon: {weapon}"
            notify("⏳ Uploading YouTube Short...", f"{part['title']}\n{detail_line}\nVisibility: {privacy.upper()}", icon="emblem-synchronizing", expire_ms=4000)
        elif is_shorts:
            notify("🖥️ Uploading 1440p Widescreen Version...", f"Original 2560x1440 60FPS for {Path(video_path).name}...", icon="emblem-synchronizing", expire_ms=4000)
        else:
            detail_line = f"Target: {enemy} | Weapon: {weapon}" if enemy else f"Weapon: {weapon}"
            notify("⏳ Uploading Video to YouTube...", f"{part['title']}\n{detail_line}\nVisibility: {privacy.upper()}", icon="emblem-synchronizing", expire_ms=5000)

        vid_id, vid_url, err_kind = _execute_video_upload_with_status(
            youtube,
            part["file_path"],
            part["title"],
            part["description"],
            part["tags"],
            part["category_id"],
            part["privacy"],
            part.get("thumb_path"),
        )

        if vid_id:
            _record_successful_upload(vid_id, part["title"])
            _clear_limit_state()
            link = f"https://www.youtube.com/shorts/{vid_id}" if part["kind"] == "shorts" else vid_url
            uploaded_links.append(link)
            if part.get("delete_media_on_done"):
                _cleanup_temp_files([part.get("file_path")])
            continue

        if err_kind in RETRYABLE_UPLOAD_ERRORS:
            state = _record_limit_state(err_kind)
            _, total_pending = _enqueue_upload_job(
                original_video_to_move,
                upload_parts[idx:],
                uploaded_links,
                err_kind,
                temp_trimmed_source=temp_trimmed_source,
            )
            retry_str = _format_retry_time(state.get("next_retry_at"))
            if err_kind == "uploadLimitExceeded":
                notify(
                    f"⏳ Daily Limit Reached — Queued (#{total_pending})",
                    f"{Path(original_video_to_move).name}\nWill auto-upload once an opening frees up.\nNext check: {retry_str}",
                    icon="dialog-warning",
                    expire_ms=9000,
                )
            elif err_kind == "quotaExceeded":
                notify(
                    f"⏳ API Quota Reached — Queued (#{total_pending})",
                    f"{Path(original_video_to_move).name}\nWill auto-upload after midnight PT reset.\nNext check: {retry_str}",
                    icon="dialog-warning",
                    expire_ms=9000,
                )
            else:
                notify(
                    f"⏳ Upload Deferred — Queued (#{total_pending})",
                    f"{Path(original_video_to_move).name}\nTemporary network error — next retry: {retry_str}",
                    icon="dialog-warning",
                    expire_ms=8000,
                )
            return

    _cleanup_temp_files([temp_trimmed_source])
    if uploaded_links:
        dest_moved = _archive_uploaded_sources(original_video_to_move)
        _notify_upload_success(uploaded_links, dest_moved, privacy, from_queue=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Quake Live / Arena FPS YouTube Shorts Uploader")
    parser.add_argument("video", nargs="?", default=None, help="Path to video file")
    parser.add_argument("--process-queue", action="store_true", help="Upload pending queued clips whose retry window is due")
    parser.add_argument("--queue-status", action="store_true", help="Display pending upload queue status")
    parser.add_argument("--force-queue", "--force-upload", dest="force_queue", action="store_true", help="Ignore cooldown backoff when uploading or draining queue")
    parser.add_argument("--enemy", default=None, help="Enemy player name(s)")
    parser.add_argument("--weapon", default=None, help="Weapon used")
    parser.add_argument("--feat", default=None, help="Special feat / highlight")
    parser.add_argument("--title", default=None, help="Custom YouTube title")
    parser.add_argument("--player", default=None, help="POV player name (default from config: Player)")
    parser.add_argument("--desc", default=None, help="Custom YouTube description")
    parser.add_argument("--prompt", "--manual", action="store_true", help="Prompt interactive GUI dialog instead of auto-analyzing")
    parser.add_argument("--shorts", action="store_true", default=None, help="Force formatting and uploading as a 9:16 vertical YouTube Short")
    parser.add_argument("--horizontal", "--widescreen", dest="shorts", action="store_false", help="Force 16:9 widescreen upload instead of Short")
    parser.add_argument("--shorts-style", choices=["crop", "blur"], default=None, help="Shorts layout: 'crop' (full vertical trim) or 'blur' (blurred background)")
    parser.add_argument("--dual", dest="dual_upload", action="store_true", default=None, help="Upload both 9:16 Short and 16:9 widescreen versions")
    parser.add_argument("--no-dual", dest="dual_upload", action="store_false", help="Upload only one version")
    parser.add_argument("--cut-on-death", "--cut-respawn", "--cut-before-respawn", dest="cut_on_death", action="store_true", default=None, help="Stop video before respawn/scoreboard (whether died or survived)")
    parser.add_argument("--no-cut-on-death", "--no-cut-respawn", dest="cut_on_death", action="store_false", help="Do not cut video before respawn")
    parser.add_argument("--include-hud", dest="include_hud", action="store_true", default=None, help="Include top scores and full bottom Health/Armor HUD overlay")
    parser.add_argument("--no-include-hud", dest="include_hud", action="store_false", help="Do not overlay HUD elements")
    parser.add_argument("--slowmo", dest="action_slowmo", action="store_true", default=None, help="Enable action climax slow-motion zoom & highlights")
    parser.add_argument("--no-slowmo", dest="action_slowmo", action="store_false", help="Disable action climax slow-motion")
    parser.add_argument("--slowmo-speed", type=float, default=None, help="Action slow-motion playback speed (default: 0.25)")
    parser.add_argument("--zoom", dest="action_zoom", action="store_true", default=None, help="Enable dynamic action zoom on climax/frags")
    parser.add_argument("--no-zoom", dest="action_zoom", action="store_false", help="Disable dynamic action zoom")
    parser.add_argument("--emojis", dest="action_emojis", action="store_true", default=None, help="Enable excitement/weapon emoji overlays")
    parser.add_argument("--no-emojis", dest="action_emojis", action="store_false", help="Disable emoji overlays")
    parser.add_argument("--excitement-audio", dest="action_audio", action="store_true", default=None, help="Enable iconic Quake announcer & excitement audio")
    parser.add_argument("--no-excitement-audio", dest="action_audio", action="store_false", help="Disable excitement audio")
    parser.add_argument("--start-time", "--ss", type=float, default=None, help="Start time in seconds to cut off beginning of video")
    args = parser.parse_args()

    if args.queue_status:
        print_queue_status()
        sys.exit(0)
    if args.process_queue:
        sys.exit(process_upload_queue(force=args.force_queue))
    if not args.video:
        parser.error("the following arguments are required: video (or use --process-queue / --queue-status)")

    upload_video(
        video_path=args.video,
        enemy=args.enemy,
        weapon=args.weapon,
        special_play=args.feat,
        custom_title=args.title,
        prompt=args.prompt,
        shorts=args.shorts,
        shorts_style=args.shorts_style,
        dual_upload=args.dual_upload,
        cut_on_death=args.cut_on_death,
        include_hud=args.include_hud,
        player=args.player,
        custom_desc=args.desc,
        action_slowmo=args.action_slowmo,
        slowmo_speed=args.slowmo_speed,
        action_zoom=args.action_zoom,
        action_emojis=args.action_emojis,
        action_audio=args.action_audio,
        start_time=args.start_time,
        force_upload=args.force_queue,
    )
