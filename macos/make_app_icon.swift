import CoreGraphics
import Foundation
import ImageIO

/// Draws the app icon: a resistor between two nodes, on a rounded tile.
/// CoreGraphics only, so the mark does not depend on an icon library or on
/// AppKit having a window server connection while the script runs.
let canvas = 1024
guard CommandLine.arguments.count == 2 else {
    fputs("make_app_icon: usage: make_app_icon <output.png>\n", stderr)
    exit(1)
}

let colorSpace = CGColorSpaceCreateDeviceRGB()
guard let context = CGContext(
    data: nil, width: canvas, height: canvas, bitsPerComponent: 8, bytesPerRow: 0,
    space: colorSpace, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
) else {
    fputs("make_app_icon: could not create a bitmap\n", stderr)
    exit(1)
}

let tile = CGRect(x: 48, y: 48, width: 928, height: 928)
context.setFillColor(CGColor(srgbRed: 0.11, green: 0.24, blue: 0.34, alpha: 1))
context.addPath(CGPath(roundedRect: tile, cornerWidth: 200, cornerHeight: 200, transform: nil))
context.fillPath()

let white = CGColor(gray: 1, alpha: 1)
context.setStrokeColor(white)
context.setFillColor(white)
context.setLineWidth(36)
context.setLineCap(.round)
context.setLineJoin(.round)

let y = 512.0
let x0 = 230.0
let x1 = 370.0
let x2 = 654.0
let x3 = 794.0
let lead = CGMutablePath()
lead.move(to: CGPoint(x: x0, y: y))
lead.addLine(to: CGPoint(x: x1, y: y))
let peaks = [96.0, -96.0, 96.0, -96.0, 96.0, -96.0]
let step = (x2 - x1) / Double(peaks.count)
var x = x1
for peak in peaks {
    let mid = x + step / 2
    x += step
    lead.addLine(to: CGPoint(x: mid, y: y + peak))
    lead.addLine(to: CGPoint(x: x, y: y))
}
lead.addLine(to: CGPoint(x: x3, y: y))
context.addPath(lead)
context.strokePath()

for center in [x0, x3] {
    let node = CGRect(x: center - 28, y: y - 28, width: 56, height: 56)
    context.fillEllipse(in: node)
}

guard let image = context.makeImage() else {
    fputs("make_app_icon: could not read the bitmap back\n", stderr)
    exit(1)
}
let destinationURL = URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL
guard let destination = CGImageDestinationCreateWithURL(destinationURL, "public.png" as CFString, 1, nil) else {
    fputs("make_app_icon: could not create \(CommandLine.arguments[1])\n", stderr)
    exit(1)
}
CGImageDestinationAddImage(destination, image, nil)
guard CGImageDestinationFinalize(destination) else {
    fputs("make_app_icon: could not write \(CommandLine.arguments[1])\n", stderr)
    exit(1)
}
