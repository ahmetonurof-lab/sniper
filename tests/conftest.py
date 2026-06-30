import logging
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


@pytest.fixture(autouse=True)
def _isolate_logging():
    """Test'lerin paper_trade.log'a yazmasını engelle.

    _setup_logging() artık modül seviyesinde çağrılmadığı için
    import sırasında root logger boştur. Bu fixture root'a
    NullHandler ekleyerek log'ların stderr'e bile düşmesini engeller.
    """
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    root.handlers.clear()
    root.addHandler(logging.NullHandler())
    yield
    root.handlers = old_handlers
