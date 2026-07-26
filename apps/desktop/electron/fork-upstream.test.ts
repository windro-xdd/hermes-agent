import assert from 'node:assert/strict'

import { test } from 'vitest'

import {
  isForkMergeEnabled,
  parseBehindCount,
  resolveForkUpstreamStatus,
  shouldProbeUpstream,
  withForkUpstreamStatus
} from './fork-upstream'

// FAIL-BEFORE: without this module checkUpdates() only ever measured
// HEAD..origin/<branch>. On a fork that merges upstream itself and pushes, that
// count is permanently 0, so the update popup could never fire no matter how far
// behind NousResearch/hermes-agent the checkout was.

function gitStub(responses) {
  const calls = []
  const git = async args => {
    calls.push(args.join(' '))
    for (const [match, response] of responses) {
      if (args.join(' ').includes(match)) return response
    }
    return { code: 0, stdout: '', stderr: '' }
  }
  git.calls = calls
  return git
}

const ok = stdout => ({ code: 0, stdout, stderr: '' })
const fail = stderr => ({ code: 1, stdout: '', stderr })

test('reports how far behind the upstream remote the checkout is', async () => {
  const git = gitStub([
    ['remote get-url upstream', ok('https://github.com/NousResearch/hermes-agent.git')],
    ['rev-list HEAD..upstream/main --count', ok('43\n')],
    ['rev-parse upstream/main', ok('abc123\n')]
  ])

  const status = await resolveForkUpstreamStatus({ git, branch: 'main' })

  assert.deepEqual(status, {
    behind: 43,
    remote: 'upstream',
    ref: 'upstream/main',
    targetSha: 'abc123'
  })
})

test('fetches only the target branch', async () => {
  const git = gitStub([
    ['remote get-url upstream', ok('https://example.com/x.git')],
    ['rev-list', ok('2')],
    ['rev-parse', ok('sha')]
  ])

  await resolveForkUpstreamStatus({ git, branch: 'main' })

  assert.ok(
    git.calls.some(c => c === 'fetch --quiet upstream main'),
    `expected a branch-scoped fetch, got: ${JSON.stringify(git.calls)}`
  )
})

test('no upstream remote is not an error — it is a normal install', async () => {
  const git = gitStub([['remote get-url upstream', fail('No such remote')]])
  assert.equal(await resolveForkUpstreamStatus({ git, branch: 'main' }), null)
  assert.ok(
    !git.calls.some(c => c.startsWith('fetch')),
    'must not fetch when there is no upstream remote'
  )
})

test('a failed fetch degrades to null rather than throwing', async () => {
  const git = gitStub([
    ['remote get-url upstream', ok('https://example.com/x.git')],
    ['fetch', fail('could not resolve host')]
  ])
  assert.equal(await resolveForkUpstreamStatus({ git, branch: 'main' }), null)
})

test('an exception inside git degrades to null', async () => {
  const git = async () => {
    throw new Error('spawn failed')
  }
  assert.equal(await resolveForkUpstreamStatus({ git, branch: 'main' }), null)
})

test('upstream with nothing new returns null so origin stays authoritative', async () => {
  const git = gitStub([
    ['remote get-url upstream', ok('https://example.com/x.git')],
    ['rev-list', ok('0')],
    ['rev-parse', ok('sha')]
  ])
  assert.equal(await resolveForkUpstreamStatus({ git, branch: 'main' }), null)
})

test('a garbled count cannot invent an update', async () => {
  assert.equal(parseBehindCount('not-a-number'), 0)
  assert.equal(parseBehindCount(''), 0)
  assert.equal(parseBehindCount(undefined), 0)
  assert.equal(parseBehindCount('-4'), 0)
  assert.equal(parseBehindCount(' 7 \n'), 7)
})

test('the probe only runs when origin found nothing', () => {
  assert.equal(shouldProbeUpstream({ originBehind: 0 }), true)
  assert.equal(shouldProbeUpstream({ originBehind: 3 }), false)
  // Payload health is the wrapper's job, asserted in 'leaves an errored or
  // unsupported payload alone' below — not this predicate's.
  assert.equal(shouldProbeUpstream({ originBehind: '0' }), true)
  assert.equal(shouldProbeUpstream({ originBehind: undefined }), true)
})

test('a missing git runner is handled, not thrown', async () => {
  assert.equal(await resolveForkUpstreamStatus({ branch: 'main' }), null)
  assert.equal(await resolveForkUpstreamStatus({ git: gitStub([]), branch: '' }), null)
})

// ── the off switch must cover BOTH halves ────────────────────────────────────
// FAIL-BEFORE: HERMES_FORK_MERGE=0 disabled only the Python merge. The probe kept
// running, so the popup advertised upstream commits while the Update button did
// nothing — the half-wired state the fork's R7 explicitly forbids.

test('the off switch matches the Python half, value for value', () => {
  for (const off of ['0', 'false', 'no', 'off', '', ' OFF ']) {
    assert.equal(isForkMergeEnabled({ HERMES_FORK_MERGE: off }), false, `expected off for ${JSON.stringify(off)}`)
  }
  for (const on of ['1', 'true', 'yes']) {
    assert.equal(isForkMergeEnabled({ HERMES_FORK_MERGE: on }), true)
  }
  assert.equal(isForkMergeEnabled({}), true, 'absent must mean enabled')
})

// ── the call-site wrapper ────────────────────────────────────────────────────
const originStatus = (extra = {}) => ({
  supported: true,
  branch: 'main',
  behind: 0,
  currentSha: 'local',
  targetSha: 'local',
  commits: [],
  ...extra
})

function forkGit() {
  return gitStub([
    ['remote get-url upstream', ok('https://github.com/NousResearch/hermes-agent.git')],
    ['rev-list', ok('43')],
    ['rev-parse', ok('upstreamsha')]
  ])
}

test('decorates the payload when origin found nothing but upstream is ahead', async () => {
  const status = await withForkUpstreamStatus(
    Promise.resolve(originStatus()),
    forkGit(),
    { env: {} }
  )
  assert.equal(status.behind, 43)
  assert.equal(status.targetSha, 'upstreamsha')
  assert.equal(status.forkUpstream, 'upstream/main')
  assert.equal(status.branch, 'main', 'the rest of the payload must survive')
})

test('the off switch disables the probe entirely', async () => {
  const git = forkGit()
  const status = await withForkUpstreamStatus(
    Promise.resolve(originStatus()),
    git,
    { env: { HERMES_FORK_MERGE: '0' } }
  )
  assert.equal(status.behind, 0, 'must report exactly what origin said')
  assert.equal(status.forkUpstream, undefined)
  assert.deepEqual(git.calls, [], 'must not even talk to git when switched off')
})

test('leaves the payload alone when origin already has something to report', async () => {
  const git = forkGit()
  const status = await withForkUpstreamStatus(
    Promise.resolve(originStatus({ behind: 4 })),
    git,
    { env: {} }
  )
  assert.equal(status.behind, 4)
  assert.deepEqual(git.calls, [], 'origin stays authoritative when it has an answer')
})

test('leaves an errored or unsupported payload alone', async () => {
  const git = forkGit()
  const errored = await withForkUpstreamStatus(
    Promise.resolve({ supported: true, branch: 'main', error: 'fetch-failed' }),
    git,
    { env: {} }
  )
  assert.equal(errored.error, 'fetch-failed')
  const unsupported = await withForkUpstreamStatus(
    Promise.resolve({ supported: false, reason: 'not-a-git-checkout' }),
    git,
    { env: {} }
  )
  assert.equal(unsupported.supported, false)
  assert.deepEqual(git.calls, [], 'a broken check must not be papered over with a probe')
})

test('a rejection passes through to the caller', async () => {
  await assert.rejects(
    () => withForkUpstreamStatus(Promise.reject(new Error('check exploded')), forkGit(), { env: {} }),
    /check exploded/
  )
})

test('a probe failure returns the original payload untouched', async () => {
  const git = async () => {
    throw new Error('git is broken')
  }
  const status = await withForkUpstreamStatus(Promise.resolve(originStatus()), git, { env: {} })
  assert.equal(status.behind, 0)
  assert.equal(status.forkUpstream, undefined)
})

test('a null or non-object payload passes straight through', async () => {
  const git = forkGit()
  assert.equal(await withForkUpstreamStatus(Promise.resolve(null), git, { env: {} }), null)
  assert.equal(await withForkUpstreamStatus(Promise.resolve(undefined), git, { env: {} }), undefined)
  assert.deepEqual(git.calls, [], 'nothing to decorate means nothing to probe')
})

// FAIL-BEFORE: the sha lookup's exit code was ignored, so a failing
// `rev-parse upstream/main` yielded targetSha:'' while behind stayed 43. The
// renderer refuses to raise the notification without a targetSha
// (src/store/updates.ts), so the popup went silent while claiming 43 commits —
// the precise failure this feature exists to remove.
test('a failed sha lookup reports nothing rather than a count with no sha', async () => {
  const git = gitStub([
    ['remote get-url upstream', ok('https://example.com/x.git')],
    ['rev-list', ok('43')],
    ['rev-parse', fail('unknown revision')]
  ])
  assert.equal(await resolveForkUpstreamStatus({ git, branch: 'main' }), null)
})

test('an empty sha with a zero exit is also rejected', async () => {
  const git = gitStub([
    ['remote get-url upstream', ok('https://example.com/x.git')],
    ['rev-list', ok('43')],
    ['rev-parse', ok('   \n')]
  ])
  assert.equal(await resolveForkUpstreamStatus({ git, branch: 'main' }), null)
})

test('a good origin targetSha is never replaced by an empty one', async () => {
  const git = gitStub([
    ['remote get-url upstream', ok('https://example.com/x.git')],
    ['rev-list', ok('43')],
    ['rev-parse', fail('boom')]
  ])
  const status = await withForkUpstreamStatus(
    Promise.resolve(originStatus({ targetSha: 'origin-sha' })),
    git,
    { env: {} }
  )
  assert.equal(status.targetSha, 'origin-sha')
  assert.equal(status.behind, 0)
})
