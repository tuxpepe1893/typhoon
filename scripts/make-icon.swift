import AppKit

let output = CommandLine.arguments[1]
try FileManager.default.createDirectory(atPath: output, withIntermediateDirectories: true)
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
let tile = NSBezierPath(roundedRect: NSRect(x: 46, y: 46, width: 932, height: 932), xRadius: 212, yRadius: 212)
NSGradient(starting: NSColor(red: 0.055, green: 0.14, blue: 0.24, alpha: 1), ending: NSColor(red: 0.06, green: 0.40, blue: 0.55, alpha: 1))!.draw(in: tile, angle: 40)
let rings: [(CGFloat, CGFloat, CGFloat)] = [(640, 568, 192), (482, 470, 150), (336, 405, 110)]
for (index, ring) in rings.enumerated() {
    let (y, center, radius) = ring
    let path = NSBezierPath()
    path.lineWidth = 42
    path.lineCapStyle = .round
    path.move(to: NSPoint(x: 262 + CGFloat(index) * 43, y: y))
    path.line(to: NSPoint(x: center, y: y))
    path.curve(to: NSPoint(x: center + radius, y: y + 74), controlPoint1: NSPoint(x: center + radius, y: y), controlPoint2: NSPoint(x: center + radius + 18, y: y + 8))
    path.curve(to: NSPoint(x: center + radius - 96, y: y + 75), controlPoint1: NSPoint(x: center + radius, y: y + 140), controlPoint2: NSPoint(x: center + radius - 94, y: y + 140))
    NSColor(red: 0.76 + CGFloat(index) * 0.065, green: 0.94, blue: 1, alpha: 1).setStroke()
    path.stroke()
}
image.unlockFocus()
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        image.draw(in: NSRect(x: 0, y: 0, width: pixels, height: pixels))
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output + "/icon_\(size)x\(size)\(suffix).png"))
    }
}
