"""AList 原生 REST API 客户端。

所有 AList 部署都提供 /api/* 接口，且权限模型独立于 WebDAV 开关，
比 /dav/ 端点更通用，故北斗的 AList 集成统一走原生接口。
响应为标准信封 {"code": 200, "message": "...", "data": ...}。
凭据只用于登录请求，token 绝不写入日志或错误消息。
"""

import urllib.parse

import httpx

TIMEOUT = httpx.Timeout(60.0, connect=15.0)
PUT_TIMEOUT = httpx.Timeout(300.0, connect=15.0)  # 上传大文件用更长的读超时


class AlistError(Exception):
    """AList 调用失败。

    message 为可直接展示给用户的中文/上游可读信息；
    status 记录 HTTP 状态码（0 = 业务层失败或网络异常）。
    """

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


class AlistClient:
    """面向北斗所需操作的极简 AList 客户端：列目录/建目录/上传/删除。"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._transport = transport  # 仅测试注入 httpx.MockTransport；生产为 None
        self._token: str = ""

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    @staticmethod
    def _norm(path: str) -> str:
        """规范化为绝对路径：原生 API 的 path 字段必须以 / 开头。

        调用方（如 integrations.py）传入的可能是 strip 过的相对形式（"beidou/backup"），
        统一在这里补齐，避免上游路径语义差异。
        """
        return "/" + path.strip("/")

    async def _send(
        self,
        method: str,
        url: str,
        *,
        json_body: dict | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout = TIMEOUT,
    ) -> dict:
        """发一次原始请求并解析信封；网络/解析异常统一包装为 AlistError。"""
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, transport=self._transport) as client:
                resp = await client.request(method, url, json=json_body, content=data, headers=headers)
        except httpx.HTTPError as exc:
            raise AlistError(f"无法连接 AList: {exc.__class__.__name__}") from exc
        try:
            payload = resp.json()
        except ValueError:
            # 非 JSON 响应（反代错误页等）：把 HTTP 状态码当作业务码处理，
            # 这样 401 仍能触发重登，其余状态码照常报错
            payload = {"code": resp.status_code, "message": f"AList 返回了无法解析的内容（HTTP {resp.status_code}）"}
        if not isinstance(payload, dict):
            raise AlistError(f"AList 返回了非标准响应（HTTP {resp.status_code}）", status=resp.status_code)
        return {"code": payload.get("code", resp.status_code), "message": str(payload.get("message") or ""), "data": payload.get("data")}

    async def login(self) -> None:
        """登录获取 token 并缓存。"""
        payload = await self._send(
            "POST",
            self._url("/api/auth/login"),
            json_body={"username": self.username, "password": self.password},
        )
        if payload["code"] != 200:
            raise AlistError(payload["message"] or "登录失败", status=payload["code"])
        token = (payload["data"] or {}).get("token", "")
        if not token:
            raise AlistError("登录成功但 AList 未返回令牌", status=payload["code"])
        self._token = str(token)

    async def _req(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout = TIMEOUT,
    ) -> dict:
        """统一业务请求：未登录先登录，携带 Authorization，返回解包后的 data 字段。"""
        if not self._token:
            await self.login()
        req_headers = {"Authorization": self._token, **(headers or {})}
        for attempt in range(2):
            payload = await self._send(method, self._url(path), json_body=json_body, data=data, headers=req_headers, timeout=timeout)
            if payload["code"] == 200:
                result = payload["data"]
                return result if isinstance(result, dict) else {}
            if payload["code"] == 401 and attempt == 0:
                # token 过期/失效：重新登录后重试一次
                await self.login()
                req_headers["Authorization"] = self._token
                continue
            raise AlistError(payload["message"] or f"AList 接口返回 code {payload['code']}", status=payload["code"])
        raise AlistError("AList 请求失败", status=0)  # pragma: no cover - 循环必经 return/raise

    async def list_dir(self, path: str) -> list[dict]:
        """列出目录内容；空目录/无内容时返回空列表。"""
        data = await self._req("POST", "/api/fs/list", json_body={"path": self._norm(path), "page": 1, "per_page": 0, "refresh": False})
        return data.get("content") or []

    async def exists(self, path: str) -> bool:
        """判断路径是否存在。

        不存在判定与 remove() 口径一致：HTTP 404 或 message 含 "not found"
        均视为不存在（兼容不同 AList 版本的措辞变体，典型为 "object not found"）。
        """
        try:
            await self._req("POST", "/api/fs/get", json_body={"path": self._norm(path)})
        except AlistError as exc:
            if exc.status == 404 or "not found" in exc.message.lower():
                return False
            raise
        return True

    async def mkdir(self, path: str) -> None:
        """创建目录；目录已存在的报错视为成功（幂等）。

        匹配到含 "exist" 的报错后需用 exists() 复核确认目录确已存在才算成功，
        避免 "parent does not exist" 等父级缺失类错误被误吞成成功。
        """
        try:
            await self._req("POST", "/api/fs/mkdir", json_body={"path": self._norm(path)})
        except AlistError as exc:
            if "exist" in exc.message.lower():
                try:
                    if await self.exists(self._norm(path)):
                        return
                except AlistError:
                    pass  # 复核本身失败时按原始错误上报
            raise

    async def ensure_dirs(self, path: str) -> None:
        """确保目录存在（类似 mkdir -p）：exists() 短路，否则从根到叶逐级创建。

        每一级创建前先 exists() 探测、已存在则跳过——受限账户的基本路径本身
        已存在时不再对其多发一次 mkdir，避免触发权限类误报。
        """
        parts = [p for p in path.strip("/").split("/") if p]
        if not parts:
            return
        if await self.exists(self._norm(path)):
            return
        current = ""
        for part in parts:
            current += "/" + part
            if await self.exists(current):
                continue
            await self.mkdir(current)

    async def put(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """上传文件到远端路径（As-Task:false 同步等待结果）。"""
        headers = {
            "File-Path": urllib.parse.quote(self._norm(path), safe=""),
            "As-Task": "false",
            "Content-Type": content_type,
        }
        await self._req("PUT", "/api/fs/put", data=data, headers=headers, timeout=PUT_TIMEOUT)

    async def get_bytes(self, path: str) -> bytes:
        """下载文件到内存：先 POST /api/fs/get 拿 raw_url（带签名直链），再 GET raw_url 取字节。

        用于小文件（如备份 zip）；不适合大文件（会全部加载到内存）。
        """
        meta = await self._req("POST", "/api/fs/get", json_body={"path": self._norm(path)})
        raw_url = meta.get("raw_url")
        if not raw_url:
            raise AlistError(f"AList 未返回下载链接（{path}）", status=500)
        # raw_url 可能是相对路径（/d/...），拼上 base
        if raw_url.startswith("/"):
            url = self.base + raw_url
        elif raw_url.startswith("http://") or raw_url.startswith("https://"):
            url = raw_url
        else:
            url = self.base + "/" + raw_url.lstrip("/")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=15.0),
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise AlistError(f"下载 AList 文件失败: {exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            raise AlistError(f"下载 AList 文件返回 {resp.status_code}", status=resp.status_code)
        return resp.content

    async def remove(self, path: str) -> None:
        """删除文件；不存在视为成功。"""
        trimmed = path.strip("/")
        if "/" in trimmed:
            parent, name = trimmed.rsplit("/", 1)
            dir_path = "/" + parent
        else:
            dir_path, name = "/", trimmed
        try:
            await self._req("POST", "/api/fs/remove", json_body={"dir": dir_path, "names": [name]})
        except AlistError as exc:
            if exc.status != 404 and "not found" not in exc.message.lower():
                raise

    async def test(self) -> None:
        """连通性测试：登录并列出根目录，任何失败向上抛。"""
        await self.login()
        await self.list_dir("/")
