# Changelog

## [3.1.2](https://github.com/publicanus/claude-observability-plugin/compare/v3.1.1...v3.1.2) (2026-11-01)


### Bug Fixes

* **hook:** an agent's work after it woke up on its own reaches Langfuse ([#105](https://github.com/publicanus/claude-observability-plugin/issues/105)) ([#116](https://github.com/publicanus/claude-observability-plugin/issues/116)) ([1936758](https://github.com/publicanus/claude-observability-plugin/commit/1936758ff8a3a662e011c60a1c5264a7c6048773))

## [3.1.1](https://github.com/publicanus/claude-observability-plugin/compare/v3.1.0...v3.1.1) (2026-11-01)


### Bug Fixes

* **hook:** an agent's message after the turn ended starts a turn, so the session's reply reaches Langfuse ([#106](https://github.com/publicanus/claude-observability-plugin/issues/106)) ([#112](https://github.com/publicanus/claude-observability-plugin/issues/112)) ([fc1355d](https://github.com/publicanus/claude-observability-plugin/commit/fc1355d033ce73b3e98357512b52dd26ba92236e))
* **hurt:** a /hurt note that starts with '/' reaches the trace as typed on Windows ([#84](https://github.com/publicanus/claude-observability-plugin/issues/84)) ([#115](https://github.com/publicanus/claude-observability-plugin/issues/115)) ([fe723ed](https://github.com/publicanus/claude-observability-plugin/commit/fe723ed41cc24c3555ccfef2da7829469032e144))

## [3.1.0](https://github.com/publicanus/claude-observability-plugin/compare/v3.0.5...v3.1.0) (2026-11-01)


### Features

* **setup:** consultants on a GitHub repo get its Langfuse keys from the repo ([#89](https://github.com/publicanus/claude-observability-plugin/issues/89)) ([#103](https://github.com/publicanus/claude-observability-plugin/issues/103)) ([36cd7c1](https://github.com/publicanus/claude-observability-plugin/commit/36cd7c1f7f87bcb53b8984f05184e60c86bdccc3))
* **setup:** whoever manages a GitHub repo can take people or laptops off and put in a new key pair ([#90](https://github.com/publicanus/claude-observability-plugin/issues/90)) ([#109](https://github.com/publicanus/claude-observability-plugin/issues/109)) ([d8455c0](https://github.com/publicanus/claude-observability-plugin/commit/d8455c08f5034fc6f55ee7042886b4d6d8c78e48))


### Bug Fixes

* **hook:** a 403 answer warns that Langfuse cannot be reached ([#110](https://github.com/publicanus/claude-observability-plugin/issues/110)) ([db35e6a](https://github.com/publicanus/claude-observability-plugin/commit/db35e6ae8ecb180754c6a1d665c2349f3b3f11a9)), closes [#85](https://github.com/publicanus/claude-observability-plugin/issues/85)
* **hurt:** /hurt outlasts a brief hold on its queue file on Windows ([#83](https://github.com/publicanus/claude-observability-plugin/issues/83)) ([#111](https://github.com/publicanus/claude-observability-plugin/issues/111)) ([3be8413](https://github.com/publicanus/claude-observability-plugin/commit/3be8413c9cfcb6e50cdcd514e1abbd3f658bec5b))

## [3.0.5](https://github.com/publicanus/claude-observability-plugin/compare/v3.0.4...v3.0.5) (2026-10-31)


### Bug Fixes

* **hook:** a 403 answer keeps the turns waiting to be sent ([#81](https://github.com/publicanus/claude-observability-plugin/issues/81)) ([#102](https://github.com/publicanus/claude-observability-plugin/issues/102)) ([894522c](https://github.com/publicanus/claude-observability-plugin/commit/894522cdbebd04c0b7426e954609c0802e2778c3))
* **hook:** waiting turns are dropped after 30 days ([#82](https://github.com/publicanus/claude-observability-plugin/issues/82)) ([#99](https://github.com/publicanus/claude-observability-plugin/issues/99)) ([5510af6](https://github.com/publicanus/claude-observability-plugin/commit/5510af662ce1f14d89d24abc5236c7cf15d8dbdd))
* **hurt:** the end of a reply waits once for a silent Langfuse, however many notes are queued ([#78](https://github.com/publicanus/claude-observability-plugin/issues/78)) ([#101](https://github.com/publicanus/claude-observability-plugin/issues/101)) ([92e8e76](https://github.com/publicanus/claude-observability-plugin/commit/92e8e76fd92fd8d25d689e379daefc705d43a2a1))

## [3.0.4](https://github.com/publicanus/claude-observability-plugin/compare/v3.0.3...v3.0.4) (2026-10-31)


### Bug Fixes

* **hook:** passwords, tokens and API keys in common forms are masked ([#79](https://github.com/publicanus/claude-observability-plugin/issues/79)) ([#97](https://github.com/publicanus/claude-observability-plugin/issues/97)) ([108d112](https://github.com/publicanus/claude-observability-plugin/commit/108d112449d873346c8b04d368ae331cd37c6904))

## [3.0.3](https://github.com/publicanus/claude-observability-plugin/compare/v3.0.2...v3.0.3) (2026-10-31)


### Bug Fixes

* **hurt:** a /hurt after a turn stopped with Esc, or ended by an API error, lands on that turn ([#73](https://github.com/publicanus/claude-observability-plugin/issues/73)) ([#86](https://github.com/publicanus/claude-observability-plugin/issues/86)) ([43ee9c9](https://github.com/publicanus/claude-observability-plugin/commit/43ee9c920fc758579fa8edb7fff9c84c1114928b))

## [3.0.2](https://github.com/publicanus/claude-observability-plugin/compare/v3.0.1...v3.0.2) (2026-10-31)


### Bug Fixes

* **hook:** Claude stops as usual when uv cannot download the hook's packages ([#72](https://github.com/publicanus/claude-observability-plugin/issues/72)) ([#77](https://github.com/publicanus/claude-observability-plugin/issues/77)) ([69d6032](https://github.com/publicanus/claude-observability-plugin/commit/69d6032aba9d7a82dfb10c5d653e84c52cde6bb9))

## [3.0.1](https://github.com/publicanus/claude-observability-plugin/compare/v3.0.0...v3.0.1) (2026-10-30)


### Bug Fixes

* **hook:** send x-langfuse-ingestion-version: 4 with every span batch ([#75](https://github.com/publicanus/claude-observability-plugin/issues/75)) ([6e4ef1a](https://github.com/publicanus/claude-observability-plugin/commit/6e4ef1aeca993f6930dcfa894cc418239f3c8e3d))

## [3.0.0](https://github.com/publicanus/claude-observability-plugin/compare/v2.0.1...v3.0.0) (2026-10-30)


### ⚠ BREAKING CHANGES

* **hook:** tracing now uses only the Langfuse keys and address in the repo's own .claude/settings.local.json (in a worktree: the main checkout's file when it holds both keys, else the worktree's own). Keys or an address set in the terminal, in direnv, or with /plugin configure (machine-wide userConfig) are no longer used, and the userConfig fields LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL are gone. Without an address in that file, traces go to Langfuse Cloud EU. To keep tracing a repo, run /mate-record:langfuse-setup in it and paste the keys into the file it opens.

### Bug Fixes

* **hook:** a session is traced only with its repo's own keys ([#56](https://github.com/publicanus/claude-observability-plugin/issues/56)) ([82c75d1](https://github.com/publicanus/claude-observability-plugin/commit/82c75d1c9c10dcf5d8a9d1bd36c55988c8f46670))
* **hook:** the session's reply to a finished agent reaches Langfuse ([#60](https://github.com/publicanus/claude-observability-plugin/issues/60)) ([#66](https://github.com/publicanus/claude-observability-plugin/issues/66)) ([a243fdc](https://github.com/publicanus/claude-observability-plugin/commit/a243fdc1ea7f518e69b217a7f85e25e41b64bcb1))

## [2.0.1](https://github.com/publicanus/claude-observability-plugin/compare/v2.0.0...v2.0.1) (2026-10-30)


### Bug Fixes

* **hook:** a /hurt typed while turns wait for an agent reaches their trace ([#65](https://github.com/publicanus/claude-observability-plugin/issues/65)) ([#67](https://github.com/publicanus/claude-observability-plugin/issues/67)) ([1ad60f5](https://github.com/publicanus/claude-observability-plugin/commit/1ad60f5baf072f7f5d3922f7fb20018d120e33a5))

## [2.0.0](https://github.com/publicanus/claude-observability-plugin/compare/v1.7.0...v2.0.0) (2026-10-29)


### ⚠ BREAKING CHANGES

* the plugin is now mate-record, installed with `claude plugin install mate-record@mate-marketplace`. The commands are /mate-record:langfuse-setup and /mate-record:hurt. A repo's own no-keys steps now live in .claude/mate-record/no-keys-steps/. Do not uninstall langfuse-observability or mate-hurt: a later mate-core release brings mate-record in, and mate-marketplace then moves each install over to mate-record.

### Features

* **hook:** each observation names its component, plugin and version ([#44](https://github.com/publicanus/claude-observability-plugin/issues/44)) ([#50](https://github.com/publicanus/claude-observability-plugin/issues/50)) ([4425d19](https://github.com/publicanus/claude-observability-plugin/commit/4425d19731508a6160224d131f936f68e72d06e8))
* **hook:** each round of a continued agent is a run of its own ([#53](https://github.com/publicanus/claude-observability-plugin/issues/53)) ([#59](https://github.com/publicanus/claude-observability-plugin/issues/59)) ([f58e74c](https://github.com/publicanus/claude-observability-plugin/commit/f58e74c059bb8d963fb2ba37140437467752c86c))
* **hook:** each trace names its work item ([#46](https://github.com/publicanus/claude-observability-plugin/issues/46)) ([#54](https://github.com/publicanus/claude-observability-plugin/issues/54)) ([f108b07](https://github.com/publicanus/claude-observability-plugin/commit/f108b07f65a7f687ff5e426d3415620a8def36a5))
* the plugin becomes mate-record and ships /hurt ([#52](https://github.com/publicanus/claude-observability-plugin/issues/52)) ([#61](https://github.com/publicanus/claude-observability-plugin/issues/61)) ([87efe6c](https://github.com/publicanus/claude-observability-plugin/commit/87efe6c0c9cdb06d9e51b649bdae38bea98e4820))


### Bug Fixes

* **setup:** only the repo's own keys file counts as configured ([#55](https://github.com/publicanus/claude-observability-plugin/issues/55)) ([#58](https://github.com/publicanus/claude-observability-plugin/issues/58)) ([788d954](https://github.com/publicanus/claude-observability-plugin/commit/788d954f99f8347a800ad7eaacc680ccdcf77a46))

## [1.7.0](https://github.com/publicanus/claude-observability-plugin/compare/v1.6.0...v1.7.0) (2026-10-27)


### Features

* **hook:** every trace lists the plugins its turn used, with their versions ([#37](https://github.com/publicanus/claude-observability-plugin/issues/37)) ([#48](https://github.com/publicanus/claude-observability-plugin/issues/48)) ([f2114d6](https://github.com/publicanus/claude-observability-plugin/commit/f2114d63962d1e8362b987bd81b0c90b41af75aa))
* **hook:** turns that failed to reach Langfuse are sent again later ([#20](https://github.com/publicanus/claude-observability-plugin/issues/20)) ([#49](https://github.com/publicanus/claude-observability-plugin/issues/49)) ([11b7b34](https://github.com/publicanus/claude-observability-plugin/commit/11b7b349314e737dff49cfe414248def4eeee983))


### Bug Fixes

* **hook:** two sessions on one Windows machine do not spoil each other's saved state ([#33](https://github.com/publicanus/claude-observability-plugin/issues/33)) ([#45](https://github.com/publicanus/claude-observability-plugin/issues/45)) ([f21d2b6](https://github.com/publicanus/claude-observability-plugin/commit/f21d2b66d9360aabe4725fe283b191cad86dbc35))

## [1.6.0](https://github.com/publicanus/claude-observability-plugin/compare/v1.5.1...v1.6.0) (2026-10-27)


### Features

* a setup skill and per-OS session-start steps help developers put Langfuse keys in a repo ([#32](https://github.com/publicanus/claude-observability-plugin/issues/32)) ([d089bc4](https://github.com/publicanus/claude-observability-plugin/commit/d089bc462f6f5ab4acfbc0e852da51b90441b6c2))
* **hook:** each trace carries its repo name, so token use can be totalled per repo ([#35](https://github.com/publicanus/claude-observability-plugin/issues/35)) ([b774a56](https://github.com/publicanus/claude-observability-plugin/commit/b774a567b211c94408720f13b107373269fdadb2))
* **hook:** secrets show in Langfuse as [REDACTED], not as the secret ([#23](https://github.com/publicanus/claude-observability-plugin/issues/23)) ([#40](https://github.com/publicanus/claude-observability-plugin/issues/40)) ([f99494b](https://github.com/publicanus/claude-observability-plugin/commit/f99494b1827991584fb543089dad988cb1682142))
* **hook:** session start says when the hook cannot run on this machine ([#21](https://github.com/publicanus/claude-observability-plugin/issues/21)) ([#42](https://github.com/publicanus/claude-observability-plugin/issues/42)) ([62368ea](https://github.com/publicanus/claude-observability-plugin/commit/62368eadda85b88b040dd4e3a7b408d60bbdeb72))

## [1.5.1](https://github.com/publicanus/claude-observability-plugin/compare/v1.5.0...v1.5.1) (2026-10-24)


### Bug Fixes

* **hook:** session data goes only to Langfuse Cloud EU or a self-hosted Langfuse ([#30](https://github.com/publicanus/claude-observability-plugin/issues/30)) ([3c3d2d4](https://github.com/publicanus/claude-observability-plugin/commit/3c3d2d42a05ebd3f4dbaf526452fb3f3da54dc9b)), closes [#24](https://github.com/publicanus/claude-observability-plugin/issues/24)

## [1.5.0](https://github.com/publicanus/claude-observability-plugin/compare/v1.4.1...v1.5.0) (2026-10-24)


### Features

* **hook:** each trace carries the real user without anyone typing it ([#27](https://github.com/publicanus/claude-observability-plugin/issues/27)) ([b99bb9e](https://github.com/publicanus/claude-observability-plugin/commit/b99bb9e795be3a4c127f5449a0952808f005113f))


### Bug Fixes

* **hook:** queued /hurt annotations keep attaching on Langfuse v4 ([#26](https://github.com/publicanus/claude-observability-plugin/issues/26)) ([09921e8](https://github.com/publicanus/claude-observability-plugin/commit/09921e84162843b874dc390f10fa4cb6308804ec))

## [1.4.1](https://github.com/publicanus/claude-observability-plugin/compare/v1.4.0...v1.4.1) (2026-10-15)


### Bug Fixes

* **hook:** a session without langfuse keys says once that it is not traced ([#16](https://github.com/publicanus/claude-observability-plugin/issues/16)) ([c467422](https://github.com/publicanus/claude-observability-plugin/commit/c46742267567583c10e88d005aecfa81c91573fd))
* **hook:** queued /hurt annotations attach, because the hurt score config is created with numeric category values ([#18](https://github.com/publicanus/claude-observability-plugin/issues/18)) ([83356f2](https://github.com/publicanus/claude-observability-plugin/commit/83356f2cd436c3e0f49d1d23878eea45045623e3))

## [1.4.0](https://github.com/publicanus/claude-observability-plugin/compare/v1.3.0...v1.4.0) (2026-08-26)


### Features

* wire release-please for automatic, visible plugin versioning ([#10](https://github.com/publicanus/claude-observability-plugin/issues/10)) ([170ecd2](https://github.com/publicanus/claude-observability-plugin/commit/170ecd2da9dbc843bca4f074e9467e05721ba53d))
