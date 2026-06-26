"""
ntfy 推送日志 → 本地 yan.log 同步工具

从 ntfy.sh 拉取最近的推送消息，追加写入 yan.log，
让 get_push_log 工具能读到云端推送记录。

用法:
  python sync_push_log.py             # 同步最近 48h
  python sync_push_log.py --hours 72  # 同步最近 72h
"""
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / ".ntfy_push_sync_state.json"
LOG_FILE = BASE_DIR / "yan.log"
MAX_MESSAGE_AGE_HOURS = 48


def _read_config() -> dict:
    config_path = BASE_DIR / "config.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


_cfg = _read_config()
NTFY_SERVER = os.environ.get("NTFY_SERVER", _cfg.get("ntfy_server", "https://ntfy.sh"))
TOPIC = os.environ.get("NTFY_TOPIC", _cfg.get("ntfy_topic", "YOUR_TOPIC"))


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_id": "", "last_ts": 0}


def save_state(last_id: str, last_ts: int) -> None:
    STATE_FILE.write_text(
        json.dumps({"last_id": last_id, "last_ts": last_ts}),
        encoding="utf-8",
    )


def fetch_ntfy_messages(since_id: str, hours: int) -> list[dict]:
    """Fetch message events from ntfy, starting from since_id (or recent hours)."""
    if since_id:
        url = f"{NTFY_SERVER}/{TOPIC}/json?since={since_id}&poll=1"
    else:
        url = f"{NTFY_SERVER}/{TOPIC}/json?since={hours}h&poll=1"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    messages = []
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            for raw_line in resp.read().decode("utf-8").split("\n"):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("event") == "message":
                    messages.append(msg)
    except Exception as e:
        print(f"[警告] ntfy 拉取失败: {e}", file=sys.stderr)
    return messages


def main() -> int:
    hours = MAX_MESSAGE_AGE_HOURS
    if "--hours" in sys.argv:
        idx = sys.argv.index("--hours")
        if idx + 1 < len(sys.argv):
            hours = int(sys.argv[idx + 1])

    state = load_state()
    last_id = state.get("last_id", "")
    messages = fetch_ntfy_messages(last_id, hours)

    if not messages:
        print("没有新推送需要同步")
        return 0

    seen_ids = set()
    new_messages = []
    for msg in messages:
        mid = msg.get("id", "")
        if mid and mid != last_id and mid not in seen_ids:
            seen_ids.add(mid)
            new_messages.append(msg)

    if not new_messages:
        print("没有新推送需要同步")
        return 0

    new_messages.sort(key=lambda m: int(m.get("time", 0)))

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        for msg in new_messages:
            ts = msg.get("time", 0)
            dt = datetime.fromtimestamp(ts)
            timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            content = msg.get("message", "").replace("\n", " | ")
            f.write(f"{timestamp} [ntfy-sync] INFO: {content}\n")

    last_msg = new_messages[-1]
    save_state(last_msg.get("id", ""), int(last_msg.get("time", 0)))

    print(f"已同步 {len(new_messages)} 条推送到 yan.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
