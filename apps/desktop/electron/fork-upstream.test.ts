import assert from 'node:assert/strict'

import { test } from 'vitest'

import { parseBehindCount, resolveForkUpstreamStatus, shouldProbeUpstream } from './fork-upstream'

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
  assert.equal(shouldProbeUpstream({ originBehind: 0, supported: false }), false)
})

test('a missing git runner is handled, not thrown', async () => {
  assert.equal(await resolveForkUpstreamStatus({ branch: 'main' }), null)
  assert.equal(await resolveForkUpstreamStatus({ git: gitStub([]), branch: '' }), null)
})
