import tempfile

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from audiobooks.models import Audiobook


class PlayerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.book = Audiobook.objects.create(
            title="Playback test",
            author="Test author",
            narrator="Test narrator",
            description="A test audiobook.",
            audio_file="audio/test.mp3",
            cover="covers/test.jpg",
            duration=120,
            published=True,
        )

    def test_full_page_has_one_persistent_audio_element(self):
        response = self.client.get(reverse("book_detail", args=[self.book.slug]))
        self.assertContains(response, '<audio id="audio"', count=1)
        content = response.content.decode()
        self.assertLess(content.index('<audio id="audio"'), content.index('<div id="global-player"'))
        self.assertContains(response, f'data-src="{reverse("player_stream", args=[self.book.pk])}"')

    def test_player_swap_contains_controls_but_not_audio(self):
        response = self.client.get(reverse("player_play", args=[self.book.pk]))
        self.assertContains(response, 'id="global-player"', count=1)
        self.assertContains(response, f'data-src="{reverse("player_stream", args=[self.book.pk])}"')
        self.assertNotContains(response, '<audio')

    def test_unpublished_book_cannot_be_played(self):
        Audiobook.objects.filter(pk=self.book.pk).update(published=False)
        response = self.client.get(reverse("player_play", args=[self.book.pk]))
        self.assertEqual(response.status_code, 404)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.book.audio_file.storage.save("audio/test.mp3", ContentFile(b"0123456789"))
        self.stream_url = reverse("player_stream", args=[self.book.pk])

    def test_stream_full_file(self):
        response = self.client.get(self.stream_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(response["Content-Length"], "10")
        self.assertEqual(b"".join(response.streaming_content), b"0123456789")
        response.close()

    def test_stream_byte_ranges(self):
        for header, expected, content_range in [
            ("bytes=2-5", b"2345", "bytes 2-5/10"),
            ("bytes=6-", b"6789", "bytes 6-9/10"),
            ("bytes=-3", b"789", "bytes 7-9/10"),
            ("bytes=7-999", b"789", "bytes 7-9/10"),
        ]:
            with self.subTest(header=header):
                response = self.client.get(self.stream_url, HTTP_RANGE=header)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response["Content-Range"], content_range)
                self.assertEqual(int(response["Content-Length"]), len(expected))
                self.assertEqual(b"".join(response.streaming_content), expected)
                response.close()

    def test_stream_unsatisfiable_ranges(self):
        for header in ["bytes=10-", "bytes=7-2", "bytes=-0"]:
            with self.subTest(header=header):
                response = self.client.get(self.stream_url, HTTP_RANGE=header)
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response["Content-Range"], "bytes */10")

    def test_stream_head(self):
        response = self.client.head(self.stream_url, HTTP_RANGE="bytes=2-5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Length"], "10")
        self.assertEqual(response.content, b"")

    def test_stream_unpublished_or_missing_audio(self):
        Audiobook.objects.filter(pk=self.book.pk).update(published=False)
        self.assertEqual(self.client.get(self.stream_url).status_code, 404)
        Audiobook.objects.filter(pk=self.book.pk).update(published=True, audio_file="audio/missing.mp3")
        self.assertEqual(self.client.get(self.stream_url).status_code, 404)

    def test_card_play_click_does_not_trigger_parent_htmx_navigation(self):
        response = self.client.get(reverse("search"))
        self.assertContains(response, 'hx-trigger="click consume"')
