# Source code offer

This file ships with every unbork image and package, next to `LICENSE`
and `THIRD-PARTY-NOTICES` (in `/usr/share/doc/unbork/`).

## unbork itself

unbork is free software under the GNU Affero General Public License,
version 3 or later (`LICENSE`). Its complete source is at
<https://github.com/open-unbork/unbork>.

The exact commit an image was built from is in its
`org.opencontainers.image.revision` label
(`docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' <image>`)
and in Settings › System, which reads `GET /api/version` (`commit_sha`).
Each release's source is also on its GitHub release page, as "Source
code", under the tag `v<version>`.

If you run a modified version of unbork for users who reach it over a
network, section 13 of the AGPL requires you to offer them the source of
your version too.

## Third-party components

`THIRD-PARTY-NOTICES` lists every third-party component in the image or
package, with its version and licence, followed by the licence texts.
Some of them are under the GPL or the LGPL. Their corresponding source is
available from the same kind of place they were distributed from:

- **Python packages** (for example `ansible-core`): the source
  distribution of the listed version on PyPI,
  `https://pypi.org/project/<name>/<version>/#files`, or
  `pip download --no-binary :all: --no-deps <name>==<version>`.
- **npm packages bundled into the web interface**: the listed version on
  the npm registry, `npm pack <name>@<version>`, and the project's
  repository named in its `package.json`.
- **Debian packages in the image** (for example `git`, `openssh-client`):
  the source package of the listed version from Debian's archive,
  `https://snapshot.debian.org/package/<source>/<version>/`, or
  `apt-get source <package>=<version>` on a Debian system with `deb-src`
  entries.

These locations are kept by PyPI, npm and Debian, not by the unbork
project. If one of them stops serving a version this image contains, ask
us and we will provide that source.

## Written offer

For three years from the date of the image or package (its build date is
in `GET /api/version` and Settings › System), the unbork project will give
anyone who asks a copy of the complete corresponding source of any GPL or
LGPL component in it, on a physical medium or by download, for no more
than the cost of providing it.

Requests: open an issue at <https://github.com/open-unbork/unbork/issues>
titled "Source request", naming the image tag or package and the
component.
<!-- D-31: the contact address for this offer and the wording of this file
are waiting for Dennis's sign-off (planning/DECISIONS.md). Until then the
issue tracker above is the interim channel. -->
