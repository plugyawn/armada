import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

from typing_extensions import override

from zerver.lib.armada_plugin_preview import (
    FETCH,
    HOLD,
    ArmadaPreviewError,
    Drawn,
    PreviewSite,
    Reply,
    _Browser,
    answer_request,
    draw_widget_previews,
)
from zerver.lib.test_classes import ZulipTestCase

FIXTURES = Path(__file__).parent / "fixtures" / "armada_plugins"
FAKE_CHROMIUM = FIXTURES / "fake_chromium.py"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ORIGIN = "https://armada.example"


def fake_chromium(directory: Path) -> str:
    """The fake browser as an executable, as a browser is."""
    path = directory / "chromium"
    path.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_CHROMIUM))} "$@"\n'
    )
    path.chmod(0o755)
    return str(path)


def queue_site(*, network: tuple[str, ...] = ()) -> PreviewSite:
    """A page with one widget in the nav, as register would give it."""
    manifest = json.loads((FIXTURES / "trial" / "armada-plugin.json").read_text())
    document = {
        "version": 1,
        "title": "Eval queue",
        "data": {"waiting": "12 waiting"},
        "rest": {"figure": "data.waiting", "sizer": "88 waiting"},
        "blocks": [
            {
                "type": "list",
                "label": "Waiting",
                "items": [{"key": "mmlu", "label": "mmlu-5shot", "figure": "4m"}],
            }
        ],
    }
    register = {
        "queue_id": "preview",
        "armada_plugins": [
            {
                "id": 7,
                "widgets": manifest["provides"]["widgets"],
                "widget_defaults": {"queue": {"nav": "shown", "column": False}},
            }
        ],
        "armada_widgets": [{"widget": "queue", "document": document}],
    }
    return PreviewSite(
        page_url=f"{ORIGIN}/",
        page_html='<div id="beta-data"></div>',
        answers={"register": register},
        network=network,
    )


class PreviewTest(ZulipTestCase):
    """The preview's browser (zerver/lib/armada_plugin_preview.py), with a
    fake Chromium that speaks the DevTools protocol through the same pipe
    and draws nothing (zerver/tests/fixtures/armada_plugins/fake_chromium.py)."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.log = self.root / "chromium.log"
        self.chromium_path = fake_chromium(self.root)

    @contextmanager
    def fake(self, behaviour: str = "", requests: list[str] | None = None) -> Iterator[None]:
        with mock.patch.dict(
            os.environ,
            {
                "FAKE_CHROMIUM": behaviour,
                "FAKE_CHROMIUM_LOG": str(self.log),
                "FAKE_CHROMIUM_REQUESTS": json.dumps(
                    [f"{ORIGIN}/json/register"] if requests is None else requests
                ),
            },
        ):
            yield

    def draw(self, site: PreviewSite | None = None, widgets: list[str] | None = None) -> Drawn:
        return draw_widget_previews(
            site or queue_site(),
            browser=self.chromium_path,
            widgets=["7/queue"] if widgets is None else widgets,
            out_dir=self.root / "previews",
            name="queue",
        )

    def chromium(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_rows_and_sheets_are_pictured_in_light_and_dark(self) -> None:
        requests = [
            f"{ORIGIN}/json/register",
            f"{ORIGIN}/json/events",
            "http://localhost:9991/webpack/app.js",
        ]
        with self.fake(requests=requests):
            drawn = self.draw(queue_site(network=("http://localhost:9991/webpack/",)))
        self.assertEqual(
            [picture.as_dict() for picture in drawn.pictures],
            [
                {
                    "widget": None,
                    "state": "rest",
                    "theme": "light",
                    "path": str(self.root / "previews" / "queue-rest-light.png"),
                },
                {
                    "widget": "queue",
                    "state": "pressed",
                    "theme": "light",
                    "path": str(self.root / "previews" / "queue-queue-pressed-light.png"),
                },
                {
                    "widget": None,
                    "state": "rest",
                    "theme": "dark",
                    "path": str(self.root / "previews" / "queue-rest-dark.png"),
                },
                {
                    "widget": "queue",
                    "state": "pressed",
                    "theme": "dark",
                    "path": str(self.root / "previews" / "queue-queue-pressed-dark.png"),
                },
            ],
        )
        for picture in drawn.pictures:
            self.assertEqual(picture.path.read_bytes()[:8], PNG_SIGNATURE)
        assert drawn.rows is not None
        self.assertEqual(
            drawn.rows["rows"],
            [
                {
                    "id": "7/queue",
                    "box": {"x": 8, "y": 713, "width": 216, "height": 28},
                    "label": "Eval queue",
                    "label_cut": False,
                    "figure": "12 waiting",
                    "figure_cut": False,
                }
            ],
        )
        self.assertEqual(list(drawn.sheets), ["7/queue"])
        self.assertEqual(drawn.notes, [])
        log = self.chromium()
        answers = {entry["request"]: entry for entry in log if "answer" in entry}
        asked = {entry["url"]: entry["request"] for entry in log if "url" in entry}
        self.assertEqual(answers[asked[f"{ORIGIN}/"]]["body"], '<div id="beta-data"></div>')
        self.assertNotIn(asked[f"{ORIGIN}/json/events"], answers)
        self.assertEqual(
            answers[asked["http://localhost:9991/webpack/app.js"]]["answer"],
            "Fetch.continueRequest",
        )
        self.assertEqual(
            next(entry["media"] for entry in log if "media" in entry),
            [
                {"name": "prefers-color-scheme", "value": "light"},
                {"name": "prefers-reduced-motion", "value": "reduce"},
            ],
        )
        self.assertEqual(
            [entry["press"] for entry in log if "press" in entry][:3],
            [["mouseMoved", 116, 727], ["mousePressed", 116, 727], ["mouseReleased", 116, 727]],
        )
        # The nav's foot, then the same with the sheet beside it.
        self.assertEqual(
            [entry["picture"] for entry in log if "picture" in entry][:2],
            [
                {"x": 0, "y": 701, "width": 244, "height": 199, "scale": 1},
                {"x": 0, "y": 485, "width": 596, "height": 415, "scale": 1},
            ],
        )
        self.assertTrue(log[-1]["closed"])

    def test_what_the_page_did_not_draw_is_said(self) -> None:
        with self.fake():
            drawn = self.draw(widgets=["7/queue", "7/other"])
        self.assertEqual(
            drawn.notes, ["The nav has no row for 7/other.", "The nav has no row for 7/other."]
        )
        for behaviour, notes in (
            ("blank", ["The page drew no widget rows in the nav."]),
            (
                "nosheet",
                [
                    "Pressing 7/queue's row opened no sheet.",
                    "Pressing 7/queue's row opened no sheet.",
                ],
            ),
            ("throws", ["The page threw: TypeError: x is null"]),
        ):
            with self.fake(behaviour):
                self.assertEqual(self.draw().notes, notes, behaviour)

    def test_a_browser_that_fails_says_how(self) -> None:
        for behaviour, message in (
            ("crash", r"^The browser stopped\.$"),
            ("error", r"^Runtime\.evaluate: Target closed$"),
            ("exception", r"^The page could not be measured: Uncaught$"),
        ):
            with self.fake(behaviour), self.assertRaisesRegex(ArmadaPreviewError, message):
                self.draw()
        with (
            self.fake("slow"),
            mock.patch("zerver.lib.armada_plugin_preview.PREVIEW_SECONDS", 1),
            self.assertRaisesRegex(ArmadaPreviewError, r"^The browser took too long\.$"),
        ):
            self.draw()
        with (
            self.fake(),
            self.assertRaisesRegex(
                ArmadaPreviewError, r"^The browser stopped\. It said: .*No such file or directory"
            ),
        ):
            draw_widget_previews(
                queue_site(),
                browser="/nonexistent/chrome",
                widgets=[],
                out_dir=self.root,
                name="queue",
            )
        # Writing to a browser that is gone.
        with self.fake("crash"):
            browser = _Browser(self.chromium_path, deadline=time.monotonic() + 10)
            browser.process.wait()
            with self.assertRaisesRegex(ArmadaPreviewError, r"^The browser stopped\.$"):
                browser.send("Browser.getVersion", {"padding": "x" * 200_000})
            browser.close()

    def test_what_each_request_is_answered_with(self) -> None:
        static = self.root / "static"
        (static / "webpack-bundles").mkdir(parents=True)
        (static / "webpack-bundles" / "beta.js").write_text("ok")
        (self.root / "secret.txt").write_text("no")
        site = PreviewSite(
            page_url=f"{ORIGIN}/",
            page_html="<p>page</p>",
            answers={"register": {"queue_id": "preview"}},
            files=((f"{ORIGIN}/static/", str(static)),),
            network=("http://localhost:9991/webpack/",),
        )
        self.assertEqual(PreviewSite.from_dict(json.loads(json.dumps(site.as_dict()))), site)
        cases: list[tuple[str, Reply | str]] = [
            (f"{ORIGIN}/#narrow/channel/9", Reply(200, "text/html; charset=utf-8", b"<p>page</p>")),
            (f"{ORIGIN}/json/register", Reply(200, "application/json", b'{"queue_id": "preview"}')),
            (f"{ORIGIN}/json/events?queue_id=preview", HOLD),
            (
                f"{ORIGIN}/json/armada/runs",
                Reply(200, "application/json", b'{"result": "success", "msg": ""}'),
            ),
            ("https://elsewhere.example/json/register", Reply(404, "text/plain", b"")),
            (f"{ORIGIN}/static/webpack-bundles/beta.js?v=1", Reply(200, "text/javascript", b"ok")),
            (f"{ORIGIN}/static/webpack-bundles/gone.js", Reply(404, "text/plain", b"")),
            (f"{ORIGIN}/static/..%2Fsecret.txt", Reply(404, "text/plain", b"")),
            ("http://localhost:9991/webpack/app.js", FETCH),
            (f"{ORIGIN}/user_avatars/1.png", Reply(404, "text/plain", b"")),
        ]
        for url, answer in cases:
            self.assertEqual(answer_request(site, url), answer, url)
