from typing import Any
from unittest import mock

from django.http import HttpRequest
from django.template.loader import get_template

from zerver.lib.exceptions import InvalidMarkdownIncludeStatementError
from zerver.lib.templates import armada_palette, font_preloads
from zerver.lib.test_classes import ZulipTestCase


class TemplateTestCase(ZulipTestCase):
    def test_markdown_in_template(self) -> None:
        template = get_template("tests/test_markdown.html")
        context = {
            "markdown_test_file": "zerver/tests/markdown/test_markdown.md",
        }
        content = template.render(context)

        content_sans_whitespace = content.replace(" ", "").replace("\n", "")
        self.assertEqual(
            content_sans_whitespace,
            'header<h1id="hello">Hello!</h1><p>Thisissome<em>boldtext</em>.</p>footer',
        )

    def test_markdown_tabbed_sections_extension(self) -> None:
        template = get_template("tests/test_markdown.html")
        context = {
            "markdown_test_file": "zerver/tests/markdown/test_tabbed_sections.md",
        }
        content = template.render(context)
        content_sans_whitespace = content.replace(" ", "").replace("\n", "")

        # Note that the expected HTML has a lot of stray <p> tags. This is a
        # consequence of how the Markdown renderer converts newlines to HTML
        # and how elements are delimited by newlines and so forth. However,
        # stray <p> tags are usually matched with closing tags by HTML renderers
        # so this doesn't affect the final rendered UI in any visible way.
        expected_html = """
header

<h1 id="heading">Heading</h1>
<p>
  <div class="tabbed-section has-tabs" markdown="1">
    <ul class="nav">
      <li class="active" data-tab-key="ios" tabindex="0">iOS</li>
      <li class="" data-tab-key="desktop-web" tabindex="0">Desktop/Web</li>
    </ul>
    <div class="blocks">
      <div class="tab-content active" data-tab-key="ios" markdown="1"></p>
        <p>iOS instructions</p>
      <p></div>
      <div class="tab-content" data-tab-key="desktop-web" markdown="1"></p>
        <p>Desktop/browser instructions</p>
      <p></div>
    </div>
  </div>
</p>

<h2 id="heading-2">Heading 2</h2>
<p>
  <div class="tabbed-section has-tabs" markdown="1">
    <ul class="nav">
      <li class="active" data-tab-key="desktop-web" tabindex="0">Desktop/Web</li>
      <li class="" data-tab-key="android" tabindex="0">Android</li>
    </ul>
    <div class="blocks">
      <div class="tab-content active" data-tab-key="desktop-web" markdown="1"></p>
        <p>Desktop/browser instructions</p>
      <p></div>
      <div class="tab-content" data-tab-key="android" markdown="1"></p>
        <p>Android instructions</p>
      <p></div>
    </div>
  </div>
</p>

<h2 id="heading-3">Heading 3</h2>
<p>
  <div class="tabbed-section no-tabs" markdown="1">
    <ul class="nav">
      <li class="active" data-tab-key="instructions-for-all-platforms" tabindex="0">Instructions for all platforms</li>
    </ul>
    <div class="blocks">
      <div class="tab-content active" data-tab-key="instructions-for-all-platforms" markdown="1"></p>
        <p>Instructions for all platforms</p>
      <p></div>
    </div>
  </div>
</p>

footer
"""

        expected_html_sans_whitespace = expected_html.replace(" ", "").replace("\n", "")
        self.assertEqual(content_sans_whitespace, expected_html_sans_whitespace)

    def test_markdown_tabbed_sections_missing_tabs(self) -> None:
        template = get_template("tests/test_markdown.html")
        context = {
            "markdown_test_file": "zerver/tests/markdown/test_tabbed_sections_missing_tabs.md",
        }
        expected_regex = "^Tab 'minix' is not present in TAB_SECTION_LABELS in zerver/lib/markdown/tabbed_sections.py$"
        with self.assertRaisesRegex(ValueError, expected_regex):
            template.render(context)

    def test_markdown_nested_code_blocks(self) -> None:
        template = get_template("tests/test_markdown.html")
        context = {
            "markdown_test_file": "zerver/tests/markdown/test_nested_code_blocks.md",
        }
        content = template.render(context)

        content_sans_whitespace = content.replace(" ", "").replace("\n", "")
        expected = (
            'header<h1id="this-is-a-heading">Thisisaheading.</h1><ol>'
            '<li><p>Alistitemwithanindentedcodeblock:</p><divclass="codehilite">'
            "<pre>indentedcodeblockwithmultiplelines</pre></div></li></ol>"
            '<divclass="codehilite"><pre><span></span><code>'
            "non-indentedcodeblockwithmultiplelines</code></pre></div>footer"
        )
        self.assertEqual(content_sans_whitespace, expected)

    def test_custom_markdown_include_extension(self) -> None:
        template = get_template("tests/test_markdown.html")
        context = {
            "markdown_test_file": "zerver/tests/markdown/test_custom_include_extension.md",
        }

        with self.assertRaisesRegex(
            InvalidMarkdownIncludeStatementError, "Invalid Markdown include statement"
        ):
            template.render(context)

    def test_custom_markdown_include_extension_empty_macro(self) -> None:
        template = get_template("tests/test_markdown.html")
        context = {
            "markdown_test_file": "zerver/tests/markdown/test_custom_include_extension_empty.md",
        }
        content = template.render(context)
        content_sans_whitespace = content.replace(" ", "").replace("\n", "")
        expected = "headerfooter"
        self.assertEqual(content_sans_whitespace, expected)


def webpack_stats_with_fonts(*source_filenames: str) -> dict[str, Any]:
    # The shape webpack-bundle-tracker writes: each asset by its built
    # name, with the file it was built from.
    assets = {}
    for source_filename in source_filenames:
        name = "files/" + source_filename.rsplit("/", 1)[-1]
        assets[name] = {"name": name, "sourceFilename": source_filename}
    return {"status": "done", "assets": assets}


class ArmadaFontPreloadsTest(ZulipTestCase):
    OPEN_FONTS = (
        "fonts/armada/GeistMonoVF.woff2",
        "fonts/armada/HankenGrotesk-VF.woff2",
        "fonts/armada/HankenGrotesk-Italic-VF.woff2",
    )

    def preloaded_files(self, entrypoint: str, *source_filenames: str) -> list[str]:
        with mock.patch(
            "zerver.lib.templates.webpack_stats",
            return_value=webpack_stats_with_fonts(*source_filenames),
        ):
            urls = font_preloads(entrypoint)
        for url in urls:
            self.assertIn("/static/webpack-bundles/files/", url)
        return [url.rsplit("/", 1)[-1] for url in urls]

    def test_app_with_licensed_files(self) -> None:
        # Söhne draws everything, so Hanken Grotesk is not fetched; the
        # italic loads on use.
        self.assertEqual(
            self.preloaded_files(
                "app",
                *self.OPEN_FONTS,
                "../fonts/licensed/soehne-buch.woff2",
                "../fonts/licensed/soehne-buch-kursiv.woff2",
                "../fonts/licensed/soehne-kraftig.woff2",
                "../fonts/licensed/soehne-halbfett.woff2",
            ),
            [
                "soehne-buch.woff2",
                "soehne-halbfett.woff2",
                "soehne-kraftig.woff2",
                "GeistMonoVF.woff2",
            ],
        )

    def test_app_with_test_files(self) -> None:
        # Klim's test files hold no punctuation, which Hanken Grotesk draws.
        self.assertEqual(
            self.preloaded_files(
                "app",
                *self.OPEN_FONTS,
                "../fonts/licensed/test-soehne-buch.woff2",
                "../fonts/licensed/test-soehne-buch-kursiv.woff2",
                "../fonts/licensed/test-soehne-kraftig.woff2",
                "../fonts/licensed/test-soehne-halbfett.woff2",
            ),
            [
                "test-soehne-buch.woff2",
                "test-soehne-halbfett.woff2",
                "test-soehne-kraftig.woff2",
                "GeistMonoVF.woff2",
                "HankenGrotesk-VF.woff2",
            ],
        )

    def test_app_without_soehne(self) -> None:
        self.assertEqual(
            self.preloaded_files("app", *self.OPEN_FONTS),
            ["GeistMonoVF.woff2", "HankenGrotesk-VF.woff2"],
        )

    def test_page_outside_the_app(self) -> None:
        # Sign-in and its neighbours are set in Hanken Grotesk alone,
        # whichever files of Söhne the machine has.
        for entrypoint in ["signup", "dev-login", "common"]:
            self.assertEqual(
                self.preloaded_files(
                    entrypoint, *self.OPEN_FONTS, "../fonts/licensed/soehne-buch.woff2"
                ),
                ["HankenGrotesk-VF.woff2"],
            )

    def test_pages_before_the_app(self) -> None:
        # Sign-in and its neighbours are set in Söhne, with no code for
        # Geist Mono to draw.
        for entrypoint in ["armada-portico", "desktop-login", "desktop-redirect"]:
            self.assertEqual(
                self.preloaded_files(
                    entrypoint,
                    *self.OPEN_FONTS,
                    "../fonts/licensed/soehne-buch.woff2",
                    "../fonts/licensed/soehne-kraftig.woff2",
                    "../fonts/licensed/soehne-halbfett.woff2",
                ),
                ["soehne-buch.woff2", "soehne-halbfett.woff2", "soehne-kraftig.woff2"],
            )
            self.assertEqual(
                self.preloaded_files(entrypoint, *self.OPEN_FONTS),
                ["HankenGrotesk-VF.woff2"],
            )

    def test_stats_without_assets(self) -> None:
        # The stub stats file of the test suite lists no asset.
        with mock.patch("zerver.lib.templates.webpack_stats", return_value={"status": "done"}):
            self.assertEqual(font_preloads("app"), [])
        self.assertEqual(self.preloaded_files("app", "images/logo.svg"), [])

    def test_login_page_preloads(self) -> None:
        with mock.patch(
            "zerver.lib.templates.webpack_stats",
            return_value={
                **webpack_stats_with_fonts(*self.OPEN_FONTS, "../fonts/licensed/soehne-buch.woff2"),
                "chunks": {"armada-portico": []},
            },
        ):
            result = self.client_get("/login/")
        self.assertEqual(result.status_code, 200)
        self.assert_in_response(
            '<link rel="preload" href="http://testserver/static/webpack-bundles/files/soehne-buch.woff2"'
            ' as="font" type="font/woff2" crossorigin="anonymous" />',
            result,
        )
        self.assertEqual(result.content.decode().count('rel="preload"'), 1)


class ArmadaPaletteTest(ZulipTestCase):
    def request_with(self, **query: str) -> HttpRequest:
        request = HttpRequest()
        request.GET.update(query)
        return request

    def test_setting(self) -> None:
        self.assertEqual(armada_palette(), "lapis")
        with self.settings(ARMADA_PALETTE="tide"):
            self.assertEqual(armada_palette(), "tide")
            self.assertEqual(armada_palette(self.request_with()), "tide")
        with self.settings(ARMADA_PALETTE="mauve"):
            self.assertEqual(armada_palette(), "lapis")

    def test_query_parameter_in_development_only(self) -> None:
        request = self.request_with(p="onyx")
        with self.settings(DEVELOPMENT=True):
            self.assertEqual(armada_palette(request), "onyx")
            self.assertEqual(armada_palette(self.request_with(p="mauve")), "lapis")
        with self.settings(DEVELOPMENT=False):
            self.assertEqual(armada_palette(request), "lapis")

    def test_root_element(self) -> None:
        with self.settings(ARMADA_PALETTE="citrine"):
            result = self.client_get("/login/")
        self.assert_in_response('data-palette="citrine"', result)
