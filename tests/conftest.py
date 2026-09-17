"""One Tcl interpreter per test process; each app owns a separate Toplevel."""
import pytest


@pytest.fixture(scope="session")
def tk_interpreter():
    tkinter = pytest.importorskip("tkinter")
    root = tkinter.Tk()
    root.withdraw()
    yield root
    root.destroy()
