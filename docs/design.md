# Native interface decisions

Typhoon uses Apple's native navigation and controls while keeping device state and connection failures visible. These are implemented design choices, not a claim that every HIG or accessibility check has passed.

## Navigation and materials

- **Mac:** `NavigationSplitView` provides the sidebar and detail area. Air Conditioner and Apple Home have separate pages, native toolbars, grouped forms, and system appearance. The connection footer stays outside scrolling controls.
- **Liquid Glass:** native navigation receives the system treatment. Primary power and bridge controls use `glassProminent` on macOS 26 and later. Older systems and Reduce Transparency use `borderedProminent`. Content sections use ordinary system backgrounds rather than layers of glass.
- **Appearance:** the app doesn't force dark mode. System foreground, secondary, background, and accent colors adapt to the user's appearance. Status also has text or symbols; color alone does not convey a successful connection.

These choices follow Apple's [Materials HIG](https://developer.apple.com/design/human-interface-guidelines/materials), [Adopting Liquid Glass](https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass), and [custom Liquid Glass guidance](https://developer.apple.com/documentation/swiftui/applying-liquid-glass-to-custom-views).

## Controls and feedback

Native controls provide keyboard or VoiceOver interaction where supported, with explicit labels for temperature, connection, and device controls. The interface adds no custom motion; native controls retain the system's accessibility behavior.

Controls are disabled during a request. Temperature, mode, fan, power, display, and swing update from verified readbacks, without optimistic success. Commands are tied to the selected device. Failed requests disable further mutations, show a readable error, and retain previously reported values only as stale state. Missing readings are not replaced with invented values. Cloud results on Mac are labeled as cloud readbacks.

## Apple Home

The Mac page distinguishes an enabled login service, a running bridge, a recent verified air-conditioner read, and an Apple Home pairing. Its QR code and PIN appear only after **Show Pairing Code**, and are hidden when leaving the page. The interface explains that the Mac must remain awake and available.

## UI verification checklist

Build and plist checks have passed. Runtime observations are recorded below; unchecked items remain open and must not be inferred from compilation.

On 11 October 2026, Computer Use inspected the installed Mac app in its current dark appearance without changing the AC or bridge settings. The controller showed a verified 18°C setpoint, cool/high, and a real room-temperature reading. At the observed near-default 884 × 744 window and enforced minimum 720 × 652 outer window (600-point content height), all controller rows and the connection footer remained visible. The Apple Home form scrolled to its final instructions at minimum size, and reported Running, Enabled, Responding, and Paired with fresh device-read times. Native accessibility inspection exposed the temperature incrementor and its current value, switch labels and values, mode/fan options, and toolbar actions; this was not a VoiceOver listening test. Light appearance and system accessibility settings were not changed during this pass.

One minor behavior remains: automatic Home status polling pauses while a pairing code is visible. Closing an already-open code panel allowed an old “Not paired” label to update to the actual “Paired” state. Pairing itself and the running bridge were unaffected; no code was requested or published during this inspection.

- [ ] Check both appearances on Mac, including selected controls, secondary text, errors, and pairing QR contrast.
- [x] Check the Mac near-default and minimum allowed window sizes: native sidebar, toolbar, form scrolling, and visible connection footer (observed sizes recorded above).
- [ ] Navigate with VoiceOver: sensible reading order, labeled controls, announced values, adjustable temperature, disabled states, and error recovery. Check Mac keyboard navigation and shortcuts.
- [ ] Enable Reduce Transparency and Increase Contrast; confirm readable controls and the standard prominent-button fallback. Check Reduce Motion with native navigation.
- [ ] Observe macOS 26+ Liquid Glass and the older-OS fallback, without glass obscuring content.
- [ ] Exercise an unavailable device, failed readback, and changed device selection; confirm the interface never reports an unverified change as successful.
- [ ] Test denied and granted Local Network/Home permissions, empty Home data, standard pairing, unreachable accessories, and real HomeKit readback.
- [ ] Enable/disable the real Mac bridge, verify service/device/paired states independently, and confirm pairing details are hidden when leaving the page.

Physical AC control and HomeKit pairing are separate release evidence; see [verification](verification.md) for recorded results.
