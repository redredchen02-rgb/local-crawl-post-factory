import io

from cpost.core import cli
from cpost.core.errors import ValidationError, UsageError, DependencyError, ExternalError
from cpost.core.io_ndjson import read_lines, write_line


def test_success_returns_zero():
    assert cli.run(lambda: None) == 0
    assert cli.run(lambda: 0) == 0


def test_validation_error_maps_to_2(capsys):
    def handler():
        raise ValidationError("bad input")

    code = cli.run(handler)
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err.strip().count("\n") == 0
    assert "bad input" in captured.err


def test_unexpected_exception_maps_to_5(capsys):
    def handler():
        raise RuntimeError("boom")

    code = cli.run(handler)
    captured = capsys.readouterr()
    assert code == 5
    assert captured.out == ""
    assert captured.err.strip() != ""


def test_exit_code_mapping():
    assert cli.run(_raiser(UsageError("u"))) == 1
    assert cli.run(_raiser(ValidationError("v"))) == 2
    assert cli.run(_raiser(DependencyError("d"))) == 3
    assert cli.run(_raiser(ExternalError("e"))) == 4


def test_diagnostic_is_single_line(capsys):
    cli.run(_raiser(ValidationError("line one\nline two")))
    err = capsys.readouterr().err
    assert err.count("\n") == 1


def test_empty_stdin_is_not_an_error():
    out = io.StringIO()

    def handler():
        for obj in read_lines(io.StringIO("")):
            write_line(obj, out)

    assert cli.run(handler) == 0
    assert out.getvalue() == ""


def test_ndjson_roundtrip():
    src = io.StringIO('{"a": 1}\n\n{"b": "x"}\n')
    out = io.StringIO()
    for obj in read_lines(src):
        write_line(obj, out)
    lines = out.getvalue().strip().split("\n")
    assert len(lines) == 2


def test_malformed_ndjson_raises_validation():
    import pytest

    with pytest.raises(ValidationError):
        list(read_lines(io.StringIO("{not json}\n")))


def test_broken_pipe_returns_zero():
    """BrokenPipeError is absorbed and returns 0 (cli.py:34)."""
    def handler():
        raise BrokenPipeError()

    assert cli.run(handler) == 0


def test_keyboard_interrupt_returns_one(capsys):
    """KeyboardInterrupt maps to exit 1 with stderr message (cli.py:36-37)."""
    def handler():
        raise KeyboardInterrupt()

    code = cli.run(handler)
    assert code == 1
    assert "interrupted" in capsys.readouterr().err


def test_main_wrapper_exits_with_handler_code():
    """main_wrapper calls sys.exit with the handler's return code (cli.py:50)."""
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        cli.main_wrapper(lambda: 3)
    assert exc_info.value.code == 3


def _raiser(exc):
    def handler():
        raise exc

    return handler
