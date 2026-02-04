import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from instaloader_tracker import snapshot_profile


def require(name, default=None):
    value = os.getenv(name, default)
    if value is None or value == "":
        sys.exit(f"Missing required env var: {name}")
    return value


# Load .env from project root if present
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Config (env overrides; defaults keep prior usernames/targets but not passwords)
LOGIN_USERNAME = os.getenv("DJ_LOGIN_USERNAME", "mystiquethewolfdog")
LOGIN_PASSWORD = require("DJ_LOGIN_PASSWORD")  # must be set in env/.env
TARGET_USERNAME = os.getenv("DJ_TARGET_USERNAME", "davidjones.tv")
COOKIE_FILE = os.getenv("DJ_COOKIE_FILE", "cookies_mystique.txt")
DB_PATH = os.getenv("DJ_DB_PATH", "instaloader.db")

run = snapshot_profile(
    login_username=LOGIN_USERNAME,
    login_password=LOGIN_PASSWORD,
    target_username=TARGET_USERNAME,
    cookie_file=COOKIE_FILE,
    db_path=DB_PATH,
)

print(f"\n✓ Saved snapshot: {run['timestamp']}")
print(f"   Followers: {run['followers_count']}")
print(f"   Following: {run['followees_count']}")
if run["run_id"]:
    print(f"   Run ID: {run['run_id']} → {DB_PATH}")

# Change detection (compares to most recent previous run)
changes = run["changes"]
if not changes or not changes["previous_timestamp"]:
    print("\nNo previous snapshot for comparison yet (this is run #1)")
else:
    print(f"\n--- Followers changes since {changes['previous_timestamp']} ---")
    if changes["followers"]["added"]:
        print(f"New: {len(changes['followers']['added'])} added → {changes['followers']['added']}")
    else:
        print("No new additions")
    if changes["followers"]["removed"]:
        print(f"Removed: {len(changes['followers']['removed'])} gone → {changes['followers']['removed']}")
    else:
        print("No removals")

    print(f"\n--- Followees changes since {changes['previous_timestamp']} ---")
    if changes["followees"]["added"]:
        print(f"New: {len(changes['followees']['added'])} added → {changes['followees']['added']}")
    else:
        print("No new additions")
    if changes["followees"]["removed"]:
        print(f"Removed: {len(changes['followees']['removed'])} gone → {changes['followees']['removed']}")
    else:
        print("No removals")
