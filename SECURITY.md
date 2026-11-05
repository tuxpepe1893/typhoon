# Security policy

Typhoon handles local-network device keys, optional Tornado cloud sessions (stored in macOS Keychain), and HomeKit pairing material. Please report vulnerabilities privately.

## Reporting

Use GitHub's [private vulnerability reporting](https://github.com/tuxpepe1893/typhoon/security/advisories/new) for this repository. Don't open a public issue for a security problem.

Include the affected version or commit, the impact, and reproduction steps. Redact any real credentials, MAC addresses, or pairing codes.

## Scope

In scope: the Typhoon app, CLI, HomeKit bridge, install scripts, and how they store or transmit secrets.

Out of scope: vulnerabilities in the air conditioner firmware, the Broadlink/AUX protocol itself, or Tornado's cloud service. Report those to the vendor.

Only the latest release on `main` is supported.
