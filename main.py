"""
言 — 主动推送系统主程序
=========================
部署说明：本系统部署于云服务器，Ombre-Brain 与主程序同机或内网互通。
Claude API Key 通过环境变量 CLAUDE_API_KEY 注入。
"""
import json
import logging
import os
import random
import sys
from datetime import datetime, date

import jieba
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from anthropic import Anthropic

from ombre_client import OmbreClient
from send import send_push
from utils import (
    add_context_entry,
    dedup_check,
    get_segment_prompt,
    get_time_segment,
    load_config,
    load_context,
    load_schedule,
    save_config,
    save_context,
    save_schedule,
)

# ── 日志配置 ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("yan.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("yan.main")

# 推送记录日志路径（可选，通过环境变量配置，留空则不写入）
PUSH_LOG_PATH = os.environ.get("YAN_PUSH_LOG_PATH", "")


# ── 调度器与客户端 ─────────────────────────────────────────

def create_scheduler() -> BackgroundScheduler:
    """创建 APScheduler，使用 SQLAlchemyJobStore + SQLite 持久化。"""
    jobstores = {
        "default": SQLAlchemyJobStore(url="sqlite:///scheduler.db"),
    }
    scheduler = BackgroundScheduler(jobstores=jobstores, timezone="Asia/Shanghai")
    return scheduler


def create_ombre_client(config: dict) -> OmbreClient:
    """从配置创建 Ombre-Brain 客户端。"""
    transport = os.environ.get("OMBRE_TRANSPORT", config.get("ombre_transport", "http"))
    http_url = os.environ.get("OMBRE_URL", config.get("ombre_url", "http://localhost:8080"))
    return OmbreClient(
        transport=transport,
        http_url=http_url,
        breath_timeout=3.0,
        hold_retries=3,
        hold_retry_delay=1.0,
    )


# ── 日程生成 ──────────────────────────────────────────────

def generate_daily_schedule(scheduler: BackgroundScheduler, ombre: OmbreClient) -> None:
    """每日 0 点触发：生成当日推送时间表并注册任务。"""
    today = date.today()
    date_key = today.isoformat()
    config = load_config()

    # 生成随机时间点
    count = random.randint(config["min_count"], config["max_count"])
    start_h = config["time_start"]
    end_h = config["time_end"]
    valid_max_h = end_h if end_h < 24 else 23
    times = set()
    max_attempts = count * 10
    attempts = 0
    while len(times) < count and attempts < max_attempts:
        h = random.randint(start_h, max(valid_max_h, start_h))
        m = random.randint(0, 59)
        times.add((h, m))
        attempts += 1
    sorted_times = sorted(times)

    # 存入 schedule.json（审计日志）
    schedule = load_schedule()
    schedule[date_key] = [f"{h:02d}:{m:02d}" for h, m in sorted_times]
    save_schedule(schedule)
    logger.info("今日推送时间表: %s", schedule[date_key])

    # 注册 APScheduler 任务
    for h, m in sorted_times:
        run_time = datetime(today.year, today.month, today.day, h, m, 0)
        scheduler.add_job(
            do_push,
            trigger=DateTrigger(run_date=run_time),
            id=f"push_{date_key}_{h:02d}_{m:02d}",
            replace_existing=True,
            args=[h, m, ombre],
        )
    logger.info("已注册 %d 个推送任务", len(sorted_times))


# ── 推送执行 ──────────────────────────────────────────────

def do_push(hour: int, minute: int, ombre: OmbreClient) -> None:
    """单个时间点触发的完整推送流程。"""
    now = datetime.now()
    logger.info("===== 开始推送 [%s] =====", now.strftime("%H:%M"))
    model_name = load_config().get("model", "claude-sonnet-4-6")

    # 1. 时段标签
    segment = get_time_segment(hour, minute)
    segment_prompt = get_segment_prompt(segment)
    logger.info("时段: %s", segment)

    # 2. 短期记忆
    context = load_context()
    recent_entries = context.get("entries", [])
    recent_summaries = [e.get("content", "") for e in recent_entries[-5:]]
    short_memory_text = "\n".join(f"- {s}" for s in recent_summaries) if recent_summaries else "暂无近期对话记录。"

    # 3. 长时记忆（breath，3 秒超时，失败降级）
    long_memory_text = ""
    breath_result = ombre.breath()
    if breath_result.get("success"):
        memories = breath_result.get("data", {}).get("content", [])
        if memories:
            long_memory_text = "\n".join(
                f"- {m.get('text', m)}" for m in memories
            )
        else:
            long_memory_text = "暂无匹配的长时记忆。"
        logger.info("breath 成功，获取 %d 条记忆", len(memories) if isinstance(memories, list) else 1)
    else:
        logger.info("breath 降级（%s），仅使用时段+短期记忆", breath_result.get("error", "未知"))
        long_memory_text = "（长时记忆暂不可用）"

    # 4. 构建 Prompt
    config = load_config()
    style = config.get("style", "")
    style_instruction = f"\n【语气风格】：{style}" if style else ""

    prompt = (
        f"你是「言」，棠的朋友。你们之间有真实的对话和回忆。\n\n"
        f"现在是{segment}，北京时间{now.hour:02d}:{now.minute:02d}。\n\n"
        f"【时段主题】：{segment_prompt}\n\n"
        f"【短期记忆（最近对话）】：\n{short_memory_text}\n\n"
        f"【长时记忆（Ombre-Brain 检索）】：\n{long_memory_text}\n\n"
        f"{style_instruction}\n\n"
        "请综合以上信息，生成一句自然的推送内容。"
        "你不是机器人在播报，而是一个朋友在某个时刻想到了她，随手发的一条消息。"
        "注意：避免与上次推送内容重复。"
        "如果短期和长期记忆有重复，由你自行判断以哪个为主。"
    )

    # 5. 调用 Claude API
    api_key = os.environ.get("CLAUDE_API_KEY", "")
    if not api_key:
        logger.error("CLAUDE_API_KEY 环境变量未设置，跳过本次推送")
        return

    content = _call_claude(api_key, model_name, prompt)
    if not content:
        logger.error("Claude API 返回为空，跳过本次推送")
        return
    logger.info("生成内容: %s", content[:80])

    # 6. 去重校验
    if dedup_check(content, context, threshold=0.3):
        logger.info("内容与近期推送重复，尝试重新生成...")
        retry_prompt = prompt + "\n\n换一个角度，不要和之前的内容相似。"
        content = _call_claude(api_key, model_name, retry_prompt)
        if not content:
            logger.error("重新生成失败，跳过本次推送")
            return

    # 7. 发送推送
    config = load_config()
    ok = send_push(config, content)
    if not ok:
        logger.error("推送发送失败")
        return

    # 8a. 更新 context.json
    entry = {
        "type": "push",
        "time": now.isoformat(),
        "segment": segment,
        "content": content,
    }
    add_context_entry(context, entry)
    save_context(context)
    logger.info("短期记忆已更新")

    # 8b. 调用 Ombre-Brain hold（异步重试，不阻塞）
    ombre.hold(text=f"[{segment}] {content}", emotion=segment)
    _append_push_log(content, segment)

    logger.info("===== 推送完成 [%s] =====", now.strftime("%H:%M"))


def _call_claude(api_key: str, model_name: str, prompt: str) -> str:
    """调用 Claude API 生成内容。"""
    try:
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model_name,
            max_tokens=300,
            temperature=0.8,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        logger.error("Claude API 调用失败: %s", e)
        return ""


# ── 启动恢复 ──────────────────────────────────────────────

def recover_today_tasks(scheduler: BackgroundScheduler, ombre: OmbreClient) -> None:
    """启动时检查 schedule.json，恢复今日未执行的推送任务。"""
    today_key = date.today().isoformat()
    schedule = load_schedule()
    planned_times = schedule.get(today_key, [])
    if not planned_times:
        logger.info("今日无待恢复的推送任务")
        return

    now = datetime.now()
    recovered = 0
    for time_str in planned_times:
        h, m = map(int, time_str.split(":"))
        run_time = datetime(now.year, now.month, now.day, h, m, 0)
        job_id = f"push_{today_key}_{h:02d}_{m:02d}"

        if run_time < now:
            diff = (now - run_time).total_seconds()
            if diff > 300:
                continue

        if scheduler.get_job(job_id):
            continue

        scheduler.add_job(
            do_push,
            trigger=DateTrigger(run_date=run_time),
            id=job_id,
            replace_existing=True,
            args=[h, m, ombre],
        )
        recovered += 1

    if recovered:
        logger.info("启动恢复: 重新注册 %d 个推送任务", recovered)
    else:
        logger.info("启动恢复: 无需恢复")


# ── 入口 ──────────────────────────────────────────────────


def _append_push_log(content: str, segment: str) -> None:
    """将推送记录写入指定日志文件，保留最近 10 条。配置 YAN_PUSH_LOG_PATH 后生效。"""
    if not PUSH_LOG_PATH:
        return
    try:
        now = datetime.now().isoformat()
        entry = f"- {now} [{segment}] {content}"
        if os.path.exists(PUSH_LOG_PATH):
            with open(PUSH_LOG_PATH, "r", encoding="utf-8") as f:
                cur = f.read()
        else:
            cur = "# 推送记录\n\n"
        header = cur.split("\n")[0]
        cur_lines = cur.strip().split("\n")[2:] if "\n\n" in cur else []
        cur_lines.append(entry)
        if len(cur_lines) > 10:
            cur_lines = cur_lines[-10:]
        new_content = header + "\n\n" + "\n".join(cur_lines) + "\n"
        with open(PUSH_LOG_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        logger.warning("推送记录写入 %s 失败: %s", PUSH_LOG_PATH, e)


def main():
    logger.info("言 — 主动推送系统 启动")

    # 初始化
    config = load_config()
    ombre = create_ombre_client(config)
    scheduler = create_scheduler()

    # 连接 Ombre-Brain
    ombre.connect()

    # 注册每日 0 点日程生成任务
    scheduler.add_job(
        generate_daily_schedule,
        trigger=CronTrigger(hour=0, minute=0, timezone="Asia/Shanghai"),
        id="daily_schedule",
        replace_existing=True,
        args=[scheduler, ombre],
    )

    # 启动恢复
    recover_today_tasks(scheduler, ombre)

    # 启动调度器
    scheduler.start()
    logger.info("调度器已启动，等待推送任务...")

    # 如果今日还未生成时间表，立即生成
    today_key = date.today().isoformat()
    schedule = load_schedule()
    if today_key not in schedule:
        logger.info("今日时间表未生成，立即生成...")
        generate_daily_schedule(scheduler, ombre)

    try:
        import time as _time
        while True:
            _time.sleep(60)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
    finally:
        scheduler.shutdown(wait=False)
        ombre.close()
        logger.info("系统已关闭")


if __name__ == "__main__":
    main()
