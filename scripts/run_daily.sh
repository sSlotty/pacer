#!/bin/zsh
# Daily entry point for launchd (see scripts/com.pacer.daily.plist).
# Runs the summary, then commits data/garmin.db so history survives on the remote.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/Library/Logs/pacer.log"
LOCK="$REPO/.pacer.lock"

cd "$REPO" || exit 1
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T') starting ==="

# Never let two runs overlap (a late wake-up can fire a missed job while one runs).
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "another run holds the lock; exiting"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Wait for the network: on wake-from-sleep launchd fires before Wi-Fi is back.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  /sbin/ping -c1 -t2 connect.garmin.com >/dev/null 2>&1 && break
  echo "waiting for network..."
  sleep 15
done

"$REPO/.venv/bin/python" main.py
STATUS=$?
echo "main.py exited $STATUS"

# Keep the database history in the repo, same as the GitHub workflow used to.
if [ -n "$(git status --porcelain data/garmin.db)" ]; then
  git add data/garmin.db
  git -c user.name="pacer" -c user.email="pacer@local" commit -q -m "chore: update garmin.db ($(date -u +%F))"

  # A manual workflow run may have committed a database in the meantime. This machine
  # holds the newest data, so replay on top of the remote and keep the local file.
  git fetch -q origin main
  if ! git merge-base --is-ancestor origin/main HEAD; then
    if ! git rebase -q origin/main; then
      git checkout --theirs data/garmin.db && git add data/garmin.db
      GIT_EDITOR=true git rebase --continue >/dev/null || git rebase --abort
    fi
  fi
  git push -q origin main || echo "push failed; will retry with the next run"
fi

echo "=== $(date '+%F %T') done ==="
exit $STATUS
