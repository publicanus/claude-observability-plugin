"""Texts that Claude Code writes the same way in every session, and that the hook reads."""

# The user row Claude Code writes when Esc stops a turn.
INTERRUPTED_TEXT = "[Request interrupted by user]"

# A denied tool call's toolUseResult in Claude Code up to 2.1.198, before toolDenialKind.
LEGACY_USER_REJECTED_TOOL_USE_RESULT = "User rejected tool use"
