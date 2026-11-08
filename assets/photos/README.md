# Country photos (free-licence)

`autoshorts.longform.country_photos` stores Wikimedia Commons photos used as country-themed
backdrops in longform videos. Each country gets a folder named by slug (for example
`canada/`, `new-zealand/`) containing:

- the downloaded JPEG/PNG files (at least 1280 px wide), and
- `manifest.json`, which lists every file with its licence, licence URL, artist, source page and
  the attribution string to show on screen.

Accepted licences: CC0, Public domain, CC BY and CC BY-SA (any version). NC, ND, fair use and
unknown licences are rejected.

Image binaries are downloaded on demand and must not be committed. The manifests are the record
of what was used and how to credit it.
