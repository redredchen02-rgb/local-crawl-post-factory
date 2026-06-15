"""Exit-code-bearing exceptions for the CLI contract.

Exit codes (origin spec §2.3 / §13):
    0 success
    1 usage error
    2 input validation error
    3 dependency error
    4 external service error
    5 unexpected internal error
"""


class CliError(Exception):
    """Base error carrying a process exit code."""

    exit_code = 5

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class UsageError(CliError):
    exit_code = 1


class ValidationError(CliError):
    exit_code = 2


class DependencyError(CliError):
    exit_code = 3


class ExternalError(CliError):
    exit_code = 4


class SessionExpiredError(ExternalError):
    """Login session expired / redirected to login page.

    A distinguishable kind of external failure (still exit 4) whose remedy is to
    re-run ``auth-login`` rather than to retry the action.
    """


class InternalError(CliError):
    exit_code = 5
