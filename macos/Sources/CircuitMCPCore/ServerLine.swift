import Foundation

/// One protocol line printed by `python -m circuit_mcp.app_server`.
public enum ServerLine: Equatable {
    case ready(port: Int)
    case locked(pid: String)
    /// Starts like a protocol line but cannot be read. Reported, never ignored.
    case malformed(line: String)

    /// Returns nil for ordinary log output.
    public static func parse(_ line: String) -> ServerLine? {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        let parts = trimmed.split(separator: " ", omittingEmptySubsequences: true)
        guard let keyword = parts.first, keyword == "READY" || keyword == "LOCKED" else { return nil }
        guard parts.count == 2 else { return .malformed(line: trimmed) }
        if keyword == "LOCKED" {
            return .locked(pid: String(parts[1]))
        }
        guard let port = Int(parts[1]), (1...65535).contains(port) else { return .malformed(line: trimmed) }
        return .ready(port: port)
    }
}
