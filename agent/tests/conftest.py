"""Collection guard: test_reachability.py drives the katagiri package directly and
must run under katagiri's venv (see its header). The agent venv cannot import
katagiri, so skip collecting it there instead of erroring the whole suite."""

collect_ignore = []
try:
    import katagiri  # noqa: F401
except ImportError:
    collect_ignore.append("test_reachability.py")
