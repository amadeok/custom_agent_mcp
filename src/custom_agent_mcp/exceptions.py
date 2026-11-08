from __future__ import annotations


class PyFindRuntimeError(RuntimeError):
    pass


class WindowNotFoundError(PyFindRuntimeError):
    pass


class TemplateNotFoundError(PyFindRuntimeError):
    pass


class InputDisabledError(PyFindRuntimeError):
    pass


class Loge2NotInstalledError(PyFindRuntimeError):
    pass


class FocusNotAcquiredError(PyFindRuntimeError):
    pass


class SearxngError(RuntimeError):
    pass