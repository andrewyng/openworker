"""The line between filling a form and sending it.

`browser_click` can fill in a whole application; it must not be able to file one. Submitting
is the single action in that sequence that cannot be undone — it lands in an employer's ATS
under the user's name — so it lives in its own tool, `browser_submit`, which an automation's
`always_allowed_tools` can withhold while still granting everything needed to fill the form.

These tests pin the two halves of that contract: the guard fires on the controls that send,
and stays out of the way of the controls that do not (an "Apply" button opens the form — a
gate there would park an approval on a plain navigation).
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")

from coworker.connectors.browser_automation import (  # noqa: E402
    _SUBMIT_TEXT,
    _submit_intent,
    make_browser_automation_tools,
)
from coworker.connectors.tool_defs import TOOL_DEFS  # noqa: E402


class _Locator:
    """A locator whose element probe answers with a canned description."""

    def __init__(self, info):
        self._info = info

    def evaluate(self, _js):
        if isinstance(self._info, Exception):
            raise self._info
        return self._info


def _el(tag="button", type_="", text="", role=""):
    buttonish = tag == "button" or role == "button" or (
        tag == "input" and type_ in ("submit", "button", "image")
    )
    return {"tag": tag, "type": type_, "buttonish": buttonish, "text": text}


@pytest.mark.parametrize(
    "text",
    ["Submit", "Submit Application", "Send", "Send my application", "Finish",
     "Complete Application"],
)
def test_sending_controls_are_gated(text):
    assert _submit_intent(_Locator(_el(text=text)))


@pytest.mark.parametrize(
    "text",
    ["Apply", "Apply for this Job", "Next", "Continue", "Save draft", "Upload File",
     "Add another link"],
)
def test_controls_that_do_not_send_are_not_gated(text):
    """'Apply' opens the form. Gating it would put an approval in front of a navigation."""
    assert not _submit_intent(_Locator(_el(text=text)))


def test_a_bare_submit_input_is_gated_without_any_label():
    assert _submit_intent(_Locator(_el(tag="input", type_="submit")))


def test_non_buttons_are_never_gated():
    """Typing into a field whose label happens to say "Submit your résumé" is not a send."""
    assert not _submit_intent(_Locator(_el(tag="input", type_="text", text="Submit a link")))
    assert not _submit_intent(_Locator(_el(tag="a", text="Submit feedback")))


def test_an_unreadable_element_is_not_gated_rather_than_raising():
    """The guard adds a stop, not a new failure mode: a detached node answers 'not a send'."""
    assert _submit_intent(_Locator(RuntimeError("element is detached"))) == ""
    assert _submit_intent(_Locator(None)) == ""


def test_submit_is_its_own_tool_so_it_can_be_withheld():
    names = [t.__name__ for t in make_browser_automation_tools()]
    assert "browser_submit" in names
    assert "browser_click" in names
    declared = {d.name: d for d in TOOL_DEFS if d.connector == "browser"}
    assert declared["browser_submit"].kind == "write"


def test_apply_is_not_a_trigger_word():
    """Pinned separately from the parametrized cases: this one is easy to 'fix' by mistake."""
    assert not _SUBMIT_TEXT.search("Apply for this Job")
    assert not _SUBMIT_TEXT.search("Apply Now")
