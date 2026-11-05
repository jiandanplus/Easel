# Node / Pi SDK dependency notices

The actual runtime payload preserves Node's original `node/LICENSE` and
`node/README.md`, plus every installed npm dependency's original files, including
LICENSE, LICENCE, COPYING and NOTICE files. `dependency-provenance.json` inside
the payload enumerates their exact relative paths. Those originals are included
in the runtime manifest's complete SHA256 inventory and the app resource
inventory; they are not replaced by an SPDX label or this notice.

`../dependencies-provenance.json` records each installed locked package's public
registry URL, SHA512 integrity, version and package-declared license. Node is
the official v24.22.1 Darwin ARM64 distribution, verified against its fixed
official archive SHA256. npm lifecycle scripts were disabled. No original
upstream archive signature verification is claimed.
