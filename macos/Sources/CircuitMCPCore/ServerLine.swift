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

/// The server's output, as the lines `ServerLine` reads, from the chunks the reader is handed.
///
/// A line is held until its newline arrives, and a server printing without newlines used to make
/// the app hold all of it -- megabytes, without limit -- and search all of it again on every read,
/// long enough for a READY printed after it to miss the ready deadline. Past `maxLineBytes` the line
/// comes out cut, marked with "…", and the rest of it is skipped up to its newline. Protocol lines
/// are a few bytes, and the log is written from the chunks before they reach this, so the cut costs
/// neither the protocol nor the log anything.
struct OutputLines {
    let maxLineBytes: Int
    private var pending = Data()
    /// True between a cut and the newline that ends the line it cut.
    private var skippingCutLine = false

    init(maxLineBytes: Int) {
        self.maxLineBytes = maxLineBytes
    }

    /// How much of a line that has not ended is held.
    var heldBytes: Int { pending.count }

    /// The lines `chunk` completes, and the line it takes past `maxLineBytes`, if any, cut.
    mutating func append(_ chunk: Data) -> [String] {
        var lines: [String] = []
        var rest = chunk[...]
        while let newline = rest.firstIndex(of: 0x0A) {
            hold(rest[..<newline], cutting: &lines)
            if skippingCutLine {
                skippingCutLine = false
            } else {
                lines.append(String(decoding: pending, as: UTF8.self))
            }
            pending.removeAll(keepingCapacity: true)
            rest = rest[(newline + 1)...]
        }
        hold(rest, cutting: &lines)
        return lines
    }

    private mutating func hold(_ bytes: Data, cutting lines: inout [String]) {
        guard !skippingCutLine else { return }
        let room = maxLineBytes - pending.count
        guard bytes.count > room else {
            pending.append(bytes)
            return
        }
        pending.append(bytes.prefix(room))
        // The mark is what keeps a cut line from parsing as the protocol line it began as: cut at
        // the spaces after "READY 45678", it would otherwise trim down to a READY the server never
        // finished sending.
        lines.append(String(decoding: pending, as: UTF8.self) + "…")
        pending.removeAll(keepingCapacity: true)
        skippingCutLine = true
    }
}
