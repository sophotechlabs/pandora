#!/bin/sh
set -eu

cd /srv

rm -rf dist
npx esbuild src/app.js \
  --bundle \
  --minify \
  --platform=node \
  --external:@sentry/node \
  --sourcemap \
  --outfile=dist/app.js

npx sentry-cli sourcemaps inject dist
npx sentry-cli --log-level "${SENTRY_LOG_LEVEL:-warn}" sourcemaps upload --org "$SENTRY_ORG" --project "$SENTRY_PROJECT" dist

node dist/app.js
