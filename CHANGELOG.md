# Changelog

## [0.7.0](https://github.com/sophotechlabs/pandora/compare/v0.6.0...v0.7.0) (2026-09-04)


### Features

* add scoped deploy and attachment parity ([4a0b08d](https://github.com/sophotechlabs/pandora/commit/4a0b08d108132133933a676f86009ede386c8453))
* **api:** split reading an issue from reading its payload ([e74b173](https://github.com/sophotechlabs/pandora/commit/e74b1731e79a1b051b2f8b1bd277cabab1b99acb))
* **artifacts:** source maps, resolved when the page is rendered ([87e3f4e](https://github.com/sophotechlabs/pandora/commit/87e3f4e15c6cc467e2b99636595f8e6af6f96f25))
* **core:** reconcile projects, tokens, rules and links from a file ([322313d](https://github.com/sophotechlabs/pandora/commit/322313d7fdacb0179904fbfd9b26655f1bd79ef5))
* **core:** teams and ownership rules in the config file ([5377554](https://github.com/sophotechlabs/pandora/commit/5377554401ef98aad1f028e3c48cc378f4e01c77))
* **deploy:** a helm chart, a one-container quickstart and pinned dependencies ([0b0b227](https://github.com/sophotechlabs/pandora/commit/0b0b2279cd4a4e4888bf95db3b3664970330d254))
* **deploy:** single sign-on settings in the chart ([d7133a1](https://github.com/sophotechlabs/pandora/commit/d7133a18159457b60c8403979d70ab161caae3c9))
* **events:** keep the stack trace an SDK sends, and render it ([ef10f6b](https://github.com/sophotechlabs/pandora/commit/ef10f6be8340d2d9404c3cb27d3398eeb38c57de))
* grouping depth, releases, scale, more doors and search ([269b67f](https://github.com/sophotechlabs/pandora/commit/269b67f3de16ab27871bbb736504d3a47efffd46))
* **ingest:** quotas and spike protection, answered with the rate-limit header ([70c484d](https://github.com/sophotechlabs/pandora/commit/70c484d522bb72ecdee3c074871deeb161e52d88))
* **issues:** outbound links built from the issue's own values ([6a44708](https://github.com/sophotechlabs/pandora/commit/6a44708d0d53b5e7b5ec816b32542dc189d31e4d))
* **issues:** show what else was firing in the same window ([08d6ceb](https://github.com/sophotechlabs/pandora/commit/08d6ceb1efedf61dd4b248433d8060dd50d80d82))
* **issues:** snooze by time or by occurrence count, never forever ([00f9889](https://github.com/sophotechlabs/pandora/commit/00f988976602e303e859dcfb54ce7cd39b3b0e13))
* **mcp:** a read-only mcp server as an optional extra ([15aba4e](https://github.com/sophotechlabs/pandora/commit/15aba4e48005284a86bffa68acdc8ac1dc0c9d7e))
* **notify:** name the owner in the notification payload ([bc63b77](https://github.com/sophotechlabs/pandora/commit/bc63b775e45e87f6f3d30466c30e66a4ba85e140))
* **notify:** tell someone when an issue opens, regresses or wakes up ([ae88c4b](https://github.com/sophotechlabs/pandora/commit/ae88c4bad60952b1351dfdb5c9d9211f551f6239))
* **parity:** complete reporting and retention gaps ([c6f9e97](https://github.com/sophotechlabs/pandora/commit/c6f9e971585402e243810dc8e7ba70662077cc83))
* **people:** teams, roles, ownership rules and an audit trail ([572dcc4](https://github.com/sophotechlabs/pandora/commit/572dcc420aebe9286fba324e729def61502495bf))
* **scrub:** redact events already stored and delete a single occurrence ([709e343](https://github.com/sophotechlabs/pandora/commit/709e3432cec0a3239f86486597ac6f4e4b0d4514))
* **scrub:** redact secrets before the write and drop what a rule refuses ([f3e4cc9](https://github.com/sophotechlabs/pandora/commit/f3e4cc9e27c53c6c160450c05dc97c247c19de28))
* **ui:** render an issue as markdown ([460919a](https://github.com/sophotechlabs/pandora/commit/460919a97b1f8c5a69c22875ea97573549c13ed8))
* **ui:** scope the operator UI to a team and show who owns an issue ([68e4265](https://github.com/sophotechlabs/pandora/commit/68e4265acee7be61e2da69549a9fc8ce8319863a))
* **wrap:** a Go command wrapper for cron check-ins ([129d69d](https://github.com/sophotechlabs/pandora/commit/129d69d1928555f715b1672ecba41322b561db91))


### Bug Fixes

* **artifacts:** harden chunked debug file uploads ([7007a65](https://github.com/sophotechlabs/pandora/commit/7007a6509f17b8245101e1ce440b51d33e398f82))
* **auth:** enforce project scope and OIDC revocation ([14407a0](https://github.com/sophotechlabs/pandora/commit/14407a086df994cc3853f2573d78606b72b36d87))
* **chart:** make SQLite deployments safe and bootable ([76109a3](https://github.com/sophotechlabs/pandora/commit/76109a35cd1fcdefde3862bb5ee20877081c8eb6))
* **config:** read a blank environment variable as unset ([e89d6ed](https://github.com/sophotechlabs/pandora/commit/e89d6ed3da5b3eb8fae542d7b187b4a35f6cedf7))
* **e2e:** the issue title is the exception type and its culprit ([65266c0](https://github.com/sophotechlabs/pandora/commit/65266c097f6ba403a5b0ec7786df1e60bcc22618))
* **ingest:** reject malformed and unsafe client payloads ([9c92648](https://github.com/sophotechlabs/pandora/commit/9c9264801a6783dcb453736e2e329599c182b3a4))
* **notify:** claim deliveries before sending ([c86aad3](https://github.com/sophotechlabs/pandora/commit/c86aad3ec5ae36651bffa66d92b7a4a6600c0a41))
* **operations:** schedule maintenance sweeps ([6ffecfe](https://github.com/sophotechlabs/pandora/commit/6ffecfef9fceb940e063a4abdfe019030d90d4f1))
* **releases:** make counters and time windows race-safe ([a655748](https://github.com/sophotechlabs/pandora/commit/a65574829cb567f8be8f5701a6cf5559a0db06d5))
* satisfy ruff on the code committed since 0.6.0 ([dd47eef](https://github.com/sophotechlabs/pandora/commit/dd47eefe08cd191b795bd9276ec4186f32b4d3e3))


### Miscellaneous

* license pandora under FSL-1.1-ALv2 ([6239af9](https://github.com/sophotechlabs/pandora/commit/6239af9d4d9d80adafc21e05ee9005a87b3ec77a))


### CI

* **forgejo:** stop leaving a token in .git/config after checkout ([0053f18](https://github.com/sophotechlabs/pandora/commit/0053f18bb2a942b3dec3c11fe0a6e0dbad0743e7))
* **github:** lint, types, migrations, tests, e2e, codeql and the repo checks ([6962eaa](https://github.com/sophotechlabs/pandora/commit/6962eaa8b479e95f5ed2a7556e6ded014d766632))
* **release:** release-please, and the image and chart on ghcr ([f72a3d2](https://github.com/sophotechlabs/pandora/commit/f72a3d29bb716646179d64094d4bf4f695b576a2))
* run every gate from the justfile, on a host as well as in compose ([584b313](https://github.com/sophotechlabs/pandora/commit/584b31388e4817b7b8667069575f4f98729af995))


### Documentation

* **readme:** build badges and where the image is published ([aa88d45](https://github.com/sophotechlabs/pandora/commit/aa88d45771cfdc976cc5d1c45b0d131cdd6a2718))
* **readme:** multi-user, ownership, history and the payload scope ([97783ba](https://github.com/sophotechlabs/pandora/commit/97783ba1beed330b67d5d5b2d916cbbda51d5269))


### Tests

* **audit:** make pruning cutoff deterministic ([6ef1903](https://github.com/sophotechlabs/pandora/commit/6ef190325dffd0aa58fc2c0e579ae9ae3e69ac98))
* **ci:** add real-client, Kind, and quality gates ([0b65304](https://github.com/sophotechlabs/pandora/commit/0b65304fc2537d5e6ee241af822a24ade25ca5de))
* **ci:** harden focused and cluster coverage ([6965130](https://github.com/sophotechlabs/pandora/commit/69651306622c2481e5ba943fc5cbcffc6989bc5e))
* cover the branches a one-line conditional was hiding ([3a92803](https://github.com/sophotechlabs/pandora/commit/3a9280372bdd8ce90ffc88c36f28b421c9a6e76c))
* **e2e:** drive the operator UI through a real browser ([af7184b](https://github.com/sophotechlabs/pandora/commit/af7184b5f17599cb26d44a041deba4278c3d2590))
* hold the github gate to the same parity as the forgejo one ([3c1b916](https://github.com/sophotechlabs/pandora/commit/3c1b9163c55bf96d616e541e45dc008e2036766a))


### Build

* build on the host network and mount the source only for local work ([c98392f](https://github.com/sophotechlabs/pandora/commit/c98392f68cef2ef2614e36c95e07b65bf968d171))
* pin the toolchain the new gates need, and fix what they found ([f2cffc1](https://github.com/sophotechlabs/pandora/commit/f2cffc1b95c68ed326ab1f86ccea80d85d18af37))
* publish no fixed host port, so checkouts can run in parallel ([d33e564](https://github.com/sophotechlabs/pandora/commit/d33e5644eeec3eb20ae75f287aab8cb065f181ee))
