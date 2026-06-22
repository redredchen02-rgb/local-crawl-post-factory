"""Central CLI contract runner.

Guarantees the I/O contract across every command (origin spec §2.3 / §10):

    success -> stdout carries structured data, stderr empty, exit 0
    failure -> stdout empty, stderr one diagnostic line, exit 1-5

Every command's ``main`` should delegate to ``run`` so the contract stays
identical across all 13 commands.
"""

import sys
from typing import Callable

from cpost.core.errors import CliError


def run(handler: Callable[[], int | None]) -> int:
    """Execute ``handler`` under the CLI contract and return an exit code.

    ``handler`` is responsible for writing successful structured output to
    stdout. It must not write to stderr on success. Any ``CliError`` becomes a
    single stderr diagnostic line with the mapped exit code; any other
    exception maps to exit code 5.
    """
    try:
        result = handler()
        return int(result) if result is not None else 0
    except CliError as exc:
        _diagnose(exc.message)
        return exc.exit_code
    except BrokenPipeError:
        # Downstream closed the pipe (e.g. `| head`). Not an error.
        return 0
    except KeyboardInterrupt:
        _diagnose("interrupted")
        return 1
    except Exception as exc:  # noqa: BLE001 - contract: map everything to exit 5
        _diagnose(f"internal error: {exc}")
        return 5


def _diagnose(message: str) -> None:
    """Emit exactly one diagnostic line to stderr."""
    sys.stderr.write(message.replace("\n", " ").strip() + "\n")


def main_wrapper(handler: Callable[[], int | None]) -> None:
    """Convenience entry point: run handler and exit with its code."""
    sys.exit(run(handler))
