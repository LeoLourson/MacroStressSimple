# Локальный стенд: PostgreSQL + FastAPI + Streamlit. Профиль по умолчанию: mock.
#   .\msa.ps1 up [deepseek|local|mock] | down | status | logs [name] | rehearse
#   deepseek — API DeepSeek (DEEPSEEK_API_KEY), local — LM Studio, mock — без LLM
param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "status", "logs", "rehearse")]
    [string]$Command = "status",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$script = if ($Command -eq "rehearse") { "scripts/rehearse.py" } else { "scripts/local_stack.py" }
# @() сохраняет массив при единственном аргументе: строка передавалась бы посимвольно.
$arguments = @(if ($Command -eq "rehearse") { $Rest } else { @($Command) + $Rest })
uv run --frozen python $script @arguments
exit $LASTEXITCODE
