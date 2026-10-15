#!/bin/sh
set -eu

cli=/srv/node_modules/.bin/sentry-cli
repo=$(mktemp -d)

git -C "$repo" init -q -b main
git -C "$repo" config user.name "Live Committer"
git -C "$repo" config user.email "committer@live.test"
git -C "$repo" remote add origin https://github.com/sophotechlabs/live-shop.git
mkdir -p "$repo/live"
printf 'def charge(order, rate):\n    return order["amount"] * rate / order["discount"]\n' > "$repo/live/python_app.py"
git -C "$repo" add live/python_app.py
git -C "$repo" commit -q -m "charge: divide by the order discount"

cd "$repo"
"$cli" --log-level "${SENTRY_LOG_LEVEL:-warn}" releases new --project "$SENTRY_PROJECT" 1.4.2
"$cli" --log-level "${SENTRY_LOG_LEVEL:-warn}" releases set-commits --local 1.4.2
