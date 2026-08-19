"""极简 WebDAV 客户端（对接 AList 的 /dav/ 端点）。

仅实现北斗需要的操作：PROPFIND（探测/列目录）、MKCOL（建目录）、PUT（上传）、GET（下载）。
凭据只用于请求头，绝不写入日志或错误消息。
"""

import httpx

TIMEOUT = httpx.Timeout(60.0, connect=15.0)


class WebDAVError(Exception):
    def __init__(self, method: str, path: str, status: int):
        super().__init__(f"WebDAV {method} {path} 失败: HTTP {status}")
        self.status = status


class WebDAVClient:
    def __init__(self, base_url: str, username: str, password: str):
        # base_url 形如 https://alist.example.com/dav 或 https://alist.example.com（自动补 /dav）
        url = base_url.rstrip("/")
        if not url.lower().endswith("/dav"):
            url += "/dav"
        self.base = url
        self.auth = (username, password)

    def _url(self, path: str) -> str:
        return self.base + "/" + path.strip("/")

    async def propfind(self, path: str = "", depth: str = "0") -> tuple[int, str]:
        async with httpx.AsyncClient(timeout=TIMEOUT, auth=self.auth, follow_redirects=True) as client:
            resp = await client.request(
                "PROPFIND",
                self._url(path) if path else self.base + "/",
                headers={"Depth": depth},
            )
        return resp.status_code, resp.text

    async def exists(self, path: str) -> bool:
        status, _ = await self.propfind(path, depth="0")
        if status in (200, 207):
            return True
        if status == 404:
            return False
        raise WebDAVError("PROPFIND", path, status)

    async def mkcol(self, path: str) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT, auth=self.auth, follow_redirects=True) as client:
            resp = await client.request("MKCOL", self._url(path))
        if resp.status_code not in (200, 201, 204, 301, 302, 405):  # 405 = 已存在
            raise WebDAVError("MKCOL", path, resp.status_code)

    async def ensure_dirs(self, path: str) -> None:
        """逐级创建目录（类似 mkdir -p）。"""
        parts = [p for p in path.strip("/").split("/") if p]
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            await self.mkcol(current)

    async def put(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        parent = path.strip("/").rsplit("/", 1)[0] if "/" in path.strip("/") else ""
        if parent:
            await self.ensure_dirs(parent)
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0), auth=self.auth, follow_redirects=True) as client:
            resp = await client.put(self._url(path), content=data, headers={"Content-Type": content_type})
        if resp.status_code not in (200, 201, 204):
            raise WebDAVError("PUT", path, resp.status_code)

    async def get(self, path: str) -> bytes:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0), auth=self.auth, follow_redirects=True) as client:
            resp = await client.get(self._url(path))
        if resp.status_code != 200:
            raise WebDAVError("GET", path, resp.status_code)
        return resp.content

    async def test(self) -> None:
        """连通性测试：PROPFIND 根目录。207=正常，401=凭据错误。"""
        status, _ = await self.propfind("", depth="0")
        if status == 401:
            raise WebDAVError("PROPFIND", "/", 401)
        if status not in (200, 207):
            raise WebDAVError("PROPFIND", "/", status)
