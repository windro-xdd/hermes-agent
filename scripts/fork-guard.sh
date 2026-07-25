#!/bin/sh
# Prototype v3 of the fork-protection guard (reference-transaction hook).
#
# Purpose: refuse any ref update that would make fork-only commits UNRECOVERABLE,
# regardless of which code path issued it (hermes update, install.ps1,
# install.sh, hermes-setup.exe, or a human typing git reset --hard).
#
# Design history (each fix came from a failing test, see hooktest/):
#   v1 blocked every non-fast-forward branch move. Too broad: it also blocked
#      `git rebase`, stranding the repo mid-sequencer.
#   v2 stood aside during git's own multi-step operations (rebase/cherry-pick/
#      merge/bisect). Still too broad: it blocked `git reset --soft`, which only
#      moves the pointer and keeps the work staged.
#   v3 (this) adds the reflog-recoverability test below.
#
# The insight behind v3: a commit that leaves the branch tip is not "lost" while
# it is still in the reflog — `git reset --hard <sha>` brings it straight back.
# What makes fork work truly unrecoverable is losing the *only* record of it.
# So the guard's real job is narrower than "block non-fast-forwards": block the
# specific pattern where a fork commit is dropped in favour of an UPSTREAM tip,
# which is exactly what the three stock reset --hard sites do.

phase="$1"
[ "$phase" = "prepared" ] || exit 0

# Explicit opt-out for our own tooling doing deliberate history surgery.
[ -n "$HERMES_SYNC_ALLOW_REWRITE" ] && exit 0

GIT_DIR_PATH=$(git rev-parse --git-dir 2>/dev/null) || exit 0

# Stand aside during git's own multi-step operations: the sequencer moves the
# tip through intermediate states that look destructive but aren't, and aborting
# mid-flight strands the repo.
for state in rebase-merge rebase-apply CHERRY_PICK_HEAD MERGE_HEAD BISECT_LOG REVERT_HEAD; do
    [ -e "$GIT_DIR_PATH/$state" ] && exit 0
done

ZERO=0000000000000000000000000000000000000000
status=0

while read -r old new ref; do
    case "$ref" in
        refs/heads/*) : ;;
        *) continue ;;
    esac

    [ "$old" = "$ZERO" ] && continue
    [ "$new" = "$ZERO" ] && continue

    # Fast-forward: the new tip already contains the old tip. Nothing dropped.
    if git merge-base --is-ancestor "$old" "$new" 2>/dev/null; then
        continue
    fi

    lost=$(git rev-list "$new..$old" 2>/dev/null)
    [ -z "$lost" ] && continue

    # THE NARROW TEST: only guard the destructive-update pattern, i.e. the new
    # tip is a REMOTE-TRACKING tip (what `reset --hard origin/<branch>` does).
    # A local-to-local move (branch surgery, reset to another local commit)
    # stays the user's business and is reflog-recoverable.
    if ! git branch -r --contains "$new" 2>/dev/null | grep -q .; then
        continue
    fi

    # Only guard a move that goes BACKWARD off our own history onto the remote
    # line. A `reset --soft/--mixed HEAD~n` walks back along the SAME history —
    # the new tip is an ancestor of the old tip — and keeps every change in the
    # tree for the user to recommit. `reset --hard origin/<branch>` on a
    # diverged fork does not: the remote tip is NOT an ancestor of our tip,
    # because our fork commits sit off to the side.
    #
    # This is the discriminator git's hook interface does not give us directly,
    # and it is structural rather than inferred from working-tree state (an
    # earlier attempt inferred it from `git diff` and wrongly un-blocked the
    # real attack — see hooktest/ history).
    if git merge-base --is-ancestor "$new" "$old" 2>/dev/null; then
        continue
    fi

    for sha in $lost; do
        # Safe: the commit already lives on a remote.
        if git branch -r --contains "$sha" 2>/dev/null | grep -q .; then
            continue
        fi

        # Safe: an equivalent patch is already in the new history (rebase landed
        # it under a different sha).
        if [ -z "$(git cherry "$new" "$sha" 2>/dev/null | grep '^+')" ]; then
            continue
        fi

        echo "hermes fork guard: refusing to abandon $(git log -1 --format='%h %s' "$sha" 2>/dev/null)" >&2
        echo "  $ref would move to a remote tip, dropping this unpushed fork commit." >&2
        status=1
    done
done

if [ "$status" -ne 0 ]; then
    echo "" >&2
    echo "hermes fork guard BLOCKED a destructive ref update." >&2
    echo "Fork-only commits would have been lost. Use 'hermes-sync' to update safely." >&2
    echo "(Override for deliberate surgery: HERMES_SYNC_ALLOW_REWRITE=1)" >&2
fi

exit "$status"
