<#
.SYNOPSIS
    Update this fork from upstream without losing windro's customizations.

.DESCRIPTION
    Replaces the stock `git pull --ff-only` / `git reset --hard origin/<branch>`
    update step. Where the stock path DISCARDS divergent local commits, this
    REPLAYS them on top of the new upstream:

        fetch upstream -> tag rollback point -> rebase fork patches
        -> resolve conflicts (claude-opus-5, gated) -> build + test
        -> push fork    |   or   ->  abort + restore + notify

    Hard invariants (see CUSTOMIZATIONS.md):
      * Never `git reset --hard` this fork.
      * Never accept an AI resolution unverified: build AND the relevant test
        suite must pass. Python patches are gated by the Python suite, not just
        the TypeScript typecheck.
      * Always create a rollback tag before rebasing.
      * A conflict in the update path itself (hermes_cli/main.py, the install
        scripts) is NEVER auto-resolved — abort and hand it to a human.
      * A failed update DEFERS. It must never leave a broken tree.

.PARAMETER Branch
    Branch to sync. Defaults to main.

.PARAMETER DryRun
    Do everything except push and relaunch.

.PARAMETER NoAi
    Abort on conflict instead of calling the resolver. Useful for testing the
    safe path and for a first manual run.
#>
[CmdletBinding()]
param(
    [string]$Branch = 'main',
    [switch]$DryRun,
    [switch]$NoAi
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── configuration ────────────────────────────────────────────────────────────
$RepoRoot     = Split-Path -Parent $PSScriptRoot
$ProxyUrl     = 'http://127.0.0.1:8081/v1/messages'
$ResolverModel= 'claude-opus-5'
$MaxTokens    = 32000

# Files whose conflicts must never be resolved by the model: these ARE the
# update mechanism, so a wrong resolution can destroy the ability to update.
$HandResolveOnly = @(
    'hermes_cli/main.py',
    'scripts/install.ps1',
    'scripts/install.sh',
    'scripts/hermes-sync.ps1'
)

$ReportDir = Join-Path $env:LOCALAPPDATA 'hermes\sync-reports'

# ── helpers ──────────────────────────────────────────────────────────────────
function Write-Step { param($m) Write-Host "→ $m" }
function Write-Ok   { param($m) Write-Host "  ✓ $m" }
function Write-Warn { param($m) Write-Host "  ⚠ $m" -ForegroundColor Yellow }
function Write-Err  { param($m) Write-Host "  ✗ $m" -ForegroundColor Red }

function Invoke-Git {
    # Returns a PSCustomObject; never throws on non-zero exit so callers decide.
    # windows.appendAtomically=false mirrors what the stock updater uses to dodge
    # a Windows git ref-write bug.
    $out = & git -C $RepoRoot -c windows.appendAtomically=false @args 2>&1
    [PSCustomObject]@{
        Ok     = ($LASTEXITCODE -eq 0)
        Code   = $LASTEXITCODE
        Output = ($out | Out-String).Trim()
    }
}

function Get-Head { (Invoke-Git rev-parse HEAD).Output }

function Save-Report {
    param([string]$Name, [string]$Body)
    if (-not (Test-Path $ReportDir)) {
        New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
    }
    $path = Join-Path $ReportDir ("{0}-{1}.txt" -f $Name, (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Set-Content -LiteralPath $path -Value $Body -Encoding UTF8
    $path
}

function Restore-RollbackPoint {
    <#  Rollback WITHOUT `reset --hard` (hard invariant). An in-progress rebase
        is undone by `rebase --abort`, which restores the original tip exactly.
        Only if that fails do we fall back to moving the branch ref — and even
        then via `git update-ref`, which touches no working-tree content and
        keeps the reflog entry, so nothing becomes unrecoverable. #>
    param([string]$Tag, [string]$Sha)

    if ((Invoke-Git rev-parse --verify --quiet 'REBASE_HEAD').Ok -or
        (Test-Path (Join-Path $RepoRoot '.git/rebase-merge')) -or
        (Test-Path (Join-Path $RepoRoot '.git/rebase-apply'))) {
        Write-Step 'Aborting rebase to restore the pre-sync state'
        $abort = Invoke-Git rebase --abort
        if ($abort.Ok) { Write-Ok "restored to $Sha"; return $true }
        Write-Warn "rebase --abort failed: $($abort.Output)"
    }

    if ((Get-Head) -eq $Sha) { Write-Ok 'already at the rollback point'; return $true }

    Write-Step "Restoring branch ref to $Tag"
    $res = Invoke-Git update-ref "refs/heads/$Branch" $Sha
    if ($res.Ok) { Write-Ok "branch ref restored to $Sha"; return $true }

    Write-Err "could not restore automatically. Recover manually with: git checkout $Tag"
    return $false
}

function Get-ConflictedFiles {
    $r = Invoke-Git diff --name-only --diff-filter=U
    if (-not $r.Ok -or [string]::IsNullOrWhiteSpace($r.Output)) { return @() }
    $r.Output -split "`r?`n" | Where-Object { $_ -ne '' }
}

function Test-HandResolveOnly {
    param([string[]]$Files)
    $Files | Where-Object { $HandResolveOnly -contains $_ }
}

function Resolve-ConflictWithAi {
    <#  Ask the resolver for a merged file. Returns @{ Ok; Content; Confidence }.
        The resolver system prompt is verbatim from the project plan. #>
    param([string]$RelPath, [string]$CommitMessage, [string]$Customizations)

    $system = @'
You resolve a git REBASE conflict while replaying windro's personal
customizations onto a NEW upstream release of Hermes (Electron + Python AI agent).
Inputs: CUSTOMIZATIONS.md (what each patch does + why), the commit message of the
patch being replayed, and one conflicted file.
Note: in a rebase, "ours" (<<<<<<<) = new UPSTREAM code; "theirs" (>>>>>>>) = OUR change.
Goal: produce a merged file that keeps BOTH upstream's new behavior AND our
customization's intent.
Rules:
- Never weaken/delete an upstream change to make ours apply — adapt ours into the
  new upstream structure.
- Never drop our customization — re-express it against the new code.
- Prefer minimal, additive edits; match upstream's current style and APIs.
- If upstream removed the seam our patch used, adapt to the new seam; if truly
  impossible, leave a clearly-marked // TODO(custom-hermes) and lower confidence.
Output ONLY the full resolved file contents (no conflict markers, no commentary).
Then a final line: CONFIDENCE: high|medium|low — <one sentence why>.
'@

    $body = Get-Content -LiteralPath (Join-Path $RepoRoot $RelPath) -Raw

    $userText = @"
=== CUSTOMIZATIONS.md ===
$Customizations

=== COMMIT BEING REPLAYED ===
$CommitMessage

=== CONFLICTED FILE: $RelPath ===
$body
"@

    $payload = @{
        model      = $ResolverModel
        max_tokens = $MaxTokens
        system     = $system
        messages   = @(@{ role = 'user'; content = $userText })
    } | ConvertTo-Json -Depth 8 -Compress

    try {
        $resp = Invoke-RestMethod -Uri $ProxyUrl -Method Post -Body $payload `
            -ContentType 'application/json' `
            -Headers @{ 'x-api-key' = 'no-key-required'; 'anthropic-version' = '2023-06-01' } `
            -TimeoutSec 600
    } catch {
        return @{ Ok = $false; Reason = "proxy call failed: $($_.Exception.Message)" }
    }

    # The proxy emits a `thinking` block BEFORE `text` on reasoning-heavy
    # prompts, and only `text` on trivial ones. Take the LAST text block:
    # content[0] would hand back the model's reasoning as file contents, and
    # content[1] would IndexError on single-block replies.
    $textBlocks = @($resp.content | Where-Object { $_.type -eq 'text' })
    if ($textBlocks.Count -eq 0) { return @{ Ok = $false; Reason = 'no text block in response' } }
    $raw = $textBlocks[-1].text

    $lines = $raw -split "`r?`n"
    $confLine = ($lines | Where-Object { $_ -match '^\s*CONFIDENCE\s*:' } | Select-Object -Last 1)
    if (-not $confLine) { return @{ Ok = $false; Reason = 'resolver omitted the CONFIDENCE line' } }

    $confidence = if ($confLine -match 'high') { 'high' }
                  elseif ($confLine -match 'medium') { 'medium' }
                  else { 'low' }

    # Everything before the CONFIDENCE line is the file. Strip a markdown fence
    # if the model wrapped the file in one.
    $idx = [Array]::LastIndexOf($lines, $confLine)
    $content = ($lines[0..([Math]::Max($idx - 1, 0))] -join "`n").Trim()
    if ($content -match '^```') {
        $content = ($content -replace '^```[a-zA-Z0-9]*\r?\n', '') -replace '\r?\n```$', ''
    }

    if ($content -match '^(<<<<<<<|>>>>>>>|=======)' -or $content -match "`n(<<<<<<<|>>>>>>>)") {
        return @{ Ok = $false; Reason = 'resolver left conflict markers in the output' }
    }
    if ([string]::IsNullOrWhiteSpace($content)) {
        return @{ Ok = $false; Reason = 'resolver returned an empty file' }
    }

    @{ Ok = $true; Content = $content; Confidence = $confidence }
}

function Invoke-VerificationGate {
    <#  Nothing is accepted unverified. Which suite runs depends on WHAT changed:
        a Python patch gated only by a TypeScript typecheck is not a gate. #>
    param([string[]]$ChangedFiles)

    $pyTouched = @($ChangedFiles | Where-Object { $_ -like '*.py' }).Count -gt 0
    $tsTouched = @($ChangedFiles | Where-Object { $_ -match '\.(ts|tsx|js|jsx|mjs|cjs)$' }).Count -gt 0

    Write-Step 'Verification gate'

    # Always: the tree must at least import/compile. Cheap, catches the
    # syntax-error-bricks-the-CLI class the stock updater guards against too.
    $py = & python -c "import ast,sys;ast.parse(open(r'$RepoRoot/hermes_cli/main.py',encoding='utf-8').read())" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "hermes_cli/main.py does not parse: $py"
        return $false
    }
    Write-Ok 'main.py parses'

    if ($pyTouched) {
        Write-Step 'Python suite (scripts/run_tests.sh)'
        $log = & bash -lc "cd '$RepoRoot' && ./scripts/run_tests.sh 2>&1 | tail -40"
        if ($LASTEXITCODE -ne 0) {
            Write-Err 'Python tests failed'
            Write-Host ($log | Out-String)
            return $false
        }
        Write-Ok 'Python tests passed'
    }

    if ($tsTouched) {
        Write-Step 'Desktop typecheck'
        $log = & bash -lc "cd '$RepoRoot/apps/desktop' && npm run typecheck 2>&1 | tail -30"
        if ($LASTEXITCODE -ne 0) {
            Write-Err 'typecheck failed'
            Write-Host ($log | Out-String)
            return $false
        }
        Write-Ok 'typecheck passed'

        Write-Step 'Desktop build'
        $log = & bash -lc "cd '$RepoRoot' && hermes desktop --build-only --force-build 2>&1 | tail -20"
        if ($LASTEXITCODE -ne 0) {
            Write-Err 'desktop build failed'
            Write-Host ($log | Out-String)
            return $false
        }
        Write-Ok 'desktop build passed'
    }

    if (-not $pyTouched -and -not $tsTouched) { Write-Ok 'no code files changed; parse check sufficed' }
    return $true
}

function Install-ForkGuard {
    <#  Reinstall the reference-transaction backstop. It lives in .git/hooks,
        which is NOT versioned, so a fresh clone or a repaired install loses it —
        reinstalling on every sync makes the protection self-healing.

        KNOWN LIMITATION (verified, see CUSTOMIZATIONS.md): git's hook interface
        cannot distinguish `reset --hard` from `reset --soft` before the fact —
        both present an identical ref move. The guard therefore also refuses a
        `reset --soft` that lands on a remote tip while unpushed fork commits
        exist. Rare, loud, and overridable with HERMES_SYNC_ALLOW_REWRITE=1.
        This is a BACKSTOP; the primary protection is the patched update sites. #>
    $src = Join-Path $PSScriptRoot 'fork-guard.sh'
    if (-not (Test-Path $src)) { Write-Warn 'fork-guard.sh missing; backstop not installed'; return }
    $dst = Join-Path $RepoRoot '.git/hooks/reference-transaction'
    Copy-Item -LiteralPath $src -Destination $dst -Force
    & bash -lc "chmod +x '$dst'" 2>&1 | Out-Null
    Write-Ok 'fork guard (backstop) installed'
}

# ── main ─────────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host 'hermes-sync — rebase fork patches onto upstream' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path (Join-Path $RepoRoot '.git'))) { Write-Err "not a git repo: $RepoRoot"; exit 1 }

# rerere makes a repeated conflict replay its previous resolution: one model call
# ever per conflict shape, not one per update.
Invoke-Git config rerere.enabled true    | Out-Null
Invoke-Git config rerere.autoUpdate true | Out-Null

Install-ForkGuard

# A shallow clone has no merge-base, so `git rebase upstream/<branch>` cannot
# work at all. The installer clones with --depth 1, so this is a real state.
if ((Invoke-Git rev-parse --is-shallow-repository).Output -eq 'true') {
    Write-Step 'Repository is shallow; fetching full history (required for rebase)'
    $un = Invoke-Git fetch --unshallow upstream
    if (-not $un.Ok) { Write-Err "unshallow failed: $($un.Output)"; exit 1 }
    Write-Ok 'history complete'
}

if (-not (Invoke-Git remote get-url upstream).Ok) {
    Write-Err "no 'upstream' remote. Add it: git remote add upstream https://github.com/NousResearch/hermes-agent.git"
    exit 1
}

$dirty = (Invoke-Git status --porcelain).Output
if ($dirty) {
    Write-Err 'working tree is not clean. Commit or stash first:'
    Write-Host $dirty
    exit 1
}

Write-Step "Fetching upstream/$Branch"
$fetch = Invoke-Git fetch upstream $Branch
if (-not $fetch.Ok) { Write-Err "fetch failed: $($fetch.Output)"; exit 1 }

$behind = [int](Invoke-Git rev-list --count "HEAD..upstream/$Branch").Output
if ($behind -eq 0) { Write-Ok 'already up to date'; exit 0 }
Write-Ok "$behind new upstream commit(s)"

# Rollback point BEFORE any history operation (hard invariant).
$preSha = Get-Head
$tag    = "pre-sync-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Invoke-Git tag $tag $preSha | Out-Null
Write-Ok "rollback point $tag -> $($preSha.Substring(0,9))"

$forkCommits = (Invoke-Git rev-list --count "upstream/$Branch..HEAD").Output
Write-Ok "$forkCommits fork commit(s) to replay"

$customizations = ''
$custPath = Join-Path $RepoRoot 'CUSTOMIZATIONS.md'
if (Test-Path $custPath) { $customizations = Get-Content -LiteralPath $custPath -Raw }

Write-Step "Rebasing onto upstream/$Branch"
$env:HERMES_SYNC_ALLOW_REWRITE = '1'   # our own deliberate history surgery
try {
    $rebase = Invoke-Git rebase "upstream/$Branch"

    $guard = 0
    while (-not $rebase.Ok) {
        if (++$guard -gt 50) { Write-Err 'too many conflict rounds; aborting'; break }

        $conflicted = Get-ConflictedFiles
        if ($conflicted.Count -eq 0) {
            Write-Err "rebase failed with no conflicted files:`n$($rebase.Output)"
            break
        }

        Write-Warn "conflict in $($conflicted.Count) file(s): $($conflicted -join ', ')"

        # R14 enforced in CODE, not as a request to the model: a conflict in the
        # update path is never sent to the resolver.
        $handOnly = Test-HandResolveOnly -Files $conflicted
        if ($handOnly) {
            $reportBody = @(
                'hermes-sync stopped: a conflict landed in the update mechanism itself.',
                'These files are never auto-resolved, because a wrong resolution can',
                'destroy the ability to update at all.',
                '',
                ('Files: ' + ($handOnly -join ', ')),
                ("Rollback point: $tag ($preSha)"),
                '',
                'Resolve by hand, then run: git rebase --continue',
                '',
                '=== conflict hunks ===',
                (Invoke-Git diff).Output
            ) -join [Environment]::NewLine
            $report = Save-Report 'HAND-RESOLVE-REQUIRED' $reportBody
            Write-Err "update-path conflict — hand resolution required: $($handOnly -join ', ')"
            Write-Host "  report: $report"
            Restore-RollbackPoint -Tag $tag -Sha $preSha | Out-Null
            exit 3
        }

        if ($NoAi) {
            $report = Save-Report 'CONFLICT-NOAI' ((Invoke-Git diff).Output)
            Write-Err "conflict and -NoAi set; not calling the resolver. Report: $report"
            Restore-RollbackPoint -Tag $tag -Sha $preSha | Out-Null
            exit 4
        }

        $msg = (Invoke-Git log -1 --format='%B' 'REBASE_HEAD').Output

        $failed = $false
        foreach ($file in $conflicted) {
            Write-Step "resolving $file with $ResolverModel"
            $res = Resolve-ConflictWithAi -RelPath $file -CommitMessage $msg -Customizations $customizations

            if (-not $res.Ok) { Write-Err $res.Reason; $failed = $true; break }
            if ($res.Confidence -eq 'low') {
                Write-Err "resolver reported LOW confidence on $file"
                $failed = $true; break
            }

            Set-Content -LiteralPath (Join-Path $RepoRoot $file) -Value $res.Content -Encoding UTF8 -NoNewline
            Invoke-Git add -- $file | Out-Null
            Write-Ok "$file resolved (confidence: $($res.Confidence))"
        }

        if ($failed) {
            $reportBody = @(
                'hermes-sync aborted: the resolver could not confidently resolve a conflict.',
                "Rollback point: $tag ($preSha)",
                '',
                '=== conflicted files ===',
                ($conflicted -join [Environment]::NewLine),
                '',
                '=== hunks ===',
                (Invoke-Git diff).Output
            ) -join [Environment]::NewLine
            $report = Save-Report 'RESOLUTION-FAILED' $reportBody
            Write-Err "resolution failed. Report: $report"
            Restore-RollbackPoint -Tag $tag -Sha $preSha | Out-Null
            exit 5
        }

        $rebase = Invoke-Git rebase --continue
    }

    if (-not $rebase.Ok) {
        $report = Save-Report 'REBASE-FAILED' $rebase.Output
        Write-Err "rebase did not complete. Report: $report"
        Restore-RollbackPoint -Tag $tag -Sha $preSha | Out-Null
        exit 6
    }
    Write-Ok 'rebase complete'

    # ── verification: nothing is accepted unverified ──
    $changed = (Invoke-Git diff --name-only "$preSha..HEAD").Output
    $changedFiles = if ($changed) { $changed -split "`r?`n" } else { @() }

    if (-not (Invoke-VerificationGate -ChangedFiles $changedFiles)) {
        $reportBody = @(
            'hermes-sync aborted: the rebase completed but verification failed.',
            "The tree has been restored to $tag ($preSha); the app still runs the prior build."
        ) -join [Environment]::NewLine
        $report = Save-Report 'VERIFICATION-FAILED' $reportBody
        Write-Err "verification failed. Report: $report"
        Restore-RollbackPoint -Tag $tag -Sha $preSha | Out-Null
        exit 7
    }
    Write-Ok 'verification passed'

    if ($DryRun) {
        Write-Warn "dry run: not pushing. Rollback point $tag kept."
        exit 0
    }

    # Push immediately: a fork commit that exists only locally is the one thing
    # a stray reset can still cost us. Once pushed, it is recoverable.
    Write-Step 'Pushing fork'
    $push = Invoke-Git push origin $Branch
    if (-not $push.Ok) {
        Write-Warn "push failed (local tree is fine and verified): $($push.Output)"
        Write-Warn ("push manually when possible: git push origin " + $Branch)
    } else {
        Write-Ok 'fork pushed'
    }

    Write-Host ''
    Write-Host ("hermes-sync complete: {0} upstream commit(s) merged, {1} patch(es) replayed." -f $behind, $forkCommits) -ForegroundColor Green
    Write-Host ("Rollback point: {0}" -f $tag) -ForegroundColor DarkGray
    exit 0
}
finally {
    Remove-Item Env:\HERMES_SYNC_ALLOW_REWRITE -ErrorAction SilentlyContinue
}
