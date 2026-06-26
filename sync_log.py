"""
言 — 开机后一键同步推送记录到本地
从 ntfy.sh 拉取最近 48h 的推送消息，写入 YAN_PUSH_LOG_PATH。
"""
import os
import httpx
import json
from datetime import datetime, timedelta

MAX = 20


def _read_config() -> dict:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


_cfg = _read_config()
TOPIC = os.environ.get("NTFY_TOPIC", _cfg.get("ntfy_topic", "YOUR_TOPIC"))
LOCAL_PATH = os.environ.get("YAN_PUSH_LOG_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "push_records.md"))


def main():
    since_ts = int((datetime.now() - timedelta(hours=48)).timestamp())
    msgs = []
    try:
        url = f"https://ntfy.sh/{TOPIC}/json?since={since_ts}"
        with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=15.0, write=None, pool=None)) as c:
            with c.stream("GET", url) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get("event") == "message":
                        ct = msg.get("message", "")
                        t = msg.get("time", 0)
                        d = datetime.fromtimestamp(t).isoformat() if t else datetime.now().isoformat()
                        msgs.append(f"- {d} [ntfy] {ct}")
    except httpx.ReadTimeout:
        pass
    except Exception as e:
        print(f"拉取失败: {e}")
        return

    if not msgs:
        print("ntfy 中没有记录")
        return
    print(f"获取到 {len(msgs)} 条消息")

    local = ""
    if os.path.exists(LOCAL_PATH):
        with open(LOCAL_PATH, encoding="utf-8") as f:
            local = f.read().strip()
    header = "# 推送记录"
    lines = [l for l in local.split(chr(10)) if l.startswith("- ")] if local else []
    for m in msgs:
        if m not in lines:
            lines.append(m)
    lines.sort(reverse=True)
    if len(lines) > MAX:
        lines = lines[-MAX:]
    with open(LOCAL_PATH, "w", encoding="utf-8") as f:
        f.write(header + "\n\n" + "\n".join(lines) + "\n")
    print(f"同步完成！共 {len(lines)} 条记录")


if __name__ == "__main__":
    main()
