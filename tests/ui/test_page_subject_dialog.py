from pathlib import Path

import pytest
from PIL import Image

from qingzi_learning.domain import Subject
from qingzi_learning.ui.page_subject_dialog import PageSubjectDialog, dialog_geometry
from qingzi_learning.workflow.subject_split import PendingPageSubject


def test_dialog_uses_eighty_five_percent_of_work_area():
    assert dialog_geometry(1920, 1080) == (1632, 918)
    assert dialog_geometry(1024, 700) == (870, 595)


def test_dialog_does_not_exceed_work_area_when_minimum_cannot_fit():
    assert dialog_geometry(600, 400) == (510, 340)


@pytest.fixture
def tk_root():
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("Tk display is unavailable")
    root.geometry("1024x700+20+20")
    root.update()
    yield root
    root.destroy()


@pytest.fixture
def pending_item(tmp_path):
    path = tmp_path / "page_002.png"
    Image.new("RGB", (1400, 900), "white").save(path)
    return PendingPageSubject(2, path, Subject.MATH, .55, "含有公式")


def test_layout_keeps_footer_inside_small_window(tk_root, pending_item):
    dialog = PageSubjectDialog(tk_root, on_choose=lambda page, subject: None,
                               on_defer=lambda: None)
    dialog.show(pending_item, index=1, total=2)
    dialog.window.geometry("870x595")
    dialog.window.update_idletasks()

    assert dialog.footer.winfo_y() + dialog.footer.winfo_height() <= dialog.window.winfo_height()
    assert all(button.winfo_viewable() for button in dialog.subject_buttons)
    assert max(button.winfo_width() for button in dialog.subject_buttons) - min(
        button.winfo_width() for button in dialog.subject_buttons
    ) <= 1

    dialog.destroy()


def test_footer_survives_large_and_maximized_then_restored_layout(tk_root, pending_item):
    dialog = PageSubjectDialog(tk_root, on_choose=lambda page, subject: None,
                               on_defer=lambda: None)
    dialog.show(pending_item, index=1, total=2)

    for geometry in ("870x595", "1632x918"):
        dialog.window.geometry(geometry)
        dialog.window.update()
        assert dialog.footer.winfo_y() + dialog.footer.winfo_height() <= dialog.window.winfo_height()
        assert all(button.winfo_viewable() for button in dialog.subject_buttons)

    dialog.window.state("zoomed")
    dialog.window.update()
    assert dialog.footer.winfo_y() + dialog.footer.winfo_height() <= dialog.window.winfo_height()
    assert all(button.winfo_viewable() for button in dialog.subject_buttons)

    dialog.window.state("normal")
    dialog.window.geometry("870x595")
    dialog.window.update()
    assert dialog.footer.winfo_y() + dialog.footer.winfo_height() <= dialog.window.winfo_height()
    assert all(button.winfo_viewable() for button in dialog.subject_buttons)
    dialog.destroy()


def test_close_defers_without_confirming(tk_root, pending_item):
    chosen = []
    deferred = []
    dialog = PageSubjectDialog(tk_root, on_choose=lambda page, subject: chosen.append((page, subject)),
                               on_defer=lambda: deferred.append(True))
    dialog.show(pending_item, index=1, total=1)

    dialog.close()

    assert deferred == [True]
    assert chosen == []


def test_show_acquires_modal_grab_and_destroy_releases_it(tk_root, pending_item):
    dialog = PageSubjectDialog(tk_root, on_choose=lambda page, subject: None,
                               on_defer=lambda: None)
    dialog.show(pending_item, index=1, total=1)
    dialog.window.update()

    assert dialog.window.grab_current() == dialog.window

    dialog.destroy()

    assert tk_root.grab_current() is None


def test_hide_releases_modal_grab(tk_root, pending_item):
    dialog = PageSubjectDialog(tk_root, on_choose=lambda page, subject: None,
                               on_defer=lambda: None)
    dialog.show(pending_item, index=1, total=1)
    dialog.window.update()

    dialog.hide()

    assert tk_root.grab_current() is None
    dialog.destroy()
