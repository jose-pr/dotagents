"""The error every refusal raises, and the closed set of classes it carries."""

from __future__ import annotations

import enum
import unicodedata
from typing import Optional

__all__ = ["Class", "Error"]

#: The longest subject an error keeps, in characters.
SUBJECT_LIMIT = 256


class Class(str, enum.Enum):
    """What went wrong, as the specification's conformance vectors name it.

    A member equals its word (``Class.DUPLICATE_KEY == "duplicate-key"``), so a
    caller may match on either. ``Class.named`` reads a word back and returns
    ``None`` for one this library does not know.
    """

    AMBIGUOUS = "ambiguous"
    CASE_COLLISION = "case-collision"
    CYCLE = "cycle"
    DATETIME_VALUE = "datetime-value"
    DOTENV_SYNTAX = "dotenv-syntax"
    DUPLICATE_KEY = "duplicate-key"
    DUPLICATE_MEMBER = "duplicate-member"
    ENV_NAME_CASE_DUPLICATE = "env-name-case-duplicate"
    ENV_PATH_CONFLICT = "env-path-conflict"
    ENV_SCRIPT_OUTPUT = "env-script-output"
    HOOK_REFUSED = "hook-refused"
    ICON_DIMENSIONS = "icon-dimensions"
    ICON_FORMAT = "icon-format"
    ICON_MISMATCH = "icon-mismatch"
    ICON_TOO_LARGE = "icon-too-large"
    ICON_UNREADABLE = "icon-unreadable"
    INTEGER_RANGE = "integer-range"
    INTEGER_REQUIRED = "integer-required"
    INVALID_DIGEST = "invalid-digest"
    INVALID_ENV_FILE = "invalid-env-file"
    INVALID_LABEL = "invalid-label"
    INVALID_NAME = "invalid-name"
    INVALID_OPTION = "invalid-option"
    INVALID_PATTERN = "invalid-pattern"
    INVALID_REFERENCE = "invalid-reference"
    INVALID_REQUIREMENTS = "invalid-requirements"
    INVALID_SOURCE = "invalid-source"
    INVALID_UNICODE = "invalid-unicode"
    INVALID_UTF8 = "invalid-utf8"
    IO = "io"
    LICENSE_EXPRESSION = "license-expression"
    LOCAL_SOURCE = "local-source"
    MALFORMED_FENCE = "malformed-fence"
    MANIFEST_FILE = "manifest-file"
    MCP_COMMAND = "mcp-command"
    MCP_DUPLICATE_SERVER = "mcp-duplicate-server"
    MCP_DUPLICATE_SKILL = "mcp-duplicate-skill"
    MCP_UNKNOWN_SERVER = "mcp-unknown-server"
    MCP_URL = "mcp-url"
    MISSING_FILE = "missing-file"
    MISSING_SKILL = "missing-skill"
    NO_FORMAT_NAMED = "no-format-named"
    NON_FINITE_FLOAT = "non-finite-float"
    NON_STRING_KEY = "non-string-key"
    NON_UTF8_NAME = "non-utf8-name"
    NOT_A_CATALOG = "not-a-catalog"
    NOT_A_MAPPING = "not-a-mapping"
    NOT_A_SKILL = "not-a-skill"
    NOT_FOUND = "not-found"
    NULL_VALUE = "null-value"
    OPTION_DEFAULT_TYPE = "option-default-type"
    OPTION_SENSITIVE_DEFAULT = "option-sensitive-default"
    PARSE_ERROR = "parse-error"
    PARTIAL_LOCK = "partial-lock"
    PIN_MISMATCH = "pin-mismatch"
    REF_NOT_ALLOWED = "ref-not-allowed"
    RESERVED_NAME = "reserved-name"
    RESERVED_VARIABLE = "reserved-variable"
    SCHEMA = "schema"
    SECRET_PLACEHOLDER = "secret-placeholder"
    SKILL_AMBIGUOUS = "skill-ambiguous"
    SKILL_CASE = "skill-case"
    SKILL_DUPLICATE = "skill-duplicate"
    SKILL_NAME = "skill-name"
    SKILL_NAME_MISMATCH = "skill-name-mismatch"
    SKILL_UNREADABLE = "skill-unreadable"
    SOURCE_CREDENTIALS = "source-credentials"
    SPECIAL_FILE = "special-file"
    SYMLINK = "symlink"
    TOO_COMPLEX = "too-complex"
    TOO_DEEP = "too-deep"
    TOO_LARGE = "too-large"
    TWO_MANIFESTS = "two-manifests"
    UNDEFINED_VARIABLE = "undefined-variable"
    UNRESOLVED_REFERENCE = "unresolved-reference"
    UNSUPPORTED_FORMAT = "unsupported-format"
    UNTERMINATED_FRONT_MATTER = "unterminated-front-matter"
    YAML_ANCHOR = "yaml-anchor"
    YAML_DIRECTIVE = "yaml-directive"
    YAML_MERGE_KEY = "yaml-merge-key"
    YAML_TAG = "yaml-tag"

    @classmethod
    def named(cls, word: str) -> "Optional[Class]":
        """The class a vector's word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.value


def _safe(subject: str) -> str:
    """``subject`` with each control, format, line-separator, surrogate and private-use character
    escaped and the whole cut to ``SUBJECT_LIMIT`` characters, so a name from a stranger's document
    can neither drive a terminal nor fill a log."""
    out = []
    for char in subject[: SUBJECT_LIMIT + 1]:
        if unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"):
            code = ord(char)
            out.append(
                "\\x%02x" % code if code < 0x100 else "\\u%04x" % code if code < 0x10000 else "\\U%08x" % code
            )
        else:
            out.append(char)
    text = "".join(out)
    return text if len(text) <= SUBJECT_LIMIT else text[:SUBJECT_LIMIT] + "..."


class Error(Exception):
    """A document, tree or request AOS refuses.

    ``class_`` is the contract; ``subject`` names what it is about (a variable, a path, a
    pointer) for a person, never text from a document, so do not match on it. ``line`` and
    ``column`` are one-based, ``column`` counts Unicode scalar values, and a line counts from
    the first line of the file, a format line included. For a refusal of a limit (``too-large``,
    ``too-deep``, ``too-complex``, ``icon-too-large``, ``icon-dimensions``) ``setting`` is the
    name of the ``Settings`` field that was passed, such as ``"tree_files"``: stable, unlike
    ``subject``'s wording. It is ``None`` for any other refusal.
    """

    def __init__(
        self,
        class_: Class,
        subject: str = "",
        line: "Optional[int]" = None,
        column: "Optional[int]" = None,
        setting: "Optional[str]" = None,
    ) -> None:
        subject = _safe(subject)
        super().__init__(class_, subject, line, column)
        self.class_ = class_
        self.subject = subject
        self.line = line
        self.column = column
        self.setting = setting

    def over(self, setting: str) -> "Error":
        """The same error naming the ``Settings`` field whose limit was passed."""
        self.setting = setting
        return self

    def at(self, line: int, column: int) -> "Error":
        """The same error with a position."""
        self.line, self.column = line, column
        self.args = (self.class_, self.subject, line, column)
        return self

    def on_line(self, line: int) -> "Error":
        """The same error with a line and no column."""
        self.line, self.column = line, None
        self.args = (self.class_, self.subject, line, None)
        return self

    def __str__(self) -> str:
        text = self.class_.value
        if self.subject:
            text += ": " + self.subject
        if self.line is not None:
            text += " at line %d" % self.line
            if self.column is not None:
                text += " column %d" % self.column
        return text
