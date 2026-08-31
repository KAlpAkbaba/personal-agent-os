param(
  [string]$PromptFile = "01_CLAUDE_START_PROMPT.txt"
)

# Example only. Review and adapt after M-1.
# For this owner-authored repository, programmatic Claude Code can be used after the workspace is trusted.
# Auto mode reduces routine prompts. Do NOT switch to bypassPermissions on the normal owner workstation.

$prompt = Get-Content $PromptFile -Raw
claude -p $prompt --permission-mode auto --output-format stream-json --verbose
