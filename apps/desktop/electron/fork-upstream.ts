// Fork-aware update detection for the desktop update indicator.
//
// WHY THIS EXISTS
//   checkUpdates() measures `HEAD..origin/<branch>` — how far this checkout is
//   behind the remote it pushes to. On a fork that merges upstream itself and
//   then pushes, local and origin are identical, so that number is permanently
//   zero: the popup never fires, Settings always says "up to date", and the
//   "Update now" button has nothing to do. Meanwhile the actual source of new
//   code (NousResearch/hermes-agent, the `upstream` remote) can be dozens of
//   commits ahead and the user is never told.
//
//   So when origin has nothing new, ALSO look at the `upstream` remote. If that
//   is ahead, report that count instead. `hermes update` knows how to merge it
//   (hermes_cli/fork_merge.py + the mount in _sync_with_upstream_if_needed), so
//   the button that was previously inert now does the right thing.
//
// LADDER + DEGRADATION
//   Every rung returns null rather than throwing: no `upstream` remote (a normal
//   non-fork install, or the official checkout), a failed fetch (offline), an
//   unparseable count. null means "nothing to add" and the caller keeps its
//   original origin-based answer, unchanged. This must never be able to break
//   the update check itself.
//
// DELIBERATELY NOT DONE
//   No commit list for the fork case. readCommitLog() is hardcoded to the origin
//   range, and widening it would mean editing an upstream function plus its call
//   site for a cosmetic list. The official-SSH branch of checkUpdates() already
//   returns `commits: []`, so an empty list is an established, handled shape.
//   The count — which is what drives the popup — is exact.

const DEFAULT_UPSTREAM_REMOTE = 'upstream'

// Parse `git rev-list --count` output. Anything non-numeric means "no idea",
// which must read as zero so a garbled result cannot invent a fake update.
function parseBehindCount(countStr) {
  const parsed = Number.parseInt(String(countStr ?? '').trim(), 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

// Should the fork-upstream probe run at all? Only when the ordinary origin
// comparison found nothing — origin staying authoritative when it has something
// to say keeps the normal (non-fork) path byte-identical.
function shouldProbeUpstream({ originBehind, supported = true }) {
  return Boolean(supported) && parseBehindCount(originBehind) === 0
}

// `git` is injected: an async (args[]) => { code, stdout, stderr } runner. Keeps
// this unit testable without a repository, and keeps the process plumbing in
// main.ts where it belongs.
// Defaults are deliberate: a caller that forgets `git` or `branch` must get a
// null (no update claimed), not a TypeError inside the update check.
async function resolveForkUpstreamStatus({ git = null, branch = '', remote = DEFAULT_UPSTREAM_REMOTE }) {
  if (typeof git !== 'function' || !branch) return null

  try {
    const remoteUrl = await git(['remote', 'get-url', remote])
    if (!remoteUrl || remoteUrl.code !== 0 || !String(remoteUrl.stdout || '').trim()) {
      return null
    }

    // Scope the fetch to one branch. A bare fetch drags in thousands of
    // auto-generated refs on this repo — the same reason the CLI scopes its own
    // upstream fetch (hermes_cli/main.py::_sync_with_upstream_if_needed).
    const fetched = await git(['fetch', '--quiet', remote, branch])
    if (!fetched || fetched.code !== 0) return null

    const ref = `${remote}/${branch}`
    const [countRes, shaRes] = await Promise.all([
      git(['rev-list', `HEAD..${ref}`, '--count']),
      git(['rev-parse', ref])
    ])

    const behind = parseBehindCount(countRes && countRes.stdout)
    if (behind === 0) return null

    return {
      behind,
      remote,
      ref,
      targetSha: String((shaRes && shaRes.stdout) || '').trim()
    }
  } catch {
    return null
  }
}

export { DEFAULT_UPSTREAM_REMOTE, parseBehindCount, resolveForkUpstreamStatus, shouldProbeUpstream }
