"""pytest 共享配置：把 backend 目录加入 sys.path，使测试可以直接 `import app.*`。

同时必须在任何 ``app.*`` 导入之前完成环境准备，原因有三：

- ``app.config.Settings`` 在模块导入时即读取环境变量（STATIC_DIR/DATA_DIR 等）；
- ``app.main`` 在导入时以 ``settings.static_dir`` 初始化模块级 ``static_dir``
  并据此注册 ``/assets`` 挂载与 SPA 回退路由；
- ``app.db`` 在导入时以 ``settings.db_url`` 创建引擎（并按 data_dir 建目录）。

因此本文件顶部先把 STATIC_DIR / DATA_DIR 指向临时目录下构造的假静态站点
与独立数据目录，再导入 ``app.main``：既保证测试不触碰真实数据库文件，
也让 ``/assets/*`` 挂载与 spa 回退路由的行为均可测。
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# ---- 临时环境：假静态站点 + 独立数据目录（必须先于一切 app.* 导入生效）----
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="beidou-spa-test-"))

#: 假静态站点根目录（经 STATIC_DIR 环境变量注入）
STATIC_DIR = _TMP_ROOT / "static"
#: 临时数据目录（经 DATA_DIR 环境变量注入，隔离 sqlite 文件）
DATA_DIR = _TMP_ROOT / "data"

(STATIC_DIR / "assets").mkdir(parents=True)
DATA_DIR.mkdir(parents=True)

INDEX_HTML = "<!DOCTYPE html><html><body><h1>北斗 index</h1></body></html>"
APP_JS = "console.log('beidou-app');"

(STATIC_DIR / "index.html").write_text(INDEX_HTML, encoding="utf-8")
(STATIC_DIR / "assets" / "app.js").write_text(APP_JS, encoding="utf-8")

#: 故意放在静态目录之外的同级秘密文件：路径穿越攻击的靶标，绝不允许被读到
SECRET_PATH = _TMP_ROOT / "secret.txt"
SECRET_BODY = "TOP-SECRET-CONTENT"
SECRET_PATH.write_text(SECRET_BODY, encoding="utf-8")

os.environ["STATIC_DIR"] = str(STATIC_DIR)
os.environ["DATA_DIR"] = str(DATA_DIR)

atexit.register(shutil.rmtree, _TMP_ROOT, ignore_errors=True)

# ---- 环境就绪，方可安全导入应用 ----
from app.main import app  # noqa: E402  pylint: disable=wrong-import-position
