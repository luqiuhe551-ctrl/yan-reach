"""
言 — 发送模块（Telegram + ntfy 双通道）
"""
import logging
import httpx

logger = logging.getLogger("yan.send")


def send_telegram(bot_token: str, chat_id: str, content: str) -> bool:
    """通过 Telegram Bot API 发送消息。"""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": content,
        "parse_mode": "Markdown",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                logger.info("Telegram 推送成功")
                return True
            logger.error("Telegram API 返回异常: %s", data)
            return False
    except Exception as e:
        logger.error("Telegram 推送失败: %s", e)
        return False


def send_ntfy(topic: str, server: str, content: str) -> bool:
    """通过 ntfy.sh API 发送消息。"""
    url = f"{server.rstrip('/')}/{topic}"
    headers = {"Title": "yan", "Tags": "speech_balloon"}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, content=content.encode("utf-8"), headers=headers)
            resp.raise_for_status()
            logger.info("ntfy 推送成功")
            return True
    except Exception as e:
        logger.error("ntfy 推送失败: %s", e)
        return False


def send_push(config: dict, content: str) -> bool:
    """根据配置选择推送渠道。"""
    channel = config.get("push_channel", "telegram")
    if channel == "telegram":
        token = config.get("telegram_bot_token", "")
        chat_id = config.get("telegram_chat_id", "")
        if not token or not chat_id or token == "YOUR_BOT_TOKEN":
            logger.error("Telegram 凭证未配置")
            return False
        return send_telegram(token, chat_id, content)
    elif channel == "ntfy":
        topic = config.get("ntfy_topic", "")
        server = config.get("ntfy_server", "https://ntfy.sh")
        if not topic or topic == "YOUR_TOPIC":
            logger.error("ntfy 凭证未配置")
            return False
        return send_ntfy(topic, server, content)
    else:
        logger.error("未知推送渠道: %s", channel)
        return False
