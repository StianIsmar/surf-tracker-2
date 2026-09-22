import Cocoa

let outputURL = URL(fileURLWithPath: CommandLine.arguments[1])
let size = NSSize(width: 1024, height: 1024)
let image = NSImage(size: size)

image.lockFocus()
NSGraphicsContext.current?.imageInterpolation = .high

let tile = NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896), xRadius: 210, yRadius: 210)
NSColor(calibratedRed: 0.071, green: 0.235, blue: 0.227, alpha: 1).setFill()
tile.fill()

NSColor(calibratedRed: 0.906, green: 0.757, blue: 0.357, alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: 626, y: 612, width: 216, height: 216)).fill()

let wave = NSBezierPath()
wave.move(to: NSPoint(x: 126, y: 343))
wave.curve(to: NSPoint(x: 475, y: 641), controlPoint1: NSPoint(x: 260, y: 353), controlPoint2: NSPoint(x: 331, y: 622))
wave.curve(to: NSPoint(x: 786, y: 526), controlPoint1: NSPoint(x: 594, y: 657), controlPoint2: NSPoint(x: 714, y: 606))
wave.curve(to: NSPoint(x: 609, y: 539), controlPoint1: NSPoint(x: 725, y: 551), controlPoint2: NSPoint(x: 665, y: 541))
wave.curve(to: NSPoint(x: 439, y: 443), controlPoint1: NSPoint(x: 533, y: 537), controlPoint2: NSPoint(x: 505, y: 468))
wave.curve(to: NSPoint(x: 126, y: 343), controlPoint1: NSPoint(x: 329, y: 399), controlPoint2: NSPoint(x: 227, y: 370))
wave.close()
NSColor(calibratedWhite: 0.965, alpha: 1).setFill()
wave.fill()

let curl = NSBezierPath()
curl.move(to: NSPoint(x: 385, y: 450))
curl.curve(to: NSPoint(x: 655, y: 710), controlPoint1: NSPoint(x: 423, y: 660), controlPoint2: NSPoint(x: 587, y: 735))
curl.curve(to: NSPoint(x: 510, y: 606), controlPoint1: NSPoint(x: 601, y: 696), controlPoint2: NSPoint(x: 537, y: 665))
curl.curve(to: NSPoint(x: 385, y: 450), controlPoint1: NSPoint(x: 487, y: 552), controlPoint2: NSPoint(x: 437, y: 493))
curl.close()
NSColor(calibratedRed: 0.027, green: 0.535, blue: 0.510, alpha: 1).setFill()
curl.fill()

let board = NSBezierPath()
board.move(to: NSPoint(x: 330, y: 330))
board.curve(to: NSPoint(x: 664, y: 301), controlPoint1: NSPoint(x: 437, y: 292), controlPoint2: NSPoint(x: 563, y: 281))
board.curve(to: NSPoint(x: 330, y: 330), controlPoint1: NSPoint(x: 550, y: 346), controlPoint2: NSPoint(x: 432, y: 354))
board.close()
NSColor(calibratedRed: 0.812, green: 0.314, blue: 0.247, alpha: 1).setFill()
board.fill()

image.unlockFocus()

guard let data = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: data),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("Could not render app icon")
}
try png.write(to: outputURL)
