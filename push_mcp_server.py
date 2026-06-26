"""
言 — 推送配置 MCP 服务
=======================
独立的 MCP 服务，提供推送系统的配置管理工具。
支持 stdio（Claude Desktop 自动拉起）和 HTTP（端口 8765）双模式。
"""
import json
import os
import sys
import time
import uuid
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Lock
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [push-mcp] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("push-mcp")

# 同时写文件日志用于调试
fh = logging.FileHandler("push_mcp.log", encoding="utf-8", mode="a")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(fh)


CONFIG_PATH = os.environ.get("PUSH_CONFIG_PATH", "./config.json")
SCHEDULE_PATH = os.environ.get("PUSH_SCHEDULE_PATH", "./schedule.json")
PORT = int(os.environ.get("PUSH_MCP_PORT", "8765"))

file_lock = Lock()

def read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

TOOLS = [
    {
        "name": "get_push_config",
        "description": "查看当前推送配置",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_push_config",
        "description": "修改推送配置",
        "inputSchema": {
            "type": "object",
            "properties": {
                "time_start": {"type": "integer", "description": "开始小时(0-23)"},
                "time_end": {"type": "integer", "description": "结束小时(1-24)"},
                "min_count": {"type": "integer", "description": "最少推送次数"},
                "max_count": {"type": "integer", "description": "最多推送次数"},
                "model": {"type": "string", "description": "Claude 模型名称"},
                "push_channel": {"type": "string", "description": "ntfy 或 telegram"},
                "ntfy_topic": {"type": "string", "description": "ntfy 订阅主题"},
            },
        },
    },
    {
        "name": "get_push_schedule",
        "description": "查看推送时间表",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "查看最近几天（默认3）"},
            },
        },
    },
    {
        "name": "set_push_style",
        "description": "设置推送语气风格",
        "inputSchema": {
            "type": "object",
            "properties": {
                "style": {"type": "string", "description": "语气风格描述"},
            },
            "required": ["style"],
        },
    },
    {
        "name": "get_push_log",
        "description": "查看最近的推送记录（从本地日志读取）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "查看最近几小时的记录（默认 24）"},
                "max_lines": {"type": "integer", "description": "最多返回条数（默认 100）"},
            },
        },
    },
]


def _safe_str(s: str) -> str:
    """移除字符串中的非法代理项(surrogate)字符，避免 JSON 序列化报错。"""
    return s.encode("utf-8", errors="replace").decode("utf-8")


def handle_tool(name: str, arguments: dict) -> Any:
    if name == "get_push_config":
        with file_lock:
            config = read_json(CONFIG_PATH)
        return json.dumps(config, ensure_ascii=False, indent=2)
    elif name == "set_push_config":
        allowed = {"time_start","time_end","min_count","max_count","model","push_channel","ntfy_topic"}
        with file_lock:
            config = read_json(CONFIG_PATH)
            changes = []
            for k, v in arguments.items():
                if k in allowed:
                    old = config.get(k)
                    config[k] = v
                    changes.append(f"{k}: {old} -> {v}")
            write_json(CONFIG_PATH, config)
        if changes:
            return "已更新:\n" + "\n".join(changes)
        return "没有需要更新的字段"
    elif name == "get_push_schedule":
        days = arguments.get("days", 3)
        with file_lock:
            schedule = read_json(SCHEDULE_PATH)
        keys = sorted(schedule.keys(), reverse=True)[:days]
        result = {k: schedule[k] for k in keys}
        return json.dumps(result, ensure_ascii=False, indent=2) if result else "暂无推送时间表"
    elif name == "set_push_style":
        style = _safe_str(arguments.get("style", ""))
        with file_lock:
            config = read_json(CONFIG_PATH)
            old_style = config.get("style", "")
            config["style"] = style
            write_json(CONFIG_PATH, config)
        return f"语气风格已更新: {old_style} -> {style}"
    elif name == "get_push_log":
        import re, subprocess
        from datetime import datetime, timedelta
        hours = int(arguments.get("hours", 24))
        max_lines = int(arguments.get("max_lines", 100))
        # 先自动同步 ntfy 推送到本地
        try:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_push_log.py")
            subprocess.run(
                ["python", script, "--hours", str(hours)],
                capture_output=True, timeout=20
            )
        except Exception:
            pass  # 同步失败不影响读取已有日志
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yan.log")
        if not os.path.exists(log_path):
            return "暂无推送记录：yan.log 不存在"
        cutoff = datetime.now() - timedelta(hours=hours)
        ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
        records = []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    text = raw_line.rstrip("\n")
                    if not text.strip():
                        continue
                    m = ts_re.match(text)
                    if not m:
                        continue
                    try:
                        line_dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                    if line_dt >= cutoff:
                        records.append(text)
        except Exception as e:
            return f"读取本地推送日志失败: {e}"
        records = records[-max_lines:]
        if not records:
            return f"最近 {hours} 小时没有找到推送记录"
        header = f"最近 {hours} 小时共 {len(records)} 条记录（来源：yan.log）："
        return header + "\n" + "\n".join(records)
    return f"未知工具: {name}"


# --- Stdio transport ---

# Windows 下必须用 binary mode，避免 \n -> \r\n 转换
if sys.platform == "win32":
    import msvcrt
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)


def _read_message():
    """从 stdin 读取一条 JSON-RPC 消息（JSONL 格式）。"""
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            return None
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            logger.warning("无法解析: %s", line[:100])
            continue


def _write_message(data: dict):
    """通过 stdout 发送 JSON-RPC 响应（JSONL 格式）。"""
    body = json.dumps(data, ensure_ascii=False) + "\n"
    sys.stdout.buffer.write(body.encode("utf-8"))
    sys.stdout.buffer.flush()


def run_stdio():
    logger.info("stdio 循环开始，等待消息...")
    while True:
        try:
            msg = _read_message()
        except Exception as e:
            logger.error("读取消息异常: %s", e, exc_info=True)
            continue
        if msg is None:
            logger.info("stdin 关闭，退出")
            break

        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})
        logger.info("收到: method=%s id=%s", method, req_id)

        # 通知类（无 id），不回复
        if req_id is None:
            continue

        if method == "initialize":
            _write_message({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "yan-push-config", "version": "1.0.0"},
                },
            })
            logger.info("initialize 完成")
        elif method == "tools/list":
            _write_message({
                "jsonrpc": "2.0", "id": req_id,
                "result": {"tools": TOOLS},
            })
            logger.info("tools/list 返回 %d 个工具", len(TOOLS))
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            logger.info("调用工具: %s 参数: %s", tool_name, json.dumps(arguments, ensure_ascii=False)[:200])
            try:
                output = handle_tool(tool_name, arguments)
                _write_message({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": str(output)}], "isError": False},
                })
                logger.info("工具 %s 执行成功", tool_name)
            except Exception as e:
                logger.error("工具 %s 执行失败: %s", tool_name, e, exc_info=True)
                _write_message({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": _safe_str(f"错误: {e}")}], "isError": True},
                })
        elif method == "ping":
            _write_message({"jsonrpc": "2.0", "id": req_id, "result": {}})
        elif method == "shutdown":
            _write_message({"jsonrpc": "2.0", "id": req_id, "result": None})
            logger.info("收到 shutdown，退出")
            break
        else:
            logger.warning("未知方法: %s", method)
            _write_message({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"未知方法: {method}"},
            })


# --- HTTP transport ---

sessions = {}

class MCPHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(fmt % args)
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()
    def do_GET(self):
        if self.path == "/sse" or self.path.startswith("/sse"):
            self._handle_sse()
        elif self.path == "/health":
            self.send_response(200); self._cors()
            self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps({"status":"ok"}).encode())
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        if self.path.startswith("/messages"):
            self._handle_message()
        else:
            self.send_response(404); self.end_headers()
    def _handle_sse(self):
        sid = str(uuid.uuid4())
        sessions[sid] = True
        self.send_response(200); self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(f"event: endpoint\ndata: /messages?session_id={sid}\n\n".encode())
        self.wfile.flush()
        try:
            while True:
                self.wfile.write(": keepalive\n\n".encode()); self.wfile.flush()
                time.sleep(30)
        except (BrokenPipeError, ConnectionResetError):
            sessions.pop(sid, None)
    def _send_jsonrpc(self, req_id, result):
        resp = {"jsonrpc":"2.0","id":req_id,"result":result}
        self.send_response(200); self._cors()
        self.send_header("Content-Type","application/json"); self.end_headers()
        self.wfile.write(json.dumps(resp).encode())
    def _handle_message(self):
        length = int(self.headers.get("Content-Length",0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._send_jsonrpc(1,{"error":"invalid json"}); return
        method = req.get("method",""); rid = req.get("id",1); params = req.get("params",{})
        if method == "initialize":
            self._send_jsonrpc(rid,{
                "protocolVersion":"2024-11-05",
                "capabilities":{"tools":{"listChanged":False}},
                "serverInfo":{"name":"yan-push-config","version":"1.0.0"},
            })
        elif method == "tools/list":
            self._send_jsonrpc(rid,{"tools":TOOLS})
        elif method == "tools/call":
            try:
                out = handle_tool(params.get("name",""), params.get("arguments",{}))
                self._send_jsonrpc(rid,{"content":[{"type":"text","text":str(out)}],"isError":False})
            except Exception as e:
                self._send_jsonrpc(rid,{"content":[{"type":"text","text":_safe_str(f"错误:{e}")}],"isError":True})
        else:
            self._send_jsonrpc(rid,{"error":f"unknown:{method}"})


# --- Entry ---

def main():
    if "--stdio" in sys.argv:
        logger.info("言 · 推送配置 MCP 服务 (stdio 模式)")
        run_stdio()
    else:
        server = HTTPServer(("0.0.0.0", PORT), MCPHandler)
        logger.info("言 · 推送配置 MCP 服务已启动，端口 %d", PORT)
        logger.info("SSE 端点: http://localhost:%d/sse", PORT)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()

if __name__ == "__main__":
    main()

