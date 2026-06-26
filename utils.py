"""
言 — 工具函数模块
"""
import json
import os
import jieba
from datetime import datetime


# ── 数据 I/O ──────────────────────────────────────────────

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))


def _path(name: str) -> str:
    return os.path.join(_DATA_DIR, name)


def load_config() -> dict:
    with open(_path("config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(_path("config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_context() -> dict:
    with open(_path("context.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def save_context(ctx: dict) -> None:
    with open(_path("context.json"), "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)


def load_schedule() -> dict:
    with open(_path("schedule.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def save_schedule(sch: dict) -> None:
    with open(_path("schedule.json"), "w", encoding="utf-8") as f:
        json.dump(sch, f, ensure_ascii=False, indent=2)


# ── 时段判断 ──────────────────────────────────────────────

def get_time_segment(hour: int, minute: int) -> str:
    """根据精确的小时+分钟返回时段标签。"""
    t = hour + minute / 60.0
    if 5.0 <= t < 9.0:
        return "清晨"
    if 9.0 <= t < 12.0:
        return "上午"
    if 12.0 <= t < 14.0:
        return "午间"
    if 14.0 <= t < 18.0:
        return "下午"
    if 18.0 <= t < 21.0:
        return "晚间"
    return "深夜"


def get_segment_prompt(segment: str) -> str:
    """获取对应时段的 Prompt 片段。"""
    prompts = {
        "清晨": "现在是清晨，结合记忆写一句温暖的早安问候",
        "上午": "上午时段，结合记忆提醒用户适当休息",
        "午间": "午间时间，结合记忆聊聊午餐或午后小憩",
        "下午": "下午时段，结合记忆分享轻松话题",
        "晚间": "晚间时间，结合记忆聊点放松的话题",
        "深夜": "深夜时段，结合记忆简短安抚，提醒早睡",
    }
    return prompts.get(segment, "")


# ── 去重校验 ──────────────────────────────────────────────

def tokenize(text: str) -> set:
    """jieba 分词后返回词集合。"""
    return set(jieba.lcut(text))


def jaccard_similarity(text1: str, text2: str) -> float:
    """计算两段文本的词级 Jaccard 相似度。"""
    words1 = tokenize(text1)
    words2 = tokenize(text2)
    if not words1 and not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


def dedup_check(new_text: str, context: dict, threshold: float = 0.3) -> bool:
    """与最近 3 次推送内容对比，返回 True 表示需要重新生成。"""
    recent = [e["content"] for e in context.get("entries", [])
              if e.get("type") == "push"][-3:]
    for prev in recent:
        if jaccard_similarity(new_text, prev) > threshold:
            return True
    return False


# ── 短期记忆管理 ──────────────────────────────────────────

def add_context_entry(context: dict, entry: dict) -> None:
    """追加一条记录到短期记忆，超出上限则移除最旧条目。"""
    context.setdefault("entries", [])
    context["entries"].append(entry)
    max_n = context.get("max_entries", 20)
    if len(context["entries"]) > max_n:
        context["entries"] = context["entries"][-max_n:]
