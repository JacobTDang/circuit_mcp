import Foundation

public enum LogFilesError: Error, Equatable {
    case cannotCreate(URL)
    case alreadyExists(URL)
}

/// One server log per launch; the newest five are kept.
public struct LogFiles {
    public static let keep = 5
    public let directory: URL

    public init(directory: URL) {
        self.directory = directory
    }

    /// Creates `server-<UTC timestamp with milliseconds>.log`, then deletes all but the newest `keep`.
    public func startNewLog(now: Date = Date(), fileManager: FileManager = .default) throws -> URL {
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyyMMdd'T'HHmmss.SSS'Z'"
        let url = directory.appendingPathComponent("server-\(formatter.string(from: now)).log")
        guard !fileManager.fileExists(atPath: url.path) else { throw LogFilesError.alreadyExists(url) }
        guard fileManager.createFile(atPath: url.path, contents: nil) else { throw LogFilesError.cannotCreate(url) }
        for old in try existingLogs(fileManager: fileManager).dropLast(Self.keep) {
            try fileManager.removeItem(at: old)
        }
        return url
    }

    /// Server logs in this directory, oldest first. Timestamped names sort chronologically.
    public func existingLogs(fileManager: FileManager = .default) throws -> [URL] {
        try fileManager.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.lastPathComponent.hasPrefix("server-") && $0.pathExtension == "log" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    /// The last `lines` lines of a log, for the error screen. Undecodable bytes are replaced, not dropped.
    public static func tail(of url: URL, lines: Int = 40) throws -> String {
        let text = String(decoding: try Data(contentsOf: url), as: UTF8.self)
        var all = text.components(separatedBy: "\n")
        if all.last == "" { all.removeLast() }
        return all.suffix(lines).joined(separator: "\n")
    }
}
