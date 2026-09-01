"""验证 UI 路由可用（不依赖运行中的服务）。"""
import unittest

from fastapi.testclient import TestClient

from app.main import app


class UITestCase(unittest.TestCase):
    def test_index_serves_chat_page(self):
        client = TestClient(app)
        r = client.get("/")
        self.assertEqual(r.status_code, 200)
        body = r.text
        self.assertIn("对话", body)
        self.assertIn("影子审阅", body)
        self.assertIn("/v1/chat", body)
        self.assertIn("日志", body)


if __name__ == "__main__":
    unittest.main()
