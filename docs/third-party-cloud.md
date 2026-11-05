# Optional Tornado cloud transport

`typhoon/cloud.py` adapts the published Tornado WIFI 3 / AUX protocol in
[romfreiman/tornado-aircon-custom-component](https://github.com/romfreiman/tornado-aircon-custom-component),
particularly [the AUX cloud client](https://github.com/romfreiman/tornado-aircon-custom-component/blob/main/custom_components/tornado/aux_cloud/__init__.py)
and [climate mappings](https://github.com/romfreiman/tornado-aircon-custom-component/blob/main/custom_components/tornado/climate.py).
Credit to Rom Freiman and the project's contributors. The upstream README
identifies USA as the region used in verified Israeli Tornado deployments.
This project offers USA and EU explicitly; it does not try credentials against
additional regions automatically.

The implementation uses Python's standard HTTP client and `cryptography`,
preserves HTTPS certificate validation, refuses redirects, and performs no
automatic retry of writes. The published app IDs, license, IV, and signing salts
are protocol constants, not user credentials.

Treat the entire session (`userid`, `loginsession`) and private device inventory
(`cookie`, including its AES key, and `devSession`) as secrets. Store them in
macOS Keychain. Only `public_device()` is intended for CLI output or ordinary
configuration files. Passwords are used transiently for login and never returned
by the module. Vendor error bodies are not included in user-facing exceptions.

`verified: true` with `verification: "cloud-readback"` means an online QueryState
request and a separate direct SDK `get` returned settings, and after a write,
those settings matched the requested changes. Cached inventory and the write
acknowledgement never satisfy this check. The vendor service's own implementation
is opaque: this is cloud-reported readback, not proof of a compressor, fan, or
display physically changing. Human observation remains necessary for physical
acceptance. Synthetic tests exercise request formation and these verification
rules; they do not constitute live account or air-conditioner validation.

The adapted source is distributed under its upstream MIT license:

```text
MIT License

Copyright (c) 2019 - 2025  Joakim Sørensen @ludeeus

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
