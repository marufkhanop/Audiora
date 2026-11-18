from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import PlaybackProgress
from audiobooks.models import Audiobook

User = get_user_model()


class AccountFlowTests(TestCase):
    def setUp(self):
        self.book = Audiobook.objects.create(
            title="Resume me",
            author="Test author",
            narrator="Test narrator",
            description="A book.",
            audio_file="audio/test.mp3",
            cover="covers/test.jpg",
            duration=600,
            published=True,
        )

    def test_signup_with_gmail_creates_and_logs_in(self):
        response = self.client.post(
            reverse("accounts:signup"),
            {
                "email": "Reader@Gmail.com",
                "password": "a-long-password",
                "confirm": "a-long-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="reader@gmail.com")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_signup_rejects_duplicate_email(self):
        User.objects.create_user(
            username="taken@gmail.com", email="taken@gmail.com", password="x"
        )
        response = self.client.post(
            reverse("accounts:signup"),
            {
                "email": "taken@gmail.com",
                "password": "a-long-password",
                "confirm": "a-long-password",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.filter(email__iexact="taken@gmail.com").count(), 1)

    def test_login_accepts_email_and_username(self):
        user = User.objects.create_user(
            username="listener", email="listener@gmail.com", password="secret-pass"
        )
        for identifier in ("listener@gmail.com", "listener"):
            with self.subTest(identifier=identifier):
                self.client.logout()
                response = self.client.post(
                    reverse("accounts:login"),
                    {"email": identifier, "password": "secret-pass"},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_login_rejects_bad_password(self):
        User.objects.create_user(
            username="listener", email="listener@gmail.com", password="secret-pass"
        )
        response = self.client.post(
            reverse("accounts:login"),
            {"email": "listener@gmail.com", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_post(self):
        User.objects.create_user(username="u", email="u@gmail.com", password="pw")
        self.client.login(username="u", password="pw")
        self.assertEqual(
            self.client.get(reverse("accounts:logout")).status_code, 405
        )
        self.assertEqual(
            self.client.post(reverse("accounts:logout")).status_code, 302
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_progress_endpoint_requires_post_and_account(self):
        url = reverse("player_progress", args=[self.book.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {"position": 42, "duration": 600})
        self.assertJSONEqual(response.content, {"saved": False, "reason": "anonymous"})
        self.assertFalse(PlaybackProgress.objects.exists())

    def test_progress_endpoint_records_position(self):
        user = User.objects.create_user(username="u", email="u@gmail.com", password="pw")
        self.client.force_login(user)
        url = reverse("player_progress", args=[self.book.pk])
        for position in (30, 90):
            response = self.client.post(url, {"position": position, "duration": 600})
            self.assertJSONEqual(response.content, {"saved": True})
        progress = PlaybackProgress.objects.get(user=user, book=self.book)
        self.assertEqual(progress.position, 90)
        self.assertFalse(progress.completed)

    def test_completed_progress_is_marked(self):
        user = User.objects.create_user(username="u", email="u@gmail.com", password="pw")
        self.client.force_login(user)
        self.client.post(
            reverse("player_progress", args=[self.book.pk]),
            {"position": 600, "duration": 600, "completed": "1"},
        )
        self.assertTrue(PlaybackProgress.objects.get(user=user).completed)

    @override_settings(GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="secret")
    def test_google_sign_in_button_shown_when_configured(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, "Continue with Google")

    def test_google_sign_in_button_hidden_when_unconfigured(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertNotContains(response, "Continue with Google")

    @override_settings(GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="secret")
    def test_google_start_redirects_with_state(self):
        response = self.client.get(reverse("accounts:google_start"))
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(location.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertIn("client_id=client-id", location)
        self.assertIn("scope=openid", location)
        self.assertIn("google_state", self.client.session)

    def test_google_callback_rejects_bad_state(self):
        response = self.client.get(
            reverse("accounts:google_callback"), {"state": "forged", "code": "x"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("accounts:login"))

    def test_unpublished_book_cannot_store_progress(self):
        user = User.objects.create_user(username="u", email="u@gmail.com", password="pw")
        self.client.force_login(user)
        Audiobook.objects.filter(pk=self.book.pk).update(published=False)
        response = self.client.post(
            reverse("player_progress", args=[self.book.pk]),
            {"position": 10, "duration": 600},
        )
        self.assertEqual(response.status_code, 404)


class RecentAndResumeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="reader", email="reader@gmail.com", password="pw"
        )
        self.book = Audiobook.objects.create(
            title="Recent book",
            author="Author",
            narrator="Narrator",
            description="A book.",
            audio_file="audio/test.mp3",
            cover="covers/test.jpg",
            duration=600,
            published=True,
        )
        self.other = Audiobook.objects.create(
            title="Older book",
            author="Author",
            narrator="Narrator",
            description="Another book.",
            audio_file="audio/test2.mp3",
            cover="covers/test2.jpg",
            duration=300,
            published=True,
        )

    def test_recent_page_requires_sign_in_prompt(self):
        response = self.client.get(reverse("recent"))
        self.assertContains(response, "Create account")
        self.assertNotContains(response, "Recent book")

    def test_recent_page_lists_books_newest_first(self):
        PlaybackProgress.record(self.user, self.other, 100)
        PlaybackProgress.record(self.user, self.book, 45)
        self.client.force_login(self.user)
        response = self.client.get(reverse("recent"))
        content = response.content.decode()
        self.assertIn("Recent book", content)
        self.assertIn("Resume 0:45", content)
        self.assertLess(
            content.index("Recent book"), content.index("Older book")
        )

    def test_recent_page_is_htmx_partial(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("recent"), HTTP_HX_REQUEST="true")
        self.assertTemplateUsed(response, "audiobooks/partials/recent_main.html")
        self.assertNotContains(response, "<html")

    def test_header_offers_login_and_signup_to_guests(self):
        response = self.client.get("/")
        self.assertContains(response, reverse("accounts:login"))
        self.assertContains(response, reverse("accounts:signup"))
        self.assertContains(response, "Sign up")
        self.assertContains(response, 'href="/admin/"', count=0)

    def test_header_shows_account_and_logout_when_signed_in(self):
        self.client.force_login(self.user)
        response = self.client.get("/")
        self.assertContains(response, reverse("accounts:logout"))
        self.assertContains(response, "Log out")
        self.assertContains(response, "reader@gmail.com")
        self.assertContains(response, reverse("recent"))

    def test_home_page_restores_last_book_into_player(self):
        PlaybackProgress.record(self.user, self.book, 120)
        self.client.force_login(self.user)
        response = self.client.get("/")
        self.assertContains(
            response, f'data-src="{reverse("player_stream", args=[self.book.pk])}"'
        )
        self.assertContains(response, 'data-position="120"')
        self.assertContains(
            response, f'data-progress-url="{reverse("player_progress", args=[self.book.pk])}"'
        )

    def test_book_detail_seeds_saved_position(self):
        PlaybackProgress.record(self.user, self.book, 90)
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("book_detail", args=[self.book.slug])
        )
        self.assertContains(response, 'data-position="90"')

    def test_play_endpoint_starts_from_saved_position(self):
        PlaybackProgress.record(self.user, self.book, 55)
        self.client.force_login(self.user)
        response = self.client.get(reverse("player_play", args=[self.book.pk]))
        self.assertContains(response, 'data-position="55"')

    def test_guest_home_has_no_resume_track(self):
        response = self.client.get("/")
        self.assertContains(response, 'id="global-player"')
        self.assertContains(response, 'data-src=""')
        self.assertContains(response, 'data-progress-url=""')

    def test_completed_book_reports_play_again(self):
        PlaybackProgress.record(self.user, self.book, 600, completed=True)
        self.client.force_login(self.user)
        response = self.client.get(reverse("recent"))
        self.assertContains(response, "Play again")
