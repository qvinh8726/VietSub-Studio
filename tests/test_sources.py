"""Unit tests for the vietsub.sources download layer (no network, no browser)."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import quote

try:
    from . import _isolation  # noqa: F401  python -m unittest tests.test_sources
except ImportError:
    import _isolation  # noqa: F401  python -m unittest discover -s tests

from vietsub.sources import base, douyin, registry, sessions, web

AWEME_ID = "7312345678901234567"


class UrlExtractionTests(unittest.TestCase):
    def test_share_text_from_the_douyin_phone_app_yields_the_clean_link(self):
        share = (
            "4.89 复制打开抖音，看看【小明的作品】今天的测试视频 # 日常 "
            "https://v.douyin.com/iRNBho6u/ i@P.Kj 08/05 MjV:/"
        )
        self.assertEqual(base.find_urls(share), ["https://v.douyin.com/iRNBho6u/"])

    def test_chinese_text_glued_to_the_link_is_not_part_of_the_url(self):
        self.assertEqual(
            base.find_urls("复制链接https://v.douyin.com/AbC123/复制此链接，打开Dou音搜索"),
            ["https://v.douyin.com/AbC123/"],
        )

    def test_trailing_punctuation_is_removed(self):
        self.assertEqual(
            base.find_urls("Xem: https://www.douyin.com/video/123456789。"),
            ["https://www.douyin.com/video/123456789"],
        )


class DouyinLinkTests(unittest.TestCase):
    def test_web_video_link_is_normalized_to_the_canonical_url(self):
        link = douyin.parse_douyin_url(
            f"http://douyin.com/video/{AWEME_ID}?previous_page=app_code_link&enter_from=main"
        )
        self.assertEqual(link.aweme_id, AWEME_ID)
        self.assertEqual(link.kind, "video")
        self.assertEqual(link.url, f"https://www.douyin.com/video/{AWEME_ID}")

    def test_feed_and_profile_modal_links_use_the_modal_id(self):
        for url in (
            f"https://www.douyin.com/jingxuan?modal_id={AWEME_ID}",
            f"https://www.douyin.com/user/MS4wLjABAAAA?from_tab_name=main&modal_id={AWEME_ID}",
            f"https://www.douyin.com/discover?modal_id={AWEME_ID}",
        ):
            with self.subTest(url=url):
                link = douyin.parse_douyin_url(url)
                self.assertEqual(link.aweme_id, AWEME_ID)
                self.assertEqual(link.url, f"https://www.douyin.com/video/{AWEME_ID}")

    def test_share_page_and_note_links_keep_their_id(self):
        share = douyin.parse_douyin_url(
            f"https://www.iesdouyin.com/share/video/{AWEME_ID}/?region=CN&mid=1"
        )
        self.assertEqual((share.aweme_id, share.kind), (AWEME_ID, "video"))
        note = douyin.parse_douyin_url(f"https://www.douyin.com/note/{AWEME_ID}")
        self.assertEqual((note.aweme_id, note.kind), (AWEME_ID, "note"))

    def test_short_links_are_kept_for_resolution(self):
        link = douyin.parse_douyin_url("http://v.douyin.com/iRNBho6u/?utm=1")
        self.assertEqual(link.kind, "short")
        self.assertEqual(link.aweme_id, "")
        self.assertEqual(link.url, "https://v.douyin.com/iRNBho6u/")

    def test_profiles_mixes_and_lookalike_hosts_are_rejected(self):
        for url in (
            "https://www.douyin.com/user/MS4wLjABAAAA",
            "https://www.douyin.com/collection/7000000000000000000",
            f"https://www.douyin.com/mix/detail/{AWEME_ID}",
            f"https://douyin.com.attacker.test/video/{AWEME_ID}",
            "https://www.douyin.com/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                douyin.parse_douyin_url(url)


class FakeRedirectResponse:
    def __init__(self, url):
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self._url


class FakeOpener:
    """Returns scripted redirects without touching the network."""

    def __init__(self, locations):
        self.locations = dict(locations)
        self.requests = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.requests.append((url, request.get_header("User-agent"), timeout))
        location = self.locations.get(url)
        if location is None:
            return FakeRedirectResponse(url)
        headers = {"Location": location}
        raise urllib.error.HTTPError(url, 302, "Found", headers, io.BytesIO(b""))


class ShortLinkResolutionTests(unittest.TestCase):
    def test_redirect_to_the_share_page_yields_the_aweme_id(self):
        opener = FakeOpener({
            "https://v.douyin.com/iRNBho6u/": (
                f"https://www.iesdouyin.com/share/video/{AWEME_ID}/?region=CN&mid=7"
            ),
        })
        self.assertEqual(
            douyin.resolve_short_link("https://v.douyin.com/iRNBho6u/", opener=opener),
            AWEME_ID,
        )
        self.assertEqual(len(opener.requests), 1)
        self.assertTrue(opener.requests[0][1])

    def test_redirect_chains_are_followed_within_douyin_only(self):
        opener = FakeOpener({
            "https://v.douyin.com/abc/": "https://www.douyin.com/redirect?x=1",
            "https://www.douyin.com/redirect?x=1": f"/video/{AWEME_ID}",
        })
        self.assertEqual(douyin.resolve_short_link("https://v.douyin.com/abc/", opener=opener), AWEME_ID)

        outside = FakeOpener({"https://v.douyin.com/abc/": "https://evil.example/video/1"})
        with self.assertRaisesRegex(base.SourceError, "ngoài Douyin"):
            douyin.resolve_short_link("https://v.douyin.com/abc/", opener=outside)

    def test_too_many_redirects_or_no_video_raise_a_source_error(self):
        loop = FakeOpener({
            "https://v.douyin.com/abc/": "https://www.douyin.com/a",
            "https://www.douyin.com/a": "https://www.douyin.com/b",
            "https://www.douyin.com/b": "https://www.douyin.com/a",
        })
        with self.assertRaises(base.SourceError):
            douyin.resolve_short_link("https://v.douyin.com/abc/", opener=loop, max_hops=3)
        with self.assertRaises(base.SourceError):
            douyin.resolve_short_link("https://v.douyin.com/abc/", opener=FakeOpener({}))


def make_api_detail(**overrides):
    detail = {
        "aweme_id": AWEME_ID,
        "desc": "  Video thử nghiệm  ",
        "author": {"nickname": "Tác giả"},
        "duration": 65_000,
        "video": {
            "duration": 65_000,
            "play_addr": {"url_list": ["https://v26-web.douyinvod.com/default.mp4"]},
            "download_addr": {"url_list": ["https://aweme.snssdk.com/aweme/v1/play/?watermark=1"]},
            "origin_cover": {"url_list": ["https://p3-sign.douyinpic.com/origin.jpeg"]},
            "cover": {"url_list": ["https://p3-sign.douyinpic.com/cover.jpeg"]},
            "bit_rate": [
                {
                    "gear_name": "adapt_lowest_1080_1",
                    "bit_rate": 4_500_000,
                    "is_h265": 1,
                    "play_addr": {
                        "url_list": ["https://v3-web.douyinvod.com/hevc-1080.mp4"],
                        "width": 1080,
                        "height": 1920,
                    },
                },
                {
                    "gear_name": "normal_720_0",
                    "bit_rate": 1_200_000,
                    "is_h265": 0,
                    "play_addr": {
                        "url_list": [
                            "https://v3-web.douyinvod.com/avc-720.mp4",
                            "https://v9-web.douyinvod.com/avc-720.mp4",
                        ],
                        "width": 720,
                        "height": 1280,
                    },
                },
                {
                    "gear_name": "normal_1080_0",
                    "bit_rate": 2_400_000,
                    "is_h265": 0,
                    "is_bytevc1": 0,
                    "play_addr": {
                        "url_list": ["//v5-web.douyinvod.com/avc-1080.mp4"],
                        "width": 1080,
                        "height": 1920,
                    },
                },
                {
                    "gear_name": "bytevc1_1080",
                    "bit_rate": 3_000_000,
                    "is_h265": 0,
                    "is_bytevc1": 1,
                    "play_addr": {"url_list": ["https://v3-web.douyinvod.com/bytevc1.mp4"]},
                },
            ],
        },
    }
    detail.update(overrides)
    return detail


class AwemeDetailTests(unittest.TestCase):
    def test_h264_without_watermark_is_preferred_over_higher_bitrate_hevc(self):
        video = douyin.parse_aweme_detail(make_api_detail())
        self.assertEqual(video.aweme_id, AWEME_ID)
        self.assertEqual(video.title, "Video thử nghiệm")
        self.assertEqual(video.author, "Tác giả")
        self.assertEqual(video.duration_seconds, 65.0)
        self.assertEqual(
            video.play_urls[:3],
            [
                "https://v5-web.douyinvod.com/avc-1080.mp4",
                "https://v3-web.douyinvod.com/avc-720.mp4",
                "https://v9-web.douyinvod.com/avc-720.mp4",
            ],
        )
        self.assertIn("https://v3-web.douyinvod.com/hevc-1080.mp4", video.play_urls)
        self.assertIn("https://v26-web.douyinvod.com/default.mp4", video.play_urls)
        self.assertFalse(any("watermark=1" in url for url in video.play_urls))
        self.assertEqual(video.cover_urls[0], "https://p3-sign.douyinpic.com/origin.jpeg")

    def test_play_addr_is_the_fallback_when_no_bitrate_list_exists(self):
        detail = make_api_detail()
        detail["video"].pop("bit_rate")
        detail["video"]["play_addr"]["url_list"] = [
            "https://www.douyin.com/aweme/v1/playwm/?video_id=v0200&ratio=720p",
        ]
        video = douyin.parse_aweme_detail(detail)
        self.assertEqual(
            video.play_urls,
            ["https://www.douyin.com/aweme/v1/play/?video_id=v0200&ratio=720p"],
        )

    def test_api_payload_and_ssr_render_data_are_both_understood(self):
        api_payload = {"status_code": 0, "aweme_detail": make_api_detail()}
        self.assertEqual(douyin.find_aweme_detail(api_payload, AWEME_ID)["desc"].strip(), "Video thử nghiệm")
        self.assertIsNone(douyin.find_aweme_detail(api_payload, "999"))

        render_data = {
            "app": {
                "videoDetail": {
                    "awemeId": AWEME_ID,
                    "desc": "Bản SSR",
                    "authorInfo": {"nickname": "SSR"},
                    "video": {
                        "duration": 12_000,
                        "playAddr": [{"src": "//www.douyin.com/aweme/v1/play/?video_id=ssr"}],
                        "bitRateList": [
                            {"isH265": 1, "bitRate": 900, "playAddr": [{"src": "https://v1.douyinvod.com/hevc.mp4"}]},
                            {"isH265": 0, "bitRate": 500, "playAddr": [{"src": "https://v1.douyinvod.com/avc.mp4"}]},
                        ],
                        "originCover": "//p3.douyinpic.com/ssr.jpeg",
                    },
                }
            }
        }
        encoded = quote(json.dumps(render_data, ensure_ascii=False))
        detail = douyin.find_aweme_detail(douyin.parse_render_data(encoded), AWEME_ID)
        video = douyin.parse_aweme_detail(detail)
        self.assertEqual(video.title, "Bản SSR")
        self.assertEqual(video.author, "SSR")
        self.assertEqual(video.duration_seconds, 12.0)
        self.assertEqual(video.play_urls[0], "https://v1.douyinvod.com/avc.mp4")
        self.assertIn("https://www.douyin.com/aweme/v1/play/?video_id=ssr", video.play_urls)
        self.assertEqual(video.cover_urls, ["https://p3.douyinpic.com/ssr.jpeg"])

    def test_image_posts_without_video_are_rejected_with_a_clear_reason(self):
        detail = make_api_detail(images=[{"url_list": ["https://p3.douyinpic.com/1.jpeg"]}])
        detail["video"] = {"play_addr": {"url_list": []}}
        with self.assertRaisesRegex(base.SourceError, "ảnh"):
            douyin.parse_aweme_detail(detail)

    def test_captcha_probe_detects_widgets_and_titles(self):
        self.assertTrue(douyin.is_captcha_probe({"captcha": True, "title": "抖音"}))
        self.assertTrue(douyin.is_captcha_probe({"captcha": False, "title": "验证码中间页"}))
        self.assertFalse(douyin.is_captcha_probe({"captcha": False, "title": "Video - 抖音"}))
        self.assertFalse(douyin.is_captcha_probe(None))


class WebSourceTests(unittest.TestCase):
    def test_youtube_links_are_reduced_to_one_video(self):
        for url, video_id in (
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&t=42", "dQw4w9WgXcQ"),
            ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
            ("https://m.youtube.com/shorts/aBcDeFgHiJk", "aBcDeFgHiJk"),
            ("https://www.youtube.com/live/aBcDeFgHiJk?feature=share", "aBcDeFgHiJk"),
        ):
            with self.subTest(url=url):
                request = web.parse_web_url(url)
                self.assertEqual(request.site, "youtube")
                self.assertEqual(request.video_id, video_id)
                self.assertEqual(request.url, f"https://www.youtube.com/watch?v={video_id}")

    def test_tiktok_and_bilibili_videos_and_short_links_are_accepted(self):
        tiktok = web.parse_web_url("https://www.tiktok.com/@some.user/video/7301234567890123456?is_from_webapp=1")
        self.assertEqual((tiktok.site, tiktok.video_id), ("tiktok", "7301234567890123456"))
        self.assertEqual(tiktok.url, "https://www.tiktok.com/@some.user/video/7301234567890123456")
        self.assertTrue(web.parse_web_url("https://vm.tiktok.com/ZMabc123/").short_link)

        bilibili = web.parse_web_url("https://www.bilibili.com/video/BV1xx411c7mD/?p=2&spm_id_from=333")
        self.assertEqual((bilibili.site, bilibili.video_id), ("bilibili", "BV1xx411c7mD"))
        self.assertEqual(bilibili.url, "https://www.bilibili.com/video/BV1xx411c7mD?p=2")
        self.assertEqual(web.parse_web_url("https://b23.tv/AbCdEf").site, "bilibili")

    def test_channels_playlists_profiles_and_lookalikes_are_rejected(self):
        for url in (
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/@channel",
            "https://www.tiktok.com/@some.user",
            "https://space.bilibili.com/123",
            "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=short",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                web.parse_web_url(url)


XHS_NOTE_ID = "6411cf99000000001300b6d9"
KS_PHOTO_ID = "3xg9avuebhtfcbu"


class ChinesePlatformLinkTests(unittest.TestCase):
    def test_xiaohongshu_notes_keep_only_their_access_token(self):
        for url in (
            f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}?xsec_token=ABC%3D&xsec_source=pc_feed&utm=1",
            f"https://www.xiaohongshu.com/discovery/item/{XHS_NOTE_ID}?xsec_token=ABC%3D&xsec_source=pc_feed",
            f"https://www.xiaohongshu.com/user/profile/5c31698d0000000007018a31/{XHS_NOTE_ID}"
            "?xsec_token=ABC%3D&xsec_source=pc_feed",
        ):
            with self.subTest(url=url):
                request = web.parse_web_url(url)
                self.assertEqual((request.site, request.video_id), ("xiaohongshu", XHS_NOTE_ID))
                self.assertEqual(
                    request.url,
                    f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}?xsec_token=ABC%3D&xsec_source=pc_feed",
                )
        bare = web.parse_web_url(f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}")
        self.assertEqual(bare.url, f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}")
        short = web.parse_web_url("http://xhslink.com/a/AbCd1234")
        self.assertEqual((short.site, short.short_link, short.url), ("xiaohongshu", True, "https://xhslink.com/a/AbCd1234"))

    def test_kuaishou_videos_from_web_mobile_and_share_links(self):
        for url in (
            f"https://www.kuaishou.com/short-video/{KS_PHOTO_ID}?authorId=3xabc&streamSource=profile",
            f"https://v.m.chenzhongtech.com/fw/photo/{KS_PHOTO_ID}?fid=1&cc=share_copylink",
            f"https://m.gifshow.com/fw/photo/{KS_PHOTO_ID}",
        ):
            with self.subTest(url=url):
                request = web.parse_web_url(url)
                self.assertEqual((request.site, request.video_id), ("kuaishou", KS_PHOTO_ID))
                self.assertEqual(request.url, f"https://www.kuaishou.com/short-video/{KS_PHOTO_ID}")
        for url, expected in (
            ("https://v.kuaishou.com/AbCd12", "https://v.kuaishou.com/AbCd12"),
            ("https://www.kuaishou.com/f/X-6dSFbKkEcx1a1j", "https://www.kuaishou.com/f/X-6dSFbKkEcx1a1j"),
        ):
            with self.subTest(url=url):
                request = web.parse_web_url(url)
                self.assertEqual((request.site, request.short_link, request.url), ("kuaishou", True, expected))

    def test_profiles_lives_and_lookalikes_on_these_platforms_are_rejected(self):
        for url in (
            "https://www.xiaohongshu.com/user/profile/5c31698d0000000007018a31",
            "https://www.xiaohongshu.com/explore",
            f"https://xiaohongshu.com.evil.test/explore/{XHS_NOTE_ID}",
            "https://www.kuaishou.com/profile/3xabc",
            "https://live.kuaishou.com/u/3xabc",
            f"https://kuaishou.com.evil.test/short-video/{KS_PHOTO_ID}",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                web.parse_web_url(url)

    def test_share_text_from_the_phone_apps_is_understood(self):
        xhs = registry.detect_source(
            "【可爱小猫】 😆 http://xhslink.com/a/AbCd1234，复制本条信息，打开【小红书】App查看精彩内容！"
        )
        self.assertEqual((xhs.site, xhs.url), ("xiaohongshu", "https://xhslink.com/a/AbCd1234"))
        kuaishou = registry.detect_source("https://v.kuaishou.com/AbCd12 复制此消息，打开【快手】直接观看！")
        self.assertEqual((kuaishou.site, kuaishou.url), ("kuaishou", "https://v.kuaishou.com/AbCd12"))

    def test_platform_short_links_are_resolved_without_leaving_the_platform(self):
        xhs = FakeOpener({
            "https://xhslink.com/a/AbCd1234": (
                f"https://www.xiaohongshu.com/discovery/item/{XHS_NOTE_ID}"
                "?app_platform=ios&xsec_token=TOKEN&xsec_source=app_share"
            ),
        })
        resolved = web.resolve_short_link(web.parse_web_url("https://xhslink.com/a/AbCd1234"), opener=xhs)
        self.assertEqual(resolved.video_id, XHS_NOTE_ID)
        self.assertEqual(
            resolved.url,
            f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}?xsec_token=TOKEN&xsec_source=app_share",
        )
        self.assertFalse(resolved.short_link)

        kuaishou = FakeOpener({
            "https://v.kuaishou.com/AbCd12": f"https://v.m.chenzhongtech.com/fw/photo/{KS_PHOTO_ID}?fid=1",
        })
        resolved = web.resolve_short_link(web.parse_web_url("https://v.kuaishou.com/AbCd12"), opener=kuaishou)
        self.assertEqual(resolved.url, f"https://www.kuaishou.com/short-video/{KS_PHOTO_ID}")

        outside = FakeOpener({"https://v.kuaishou.com/AbCd12": "https://evil.example/fw/photo/1"})
        with self.assertRaisesRegex(base.SourceError, "ngoài Kuaishou"):
            web.resolve_short_link(web.parse_web_url("https://v.kuaishou.com/AbCd12"), opener=outside)


class SessionSiteTests(unittest.TestCase):
    def test_each_platform_has_a_home_page_and_cookie_domain(self):
        self.assertEqual(list(sessions.SESSION_SITES), ["douyin", "bilibili", "xiaohongshu", "kuaishou"])
        for site in sessions.SESSION_SITES.values():
            with self.subTest(site=site.key):
                self.assertTrue(site.home_url.startswith("https://"))
                self.assertTrue(site.cookie_domain.startswith("."))
                self.assertTrue(all(url.startswith("https://") for url in site.cookie_urls))

    def test_login_is_detected_from_each_platforms_session_cookie(self):
        def cookie(name, value="x", domain=".example.com"):
            return {"name": name, "value": value, "domain": domain}

        self.assertTrue(sessions.login_state("douyin", [cookie("sessionid")]))
        self.assertFalse(sessions.login_state("douyin", [cookie("ttwid"), cookie("sessionid", "")]))
        self.assertTrue(sessions.login_state("bilibili", [cookie("SESSDATA")]))
        self.assertFalse(sessions.login_state("bilibili", [cookie("buvid3")]))
        self.assertTrue(sessions.login_state("kuaishou", [cookie("passToken")]))
        self.assertFalse(sessions.login_state("kuaishou", [cookie("did")]))
        # Xiaohongshu gives guests a web_session cookie too, so cookies cannot tell.
        self.assertIsNone(sessions.login_state("xiaohongshu", [cookie("web_session")]))
        with self.assertRaises(KeyError):
            sessions.login_state("youtube", [])


class RegistryTests(unittest.TestCase):
    def test_the_first_supported_link_in_pasted_text_decides_the_source(self):
        request = registry.detect_source(
            f"Xem video này nhé https://www.douyin.com/jingxuan?modal_id={AWEME_ID} cảm ơn"
        )
        self.assertEqual(request.site, "douyin")
        self.assertEqual(request.video_id, AWEME_ID)
        self.assertEqual(request.url, f"https://www.douyin.com/video/{AWEME_ID}")

        short = registry.detect_source("4.89 复制打开抖音 https://v.douyin.com/iRNBho6u/ i@P.Kj")
        self.assertEqual((short.site, short.short_link), ("douyin", True))

        youtube = registry.detect_source("https://example.test/a https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(youtube.site, "youtube")

    def test_unsupported_text_explains_which_sites_work(self):
        with self.assertRaisesRegex(ValueError, "Douyin, TikTok, YouTube"):
            registry.detect_source("không có link nào")
        with self.assertRaisesRegex(ValueError, "riêng lẻ"):
            registry.detect_source("https://www.douyin.com/user/MS4wLjABAAAA")


if __name__ == "__main__":
    unittest.main()
