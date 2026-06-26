"""
言 — Ombre-Brain MCP 客户端
"""
import json
import logging
import time
import httpx
from typing import Optional

logger = logging.getLogger("yan.ombre")


class OmbreClient:
    """Ombre-Brain MCP 客户端，支持 breath/hold 调用。

    transport 模式:
      - "http": 通过 HTTP SSE + POST 与 MCP 服务通信
      - "mock":  本地模拟模式，不依赖外部服务（调试用）
    """

    def __init__(
        self,
        transport: str = "http",
        http_url: str = "http://localhost:8080",
        breath_timeout: float = 3.0,
        hold_retries: int = 3,
        hold_retry_delay: float = 1.0,
    ):
        self.transport = transport
        self.http_url = http_url.rstrip("/")
        self.breath_timeout = breath_timeout
        self.hold_retries = hold_retries
        self.hold_retry_delay = hold_retry_delay
        self._session_id: Optional[str] = None
        self._client = httpx.Client(timeout=httpx.Timeout(10.0))

    # ── 连接管理 ──────────────────────────────────────────

    def connect(self) -> None:
        """通过 SSE 端点获取 session_id。"""
        if self.transport != "http":
            return
        try:
            with self._client.stream("GET", f"{self.http_url}/sse", timeout=30.0) as resp:
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        if "session_id" in data:
                            self._session_id = data["session_id"]
                            logger.info("Ombre-Brain 已连接, session=%s", self._session_id)
                            return
                    if hasattr(resp, "event") or "endpoint" in line:
                        break
        except Exception as e:
            logger.warning("Ombre-Brain 连接失败: %s", e)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    # ── 工具调用 ──────────────────────────────────────────

    def _call_tool(self, tool_name: str, arguments: dict, timeout: float = 10.0) -> dict:
        """通过 MCP HTTP 协议调用工具（JSON-RPC）。"""
        if self.transport != "http":
            return {"success": False, "error": "transport not http"}
        if not self._session_id:
            logger.warning("未建立 MCP session，尝试连接...")
            self.connect()
        if not self._session_id:
            return {"success": False, "error": "no mcp session"}

        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        try:
            resp = self._client.post(
                f"{self.http_url}/messages?session_id={self._session_id}",
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            if "error" in result:
                logger.error("MCP 工具 %s 返回错误: %s", tool_name, result["error"])
                return {"success": False, "error": str(result["error"])}
            return {"success": True, "data": result.get("result", {})}
        except httpx.TimeoutException:
            logger.warning("MCP 工具 %s 超时 (%ss)", tool_name, timeout)
            return {"success": False, "error": "timeout"}
        except Exception as e:
            logger.warning("MCP 工具 %s 调用异常: %s", tool_name, e)
            return {"success": False, "error": str(e)}

    # ── breath: 检索长时记忆 ──────────────────────────────

    def breath(self, query: Optional[str] = None, top_k: int = 5) -> dict:
        """检索情感记忆，3 秒超时，超时/失败降级。"""
        if self.transport == "mock":
            return {
                "success": True,
                "memories": [
                    {"text": "（模拟记忆）今天天气很好", "emotion": "平静", "weight": 0.8},
                ],
            }
        return self._call_tool("breath", {"query": query or "", "top_k": top_k}, timeout=self.breath_timeout)

    # ── hold: 存储推送记忆 ────────────────────────────────

    def hold(self, text: str, emotion: Optional[str] = None) -> dict:
        """存储记忆，异步重试最多 3 次，不阻塞主流程。"""
        if self.transport == "mock":
            return {"success": True}

        last_err = None
        for attempt in range(1, self.hold_retries + 1):
            result = self._call_tool("hold", {"text": text, "emotion": emotion or "neutral"}, timeout=5.0)
            if result["success"]:
                return result
            last_err = result.get("error")
            if attempt < self.hold_retries:
                time.sleep(self.hold_retry_delay)
        logger.error("hold 重试 %s 次均失败: %s", self.hold_retries, last_err)
        return {"success": False, "error": last_err}
