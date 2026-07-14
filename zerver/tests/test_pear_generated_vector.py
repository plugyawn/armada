from unittest import TestCase

from zerver.lib.pear_generated_vector import (
    PearGeneratedVectorError,
    sanitize_pear_generated_vector,
)


class PearGeneratedVectorTest(TestCase):
    def test_penrose_vector_is_rebuilt_and_references_are_scoped(self) -> None:
        source = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50" penrose="0">
          <penrose><croppedViewBox>0 0 100 50</croppedViewBox></penrose>
          <defs><marker id="arrow`1"><path d="M0 0L5 2L0 4z"/></marker></defs>
          <line x1="0" y1="25" x2="90" y2="25" marker-end="url(#arrow`1)"/>
          <text x="4" y="18" style="font: 700 14px sans-serif; position: fixed">vector</text>
        </svg>"""
        sanitized = sanitize_pear_generated_vector(source).decode()
        self.assertNotIn("<penrose", sanitized)
        self.assertNotIn("position", sanitized)
        self.assertIn("font: 700 14px sans-serif", sanitized)
        self.assertIn('id="pear-vector-', sanitized)
        self.assertIn('marker-end="url(#pear-vector-', sanitized)
        self.assertIn(">vector</", sanitized)

    def test_active_content_and_external_resources_reject_the_document(self) -> None:
        hostile_sources = [
            '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><foreignObject/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path onclick="go()"/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path fill="url(https://example.com/x)"/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><use href="data:image/svg+xml,x"/></svg>',
        ]
        for source in hostile_sources:
            with self.subTest(source=source), self.assertRaises(PearGeneratedVectorError):
                sanitize_pear_generated_vector(source.encode())

    def test_malformed_oversized_and_unresolved_documents_fail_closed(self) -> None:
        with self.assertRaises(PearGeneratedVectorError):
            sanitize_pear_generated_vector(b"<svg>")
        with self.assertRaises(PearGeneratedVectorError):
            sanitize_pear_generated_vector(b"x" * 1_000_001)
        with self.assertRaises(PearGeneratedVectorError):
            sanitize_pear_generated_vector(
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path fill="url(#missing)"/></svg>'
            )
        nested = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
            + ("<g>" * 65)
            + ("</g>" * 65)
            + "</svg>"
        )
        with self.assertRaises(PearGeneratedVectorError):
            sanitize_pear_generated_vector(nested.encode())
        with self.assertRaises(PearGeneratedVectorError):
            sanitize_pear_generated_vector(
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><g id="same"/><g id="same"/></svg>'
            )

    def test_expensive_or_recursive_svg_features_are_not_admitted(self) -> None:
        source = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
          <defs><filter id="blur"><feGaussianBlur stdDeviation="999"/></filter></defs>
          <circle cx="5" cy="5" r="4" filter="url(#blur)"/>
        </svg>"""
        sanitized = sanitize_pear_generated_vector(source).decode()
        self.assertNotIn("filter", sanitized)
        self.assertNotIn("feGaussianBlur", sanitized)
