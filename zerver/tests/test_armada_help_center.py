# The help center documents Zulip and is no longer served (S-02 in
# docs/armada/cuts.md). In production nginx answers /help with the
# redirect (puppet/zulip/files/nginx/zulip-include-frontend/app);
# zproject/dev_urls.py answers the same in development and here.
from zerver.lib.test_classes import ZulipTestCase


class HelpCenterNotServedTest(ZulipTestCase):
    def test_help_center_redirects_to_root(self) -> None:
        for url in [
            "/help",
            "/help/",
            "/help/some-article",
            "/help/some-article/",
            "/help/some-article?raw",
            "/help/getting-started/some-section",
        ]:
            result = self.client_get(url)
            # Temporary, so that an Armada help page can take the path back.
            self.assertEqual(result.status_code, 302, url)
            self.assertEqual(result["Location"], "/", url)

    def test_help_center_redirect_is_the_same_signed_in(self) -> None:
        self.login("hamlet")
        result = self.client_get("/help/keyboard-shortcuts")
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], "/")

    def test_paths_that_only_start_with_help_are_not_redirected(self) -> None:
        result = self.client_get("/helpful")
        self.assertEqual(result.status_code, 404)

    def test_old_help_redirect_leads_to_root(self) -> None:
        # An old API documentation URL that moved to the help center
        # (REDIRECTED_TO_HELP_DOCUMENTATION) keeps its permanent redirect;
        # the help center's own temporary redirect then leads to /.
        result = self.client_get("/api/outgoing-webhooks")
        self.assertEqual(result.status_code, 301)
        self.assertEqual(result["Location"], "/help/outgoing-webhooks")

        result = self.client_get(result["Location"])
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], "/")
