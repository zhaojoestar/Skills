#!/usr/bin/env python3
"""vision.py - 通过 Kimi K2.5 (Moonshot API) 识别图片。

用法见 SKILL.md；运行 `vision.py --help` 查看完整命令。
"""
import argparse
import base64
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "kimi-k2.5"
DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
SKILL_DIR = Path(__file__).resolve().parent.parent  # .../skills/vision
CONFIG_PATH = SKILL_DIR / "config.json"

EXIT_OK = 0
EXIT_ERR = 1      # 一般错误：文件不存在/非图片/非 Windows
EXIT_NO_KEY = 2   # 缺少 API key
EXIT_QUOTA = 3    # 额度耗尽

DEFAULT_PROMPT = ("请用中文详细描述这张图片的内容：画面主体、场景、人物/物体、"
                  "布局结构；如有文字请完整提取（OCR）；如果是界面截图，说明这"
                  "是什么界面并指出关键信息。")

MAX_RAW_BYTES = 4 * 1024 * 1024  # 超过 4MB 自动压缩


def load_config():
    """读取 config.json，缺失字段补默认值。"""
    cfg = {"api_key": None, "model": DEFAULT_MODEL, "base_url": DEFAULT_BASE_URL}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.update({k: data[k] for k in ("api_key", "model", "base_url") if k in data})
        except (json.JSONDecodeError, OSError):
            pass  # 配置损坏时回退默认，由调用方提示重新配置
    return cfg


def save_config(api_key):
    """把 API key 写入 config.json（保留 model/base_url 覆盖）。"""
    cfg = load_config()
    cfg["api_key"] = api_key
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def config_ready(cfg=None):
    cfg = cfg or load_config()
    return bool(cfg["api_key"])


def cmd_config(args):
    """--config [key]：带 key 则写入；不带则打印指引。"""
    if args.config_key:
        save_config(args.config_key)
        print(f"API key 已保存到 {CONFIG_PATH}")
        return EXIT_OK
    print("缺少 API key。请到 https://platform.moonshot.cn 申请后执行：")
    print(f'  python "{Path(__file__).resolve()}" --config "sk-你的key"')
    return EXIT_NO_KEY


def cmd_check(args):
    """--check：检查配置就绪 + 网络连通性（GET /v1/models）。"""
    cfg = load_config()
    if not config_ready(cfg):
        print("[配置未就绪] 缺少 API key，请执行 --config \"sk-xxx\"")
        return EXIT_NO_KEY
    print(f"[配置就绪] model={cfg['model']}  base_url={cfg['base_url']}")
    try:
        url = cfg["base_url"].rstrip("/") + "/models"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg['api_key']}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ids = [m.get("id") for m in data.get("data", [])][:10]
        print(f"[网络连通] 可用模型示例: {ids}")
        return EXIT_OK
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", "replace")  # HTTPError 的 body 只能读一次
        code = classify_error(e.code, body_text)
        print(f"[网络失败] HTTP {e.code}: {body_text[:300]}")
        return code
    except Exception as e:
        print(f"[网络失败] {e}")
        return EXIT_ERR


IMAGE_EXT_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def get_mime(path):
    """按扩展名推断 MIME；未知返回 application/octet-stream。"""
    return IMAGE_EXT_MIME.get(Path(path).suffix.lower(), "application/octet-stream")


def read_image(path):
    """读取图片文件字节；不存在抛 FileNotFoundError。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    return p.read_bytes()


def needs_compress(path):
    """文件大小超过 MAX_RAW_BYTES 时需要压缩。"""
    return Path(path).stat().st_size > MAX_RAW_BYTES


def download_url(url, timeout=15):
    """下载 URL 到临时文件并返回路径（Moonshot 不支持远程 URL，必须先本地化）。
    调用方负责在 finally 中删除返回的文件。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 vision-skill"})
    fd, tmp_path = tempfile.mkstemp(suffix=".img", prefix="vision_url_")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with os.fdopen(fd, "wb") as f:
                f.write(resp.read())
        return tmp_path
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass  # fd 已由 os.fdopen 关闭
        os.unlink(tmp_path)
        raise


def _ps_encoded(script):
    """把 PowerShell 脚本编码为 UTF-16LE base64（-EncodedCommand 格式）。"""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _run_ps(script, timeout=120):
    """执行 PowerShell 脚本，成功返回 (returncode, stdout, stderr)。"""
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", _ps_encoded(script)]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def _ensure_windows():
    if os.name != "nt":
        raise NotImplementedError("剪贴板/截图功能仅支持 Windows")


def _screenshot_script(tmp_path):
    p = str(tmp_path).replace("'", "''")
    return r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$g.Dispose()
$bmp.Save('""" + p + r"""', [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
"""


def _clipboard_script(tmp_path):
    p = str(tmp_path).replace("'", "''")
    return r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($img -eq $null) { Write-Error '剪贴板中没有图片'; exit 1 }
$img.Save('""" + p + r"""', [System.Drawing.Imaging.ImageFormat]::Png)
$img.Dispose()
"""


def screenshot_to_file():
    """全屏截图保存到临时 PNG，返回路径。调用方负责 finally 删除。"""
    _ensure_windows()
    fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="vision_shot_")
    os.close(fd)
    rc, out, err = _run_ps(_screenshot_script(tmp_path), timeout=60)
    if rc != 0:
        os.unlink(tmp_path)
        raise RuntimeError(f"截图失败: {err.strip() or out.strip()}")
    return tmp_path


def clipboard_to_file():
    """读取剪贴板图片保存到临时 PNG，返回路径；剪贴板无图片时抛 RuntimeError。"""
    _ensure_windows()
    fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="vision_clip_")
    os.close(fd)
    rc, out, err = _run_ps(_clipboard_script(tmp_path), timeout=60)
    if rc != 0:
        os.unlink(tmp_path)
        raise RuntimeError(f"剪贴板读取失败: {err.strip() or out.strip()}")
    return tmp_path


def _compress_script(src, dst, max_side, quality):
    s = str(src).replace("'", "''")
    d = str(dst).replace("'", "''")
    return r"""
Add-Type -AssemblyName System.Drawing
$src = '""" + s + r"""'
$dst = '""" + d + r"""'
$maxSide = """ + str(int(max_side)) + r"""
$quality = """ + str(int(quality)) + r"""
$img = [System.Drawing.Image]::FromFile($src)
try {
    $ratio = [Math]::Min(1.0, $maxSide / [Math]::Max($img.Width, $img.Height))
    $w = [Math]::Max(1, [int]($img.Width * $ratio))
    $h = [Math]::Max(1, [int]($img.Height * $ratio))
    $bmp = New-Object System.Drawing.Bitmap($w, $h)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($img, 0, 0, $w, $h)
    $g.Dispose()
    $enc = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
           Where-Object { $_.MimeType -eq 'image/jpeg' }
    $ep = New-Object System.Drawing.Imaging.EncoderParameters(1)
    $ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter(
        [System.Drawing.Imaging.Encoder]::Quality, [long]$quality)
    $bmp.Save($dst, $enc, $ep)
    $bmp.Dispose()
} finally {
    $img.Dispose()
}
"""


def compress_image(src, dst, max_side=2048, quality=85):
    """用 System.Drawing 缩放图片为 JPEG（最长边 max_side，质量 quality）。"""
    _ensure_windows()
    script = _compress_script(src, dst, max_side, quality)
    rc, out, err = _run_ps(script, timeout=120)
    if rc != 0 or not Path(dst).exists():
        raise RuntimeError(f"图片压缩失败: {err.strip() or out.strip()}")


QUOTA_KEYWORDS = ("balance", "insufficient", "quota", "额度", "余额不足", "积分不足")


def build_payload(model, question, image_b64, mime, thinking):
    """构造 OpenAI 兼容请求体。"""
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ],
        }],
        "max_tokens": 8192,
        "temperature": 1.0 if thinking else 0.6,
        "stream": False,
    }
    if not thinking:
        payload["thinking"] = {"type": "disabled"}
    return payload


def classify_error(status, body_text):
    """把 API 错误分类为退出码：额度耗尽 → EXIT_QUOTA，其余 → EXIT_ERR。"""
    low = body_text.lower()
    if status in (402, 429) or any(k in low for k in QUOTA_KEYWORDS):
        return EXIT_QUOTA
    return EXIT_ERR


def mock_response(image_b64):
    return ("[mock 模式] 模拟识别结果：收到图片数据 "
            f"{len(image_b64)} 字符（base64）。此响应由本地伪造，用于验证链路。")


def call_api(cfg, payload, thinking=False, mock=False, timeout=120):
    """调用 Moonshot chat/completions，返回模型回复文本。
    mock=True 时本地伪造响应，不发网络请求。
    抛 QuotaExhaustedError / ApiError / ApiNetworkError（定义见下）。"""
    if mock:
        image_b64 = payload["messages"][0]["content"][1]["image_url"]["url"].split(",", 1)[1]
        return mock_response(image_b64)

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}",
               "Content-Type": "application/json"}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    attempts = 2 if not thinking else 1  # 429/5xx 重试 1 次
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", "replace")
            if e.code in (429,) or e.code >= 500:
                if attempt < attempts - 1:
                    time.sleep(3)
                    continue
            code = classify_error(e.code, body_text)
            if code == EXIT_QUOTA:
                raise QuotaExhaustedError(e.code, body_text)
            raise ApiError(e.code, body_text)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ApiNetworkError(str(e))
    raise ApiNetworkError("请求重试后仍失败")


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"API 错误 (HTTP {status}): {body[:500]}")
        self.status, self.body = status, body


class ApiNetworkError(Exception):
    pass


class QuotaExhaustedError(ApiError):
    def __init__(self, status, body):
        super().__init__(status, body)
        self.user_hint = ("Kimi API 额度已耗尽，请到 https://platform.moonshot.cn 充值或更换 "
                          "API key（运行 --config \"sk-新key\" 更新）")


def source_kind(source, clipboard=False, screenshot=False):
    """判定图片来源类型：file / url / clipboard / screenshot。"""
    if clipboard:
        return "clipboard"
    if screenshot:
        return "screenshot"
    if source is None:
        raise ValueError("未指定图片来源：需要图片路径/URL，或 --clipboard/--screenshot")
    if source.startswith(("http://", "https://")):
        return "url"
    return "file"


def encode_data_uri(data, mime):
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def acquire_image(source, clipboard=False, screenshot=False):
    """获取图片并返回 dict：
    {"path": 图片路径, "mime": MIME, "temp": 是否为临时文件, "cleanup": 清理函数}
    本地文件不创建临时文件；URL/剪贴板/截图创建临时文件。"""
    kind = source_kind(source, clipboard, screenshot)
    if kind == "file":
        return {"path": source, "mime": get_mime(source), "temp": False,
                "cleanup": lambda: None}
    if kind == "url":
        path = download_url(source)
        return {"path": path, "mime": get_mime(path), "temp": True,
                "cleanup": lambda p=path: Path(p).unlink(missing_ok=True)}
    if kind == "clipboard":
        path = clipboard_to_file()
        return {"path": path, "mime": "image/png", "temp": True,
                "cleanup": lambda p=path: Path(p).unlink(missing_ok=True)}
    if kind == "screenshot":
        path = screenshot_to_file()
        return {"path": path, "mime": "image/png", "temp": True,
                "cleanup": lambda p=path: Path(p).unlink(missing_ok=True)}


def maybe_compress(path, mime):
    """超过阈值则压缩为临时 JPEG；否则原样返回 (path, mime, cleanup)。"""
    if not needs_compress(path):
        return path, mime, lambda: None
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="vision_compressed_")
    os.close(fd)
    Path(tmp_path).unlink(missing_ok=True)  # System.Drawing 不覆盖已存在文件时报错，先删占位
    compress_image(path, tmp_path)
    return tmp_path, "image/jpeg", lambda p=tmp_path: Path(p).unlink(missing_ok=True)


def run_recognize(args):
    """识别图片主流程。返回退出码。"""
    cfg = load_config()

    if args.mock:
        pass  # mock 模式不校验 key
    elif not config_ready(cfg):
        print("未配置 API key。请执行:")
        print(f'  python "{Path(__file__).resolve()}" --config "sk-你的key"')
        return EXIT_NO_KEY

    try:
        acq = acquire_image(args.source, args.clipboard, args.screenshot)
    except ValueError as e:
        print(f"错误: {e}")
        return EXIT_ERR
    except NotImplementedError as e:
        print(f"错误: {e}")
        return EXIT_ERR

    try:
        path, mime = acq["path"], acq["mime"]
        try:
            path, mime, clean_compress = maybe_compress(path, mime)
        except RuntimeError as e:
            print(f"错误: {e}（原图大小 {Path(acq['path']).stat().st_size / 1024 / 1024:.1f}MB）")
            return EXIT_ERR

        data = read_image(path)
        uri = encode_data_uri(data, mime)
        question = args.question or DEFAULT_PROMPT
        payload = build_payload(cfg["model"], question, uri.split(",", 1)[1], mime, args.thinking)
        timeout = 300 if args.thinking else 120
        try:
            text = call_api(cfg, payload, thinking=args.thinking, mock=args.mock, timeout=timeout)
        except QuotaExhaustedError as e:
            print(f"错误: {e}")
            print(e.user_hint)
            return EXIT_QUOTA
        except ApiError as e:
            print(f"错误: {e}")
            return EXIT_ERR
        except ApiNetworkError as e:
            print(f"错误: 网络请求失败: {e}")
            return EXIT_ERR
        finally:
            clean_compress()
            acq["cleanup"]()
        print(text)
        return EXIT_OK
    except FileNotFoundError as e:
        print(f"错误: {e}")
        return EXIT_ERR


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="vision.py", description="通过 Kimi K2.5 识别图片")
    parser.add_argument("source", nargs="?", help="图片路径或 URL")
    parser.add_argument("question", nargs="?", help="自定义问题（可选）")
    parser.add_argument("--clipboard", action="store_true", help="识别剪贴板图片")
    parser.add_argument("--screenshot", action="store_true", help="全屏截图并识别")
    parser.add_argument("--thinking", action="store_true", help="启用 Thinking 模式")
    parser.add_argument("--mock", action="store_true", help="本地伪造 API 响应（无需 key）")
    parser.add_argument("--config", metavar="KEY", dest="config_key", help="写入 API key")
    parser.add_argument("--check", action="store_true", help="检查配置与网络连通性")
    args = parser.parse_args(argv)

    if args.config_key is not None:
        return cmd_config(args)
    if args.check:
        return cmd_check(args)
    return run_recognize(args)


if __name__ == "__main__":
    sys.exit(main())
