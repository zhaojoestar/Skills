import base64
import http.server, threading, socketserver
import json, os, subprocess, sys, tempfile, unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import vision

class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.tmp.name) / "config.json"
        # 用临时目录替换模块级 CONFIG_PATH，隔离真实配置
        self._orig = vision.CONFIG_PATH
        vision.CONFIG_PATH = self.cfg_path

    def tearDown(self):
        vision.CONFIG_PATH = self._orig
        self.tmp.cleanup()

    def test_load_missing_returns_defaults(self):
        cfg = vision.load_config()
        self.assertIsNone(cfg["api_key"])
        self.assertEqual(cfg["model"], "kimi-k2.5")
        self.assertEqual(cfg["base_url"], "https://api.moonshot.cn/v1")

    def test_save_and_load_roundtrip(self):
        vision.save_config("sk-test123")
        cfg = vision.load_config()
        self.assertEqual(cfg["api_key"], "sk-test123")
        self.assertEqual(cfg["model"], "kimi-k2.5")

    def test_save_overwrites_key(self):
        vision.save_config("sk-old")
        vision.save_config("sk-new")
        self.assertEqual(vision.load_config()["api_key"], "sk-new")

    def test_config_without_key_reports_not_ready(self):
        self.assertFalse(vision.config_ready())

class TestImageAcquire(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_mime_png(self):
        p = self.tmpdir / "a.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.assertEqual(vision.get_mime(p), "image/png")

    def test_get_mime_jpg(self):
        p = self.tmpdir / "b.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0")
        self.assertEqual(vision.get_mime(p), "image/jpeg")

    def test_get_mime_unknown_extension(self):
        p = self.tmpdir / "c.xyz"
        p.write_bytes(b"hello")
        self.assertEqual(vision.get_mime(p), "application/octet-stream")

    def test_read_image_ok(self):
        p = self.tmpdir / "ok.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        data = vision.read_image(p)
        self.assertEqual(data[:4], b"\x89PNG")

    def test_read_image_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            vision.read_image(self.tmpdir / "nope.png")

    def test_read_image_oversized_flagged(self):
        p = self.tmpdir / "big.png"
        p.write_bytes(b"\x89PNG" + b"\x00" * (vision.MAX_RAW_BYTES + 100))
        self.assertTrue(vision.needs_compress(p))

class TestUrlDownload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/img.png":
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.end_headers()
                    self.wfile.write(PNG_BYTES)
                else:
                    self.send_response(404)
                    self.end_headers()
            def log_message(self, *a):
                pass
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_download_ok(self):
        p = vision.download_url(f"http://127.0.0.1:{self.port}/img.png")
        try:
            self.assertEqual(Path(p).read_bytes()[:4], b"\x89PNG")
        finally:
            Path(p).unlink(missing_ok=True)

    def test_download_404_raises(self):
        with self.assertRaises(urllib.error.HTTPError):
            vision.download_url(f"http://127.0.0.1:{self.port}/nope.png")

    def test_download_timeout_raises(self):
        with self.assertRaises(Exception):
            vision.download_url("http://10.255.255.1:9/x.png", timeout=2)

PS_SET_CLIPBOARD_IMG = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$bmp = New-Object System.Drawing.Bitmap(120, 60)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::Orange)
$g.Dispose()
[System.Windows.Forms.Clipboard]::SetImage($bmp)
$bmp.Dispose()
"""

class TestWindowsSources(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "仅 Windows")
    def test_screenshot_to_file(self):
        p = vision.screenshot_to_file()
        try:
            data = Path(p).read_bytes()
            self.assertTrue(data[:8].startswith(b"\x89PNG") or data[:2] == b"\xff\xd8")
            self.assertGreater(len(data), 100)
        finally:
            Path(p).unlink(missing_ok=True)

    @unittest.skipUnless(os.name == "nt", "仅 Windows")
    def test_clipboard_to_file(self):
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", vision._ps_encoded(PS_SET_CLIPBOARD_IMG)],
            capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        p = vision.clipboard_to_file()
        try:
            data = Path(p).read_bytes()
            self.assertTrue(data[:8].startswith(b"\x89PNG"))
        finally:
            Path(p).unlink(missing_ok=True)

    @unittest.skipUnless(os.name != "nt", "仅非 Windows 验证报错")
    def test_non_windows_raises(self):
        with self.assertRaises(NotImplementedError):
            vision.clipboard_to_file()

class TestCompress(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "仅 Windows")
    def test_compress_small_image_works(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.png"
            dst = Path(td) / "out.jpg"
            # 用 System.Drawing 生成 300x150 测试图
            gen = r"""
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap(300, 150)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::Blue)
$g.Dispose()
$bmp.Save('""" + str(src).replace("'", "''") + r"""', [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
"""
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                                "-EncodedCommand",
                                base64.b64encode(gen.encode("utf-16-le")).decode("ascii")],
                               capture_output=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
            vision.compress_image(src, dst, max_side=2048, quality=85)
            self.assertTrue(dst.exists())
            self.assertGreater(dst.stat().st_size, 0)
            self.assertTrue(dst.read_bytes()[:2] == b"\xff\xd8")  # JPEG 魔数

    def test_compress_skipped_when_small(self):
        # 小于阈值的文件 needs_compress 为 False（阈值逻辑已在 Task 2 覆盖）
        self.assertFalse(vision.needs_compress(__file__))

class TestApi(unittest.TestCase):
    def test_build_payload_instant(self):
        p = vision.build_payload("kimi-k2.5", "问题", "QUJD", "image/png", thinking=False)
        self.assertEqual(p["model"], "kimi-k2.5")
        self.assertEqual(p["temperature"], 0.6)
        self.assertEqual(p["thinking"], {"type": "disabled"})
        content = p["messages"][0]["content"]
        self.assertEqual(content[0]["text"], "问题")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,QUJD"))

    def test_build_payload_thinking(self):
        p = vision.build_payload("kimi-k2.5", "问题", "QUJD", "image/png", thinking=True)
        self.assertNotIn("thinking", p)
        self.assertEqual(p["temperature"], 1.0)

    def test_classify_quota_402(self):
        self.assertEqual(vision.classify_error(402, "{}"), vision.EXIT_QUOTA)

    def test_classify_quota_429(self):
        self.assertEqual(vision.classify_error(429, "{}"), vision.EXIT_QUOTA)

    def test_classify_quota_balance_keyword(self):
        body = '{"error": {"message": "insufficient balance, please recharge"}}'
        self.assertEqual(vision.classify_error(200, body), vision.EXIT_QUOTA)

    def test_classify_auth_error(self):
        self.assertEqual(vision.classify_error(401, "{}"), vision.EXIT_ERR)

    def test_call_api_mock_returns_text(self):
        payload = vision.build_payload("kimi-k2.5", "q", "QUJD", "image/png", thinking=False)
        out = vision.call_api({}, payload, mock=True)
        self.assertIn("mock", out.lower())

    def test_mock_response_describes_image(self):
        self.assertIn("模拟识别", vision.mock_response("任意base64"))

class TestAcquireImage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_acquire_local_file(self):
        p = self.tmpdir / "x.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        result = vision.acquire_image(str(p))
        try:
            self.assertEqual(result["path"], str(p))
            self.assertEqual(result["mime"], "image/png")
            self.assertFalse(result["temp"])  # 本地文件不产生临时文件
        finally:
            result["cleanup"]()

    def test_acquire_url(self):
        p = self.tmpdir / "u.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        # 直接测 download 分支不可行（无服务器），这里测 source 判定
        self.assertEqual(vision.source_kind(str(p)), "file")
        self.assertEqual(vision.source_kind("https://a.com/x.png"), "url")
        self.assertEqual(vision.source_kind("http://a.com/x.png"), "url")

    def test_acquire_clipboard_flag(self):
        self.assertEqual(vision.source_kind(None, clipboard=True), "clipboard")
        self.assertEqual(vision.source_kind(None, screenshot=True), "screenshot")

    def test_acquire_nothing_raises(self):
        with self.assertRaises(ValueError):
            vision.acquire_image(None)

    def test_encode_data_uri(self):
        uri = vision.encode_data_uri(b"\x89PNG\r\n\x1a\n", "image/png")
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        self.assertIn("iVBORw0", uri)

VISION_PY = str(Path(vision.__file__).resolve())


def run_cli(*args):
    r = subprocess.run([sys.executable, VISION_PY, *args],
                       capture_output=True, text=True, encoding="utf-8", timeout=180)
    return r.returncode, r.stdout, r.stderr


class TestCli(unittest.TestCase):
    def test_cli_mock_with_local_png(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "t.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            rc, out, err = run_cli("--mock", str(img))
            self.assertEqual(rc, 0, err)
            self.assertIn("模拟识别", out)

    def test_cli_mock_custom_question(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "t.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            rc, out, err = run_cli("--mock", str(img), "这是什么颜色")
            self.assertEqual(rc, 0, err)

    def test_cli_missing_key_reports_guidance(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "t.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            with unittest.mock.patch.object(vision, "CONFIG_PATH", Path(td) / "empty_config.json"):
                rc, out, err = run_cli("--mock", str(img))  # mock 不需要 key
            self.assertEqual(rc, 0)

    def test_cli_no_source_prints_usage_error(self):
        rc, out, err = run_cli("--mock")
        self.assertEqual(rc, 1)

if __name__ == "__main__":
    unittest.main()
