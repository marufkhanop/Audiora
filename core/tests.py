from django.test import TestCase

from audiobooks.models import Audiobook, Category


class GuestHomeLayoutTests(TestCase):
    """The guest home must render its full body for #main-content swaps."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Fiction")
        cls.book = Audiobook.objects.create(
            title="Test Book",
            author="Test Author",
            narrator="Test Narrator",
            description="A test audiobook.",
            category=cls.category,
            published=True,
        )

    def test_full_page_has_hero_search_and_library(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search audiobooks")
        self.assertContains(response, 'id="guest-library"')
        self.assertContains(response, self.book.title)

    def test_main_content_swap_gets_complete_home_body(self):
        response = self.client.get(
            "/", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="main-content"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search audiobooks")
        self.assertContains(response, 'id="guest-library"')
        self.assertNotContains(response, "<html")

    def test_library_swap_gets_only_the_library_fragment(self):
        response = self.client.get(
            "/", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="guest-library"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Search audiobooks")
        self.assertNotContains(response, 'id="guest-library"')
        self.assertContains(response, 'role="tablist"')
