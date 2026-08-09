# Vendored front-end assets

These files are served from the Pi, never fetched from a CDN. That keeps the
dashboard working without internet access, and keeps third-party script
execution off a page that can reboot the Pi.

Because nothing here is installed by a package manager at deploy time, there is
no `npm install` on the Pi, no post-install script, and no automatic update. A
later compromise of an upstream package cannot reach this repo on its own —
re-vendoring is a deliberate act that shows up as a diff.

Record the version and checksum of anything added here, so that diff is
reviewable.

## echarts.min.js

- **Version**: 6.1.0
- **Source**: `https://registry.npmjs.org/echarts/-/echarts-6.1.0.tgz`, `package/dist/echarts.min.js`
- **Licence**: Apache-2.0 — see `echarts-LICENSE.txt`
- **Size**: 1,121,883 bytes
- **SHA-256**: `b66b25aeb4df84e33199dc21694014d336d222cbd9deb0e5a7c14bd6aa0d0fd0`

Verify with:

```sh
sha256sum -c - <<'EOF'
b66b25aeb4df84e33199dc21694014d336d222cbd9deb0e5a7c14bd6aa0d0fd0  bbmon/web/static/vendor/echarts.min.js
EOF
```

Checked at vendoring time: the bundle makes no `require()` calls (zrender is
inlined rather than fetched), and contains no outbound URLs beyond licence text
and W3C XML namespace identifiers. It includes the `boxplot` series type that
M5's hourly latency chart needs, so no plugin is required.
