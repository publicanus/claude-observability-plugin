# Changelog

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
