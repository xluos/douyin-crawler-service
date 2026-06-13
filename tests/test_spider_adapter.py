import unittest
from unittest.mock import patch

from douyin_crawler_service.spider_adapter import normalize_aweme_id


class NormalizeAwemeIdTest(unittest.TestCase):
    def test_accepts_numeric_aweme_id(self):
        self.assertEqual(normalize_aweme_id("7646974327342695330"), "7646974327342695330")

    def test_accepts_video_url(self):
        self.assertEqual(
            normalize_aweme_id("https://www.douyin.com/video/7646974327342695330?foo=bar"),
            "7646974327342695330",
        )

    def test_accepts_note_url(self):
        self.assertEqual(
            normalize_aweme_id("https://www.douyin.com/note/7646974327342695330?previous_page=app_code_link"),
            "7646974327342695330",
        )

    def test_accepts_modal_id_url(self):
        self.assertEqual(
            normalize_aweme_id("https://www.douyin.com/discover?modal_id=7646974327342695330"),
            "7646974327342695330",
        )

    def test_accepts_long_share_text(self):
        share_text = (
            "复制打开抖音，看看【露小裕的图文作品】 "
            "https://www.douyin.com/note/7646974327342695330?previous_page=app_code_link s@r.Eh"
        )
        self.assertEqual(normalize_aweme_id(share_text), "7646974327342695330")

    def test_resolves_short_share_text(self):
        class Response:
            url = "https://v.douyin.com/Isaxn57al44/"
            headers = {
                "location": (
                    "https://www.iesdouyin.com/share/note/7646974327342695330/"
                    "?from_ssr=1&tt_from=copy"
                )
            }

            def close(self):
                return None

        share_text = (
            "1.07 复制打开抖音，看看【露小裕的图文作品】# 她真的好甜 "
            "https://v.douyin.com/Isaxn57al44/ s@r.Eh :0pm"
        )
        with patch("douyin_crawler_service.spider_adapter.requests.get", return_value=Response()) as get:
            self.assertEqual(normalize_aweme_id(share_text), "7646974327342695330")
        get.assert_called_once()

    def test_accepts_iesdouyin_share_url(self):
        self.assertEqual(
            normalize_aweme_id("https://www.iesdouyin.com/share/note/7646974327342695330/?from_ssr=1"),
            "7646974327342695330",
        )


if __name__ == "__main__":
    unittest.main()
