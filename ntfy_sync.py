"""
言 — ntfy 推送同步监听器
=========================
监听 ntfy.sh 话题，有推送时自动写入推送记录日志。
配置：读取 config.json 中的 ntfy_topic / ntfy_server，
可通过环境变量 NTFY_TOPIC / NTFY_SERVER 覆盖。
推送记录路径通过 YAN_PUSH_LOG_PATH 环境变量配置。
"""
import httpx
import json
import time
import os
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ntfy-sync] %(message)s")
logger = logging.getLogger("ntfy-sync")


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
SERVER = os.environ.get("NTFY_SERVER", _cfg.get("ntfy_server", "https://ntfy.sh"))
LOG_PATH = os.environ.get("YAN_PUSH_LOG_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "push_records.md"))


def append_push(content: str) -> None:
    now = datetime.now().isoformat()
    entry = f"- {now} [实时同步] {content}"
    try:
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                cur = f.read()
        else:
            cur = "# 推送记录\n\n"
        header = cur.split("\n")[0]
        lines = cur.strip().split("\n")[2:] if "\n\n" in cur else []
        lines.append(entry)
        if len(lines) > 10:
            lines = lines[-10:]
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(header + "\n\n" + "\n".join(lines) + "\n")
        logger.info("已写入: %s", content[:50])
    except Exception as e:
        logger.error("写入失败: %s", e)


def main():
    url = f"{SERVER}/{TOPIC}/json"
    last_id = 0
    state_path = os.path.join(os.path.dirname(__file__), ".ntfy_offset")
    if os.path.exists(state_path):
        with open(state_path, "r") as f:
            last_id = int(f.read().strip() or "0")
    logger.info("开始监听 %s, 从 id=%s 开始", url, last_id)
    while True:
        try:
            params = {"since": last_id} if last_id > 0 else {}
            with httpx.Client(timeout=None) as c:
                with c.stream("GET", url, params=params) as r:
                    for line in r.iter_lines():
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("event") == "message":
                            content = msg.get("message", "")
                            mid = int(msg.get("id", 0))
                            if mid > last_id:
                                last_id = mid
                                append_push(content)
                                with open(state_path, "w") as f:
                                    f.write(str(last_id))
        except Exception as e:
            logger.error("连接中断: %s，5秒后重连...", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
