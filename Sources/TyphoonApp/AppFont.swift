import SwiftUI

/// David Mono (https://github.com/tuxpepe1893/davidmono) is Typhoon's default
/// typeface. The build copies its TTFs into Contents/Resources/Fonts, and
/// Info.plist's ATSApplicationFontsPath registers them at launch. When the
/// fonts are missing (for example under `swift run`), SwiftUI falls back to the
/// system font.
enum AppFont {
    static let family = "David Mono"

    /// macOS default point sizes for each text style.
    static func size(for style: Font.TextStyle) -> CGFloat {
        switch style {
        case .largeTitle: return 26
        case .title: return 22
        case .title2: return 17
        case .title3: return 15
        case .headline, .body: return 13
        case .callout: return 12
        case .subheadline: return 11
        case .footnote, .caption, .caption2: return 10
        @unknown default: return 13
        }
    }
}

extension Font {
    /// David Mono at a text style's size, scaling with the system text size.
    static func app(_ style: Font.TextStyle) -> Font {
        let font = Font.custom(AppFont.family, size: AppFont.size(for: style), relativeTo: style)
        return style == .headline ? font.weight(.semibold) : font
    }

    /// David Mono at a fixed point size, still scaling relative to body text.
    static func app(size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .custom(AppFont.family, size: size, relativeTo: .body).weight(weight)
    }
}
