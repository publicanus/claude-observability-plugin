# Changelog

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
