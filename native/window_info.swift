import AppKit
import CoreGraphics
import Foundation

struct Window: Encodable { let id: Int; let owner: String; let name: String; let area: Int }
let filter = CommandLine.arguments.dropFirst().first?.lowercased() ?? "uxplay"
let raw = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
var matches: [Window] = []
for entry in raw {
    let owner = entry[kCGWindowOwnerName as String] as? String ?? ""
    guard owner.lowercased().contains(filter) else { continue }
    guard let id = entry[kCGWindowNumber as String] as? Int,
          let bounds = entry[kCGWindowBounds as String] as? [String: Any] else { continue }
    let width = Int(bounds["Width"] as? Double ?? 0)
    let height = Int(bounds["Height"] as? Double ?? 0)
    guard width > 100 && height > 100 else { continue }
    matches.append(Window(id: id, owner: owner,
                          name: entry[kCGWindowName as String] as? String ?? "",
                          area: width * height))
}
matches.sort { $0.area > $1.area }
let data = try JSONEncoder().encode(matches)
print(String(data: data, encoding: .utf8)!)
