import Darwin
import Foundation

public enum LogFilesError: Error, Equatable {
    case cannotCreate(URL)
    case alreadyExists(URL)
}

/// One server log per launch; the newest five are kept, and none grows past `maxBytes`.
public struct LogFiles {
    public static let keep = 5
    /// The most one launch's log holds. Keeping five launches says nothing about how large one
    /// gets, and a server stuck printing filled the disk within a single launch; `LogWriter`
    /// rotates at this size instead, keeping the newest half.
    public static let maxBytes = 10_000_000
    /// The most `tail` reads, and so the most the error screen decodes and lays out.
    public static let tailBytes = 64 * 1024
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
            // Only a rotation the app did not live to finish leaves one of these behind.
            let rotation = LogWriter.rotationFile(for: old)
            if fileManager.fileExists(atPath: rotation.path) {
                try fileManager.removeItem(at: rotation)
            }
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
    ///
    /// Only the last `maxBytes` of the file are read. The error screen waits on this on the main
    /// thread, and reading the whole file to keep 40 lines of it made both the wait and the memory
    /// grow with the log: a runaway server's log could beachball or kill the one screen that exists
    /// to explain it. When the lines asked for are longer than that, fewer are returned.
    public static func tail(of url: URL, lines: Int = 40, maxBytes: Int = tailBytes) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let size = try handle.seekToEnd()
        return try tail(size: size, lines: lines, maxBytes: maxBytes) { offset, count in
            try handle.seek(toOffset: offset)
            return try handle.read(upToCount: count) ?? Data()
        }
    }

    /// `tail(of:)` given the file's size and a way to read part of it, so a test can count the reads.
    static func tail(size: UInt64, lines: Int, maxBytes: Int,
                     read: (_ offset: UInt64, _ count: Int) throws -> Data) throws -> String {
        let count = Int(min(size, UInt64(maxBytes)))
        let start = size - UInt64(count)
        var window = try read(start, count)
        var cut = false
        if start > 0 {
            // The window's first byte is only there to say whether the byte after it begins a line.
            let startsALine = window.first == 0x0A
            window = window.dropFirst()
            let body = window.last == 0x0A ? window.dropLast() : window
            if !startsALine, let newline = body.firstIndex(of: 0x0A) {
                window = window[(newline + 1)...]
            } else if !startsALine {
                // One line longer than the cap. There is no whole line to start on, so the cut is
                // shown as one, and starts on a character rather than inside it.
                cut = true
                while let first = window.first, first & 0xC0 == 0x80 { window = window.dropFirst() }
            }
        }
        var all = String(decoding: window, as: UTF8.self).components(separatedBy: "\n")
        if all.last == "" { all.removeLast() }
        let text = all.suffix(lines).joined(separator: "\n")
        return cut ? "…" + text : text
    }
}

/// Writes a server's output to its launch log without letting the file grow past `maxBytes`.
///
/// When a write would take it past, the log is replaced by its newest `maxBytes / 2`, starting on a
/// whole line, under a line saying that earlier output was dropped. Half rather than none: a log
/// emptied at the cap could hold only a few lines when the server then crashes, and the traceback
/// that explains the crash is the output this exists to keep. The replacement is written beside
/// the log and renamed over it, so a crash part-way through leaves the log as it was.
///
/// The server's reader thread is the only one that writes, so nothing here is synchronised.
final class LogWriter {
    private let url: URL
    private let maxBytes: Int
    private var handle: FileHandle
    private var size: UInt64

    /// `handle` is open for writing on `url`; it is taken rather than opened here so a test can
    /// hand in one whose writes fail.
    init(url: URL, handle: FileHandle, maxBytes: Int) throws {
        precondition(maxBytes >= 1024, "a cap this small has no room for the note a rotation writes")
        self.url = url
        self.handle = handle
        self.maxBytes = maxBytes
        self.size = try handle.seekToEnd()
    }

    /// Where a rotation writes the log's replacement before renaming it over the log.
    static func rotationFile(for log: URL) -> URL {
        log.appendingPathExtension("rotating")
    }

    func append(_ chunk: Data) throws {
        guard size + UInt64(chunk.count) > UInt64(maxBytes) else {
            try handle.write(contentsOf: chunk)
            size += UInt64(chunk.count)
            return
        }
        try rotate(adding: chunk)
    }

    func close() {
        do {
            try handle.close()
        } catch {
            NSLog("CircuitMCP could not close the server log %@: %@", url.path, "\(error)")
        }
    }

    /// Replaces the log with the newest half of the log and `chunk` taken together.
    private func rotate(adding chunk: Data) throws {
        let keep = UInt64(maxBytes / 2)
        let total = size + UInt64(chunk.count)
        // One byte before the kept output as well, to tell whether the kept output begins a line.
        let from = total - keep - 1
        var newest: Data
        if from < size {
            let reader = try FileHandle(forReadingFrom: url)
            defer { try? reader.close() }
            try reader.seek(toOffset: from)
            newest = try reader.read(upToCount: Int(size - from)) ?? Data()
            newest.append(chunk)
        } else {
            newest = chunk.suffix(Int(total - from))
        }
        let startsALine = newest.first == 0x0A
        var kept = newest.dropFirst()
        if !startsALine, let newline = kept.firstIndex(of: 0x0A) {
            kept = kept[(newline + 1)...]
        }

        let cap = ByteCountFormatter.string(fromByteCount: Int64(maxBytes), countStyle: .file)
        var replacement = Data("CircuitMCP: earlier output from this server was dropped to keep its log under \(cap).\n".utf8)
        replacement.append(kept)
        let rotation = Self.rotationFile(for: url)
        try replacement.write(to: rotation)
        guard rename(rotation.path, url.path) == 0 else {
            let failure = errno
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(failure),
                          userInfo: [NSFilePathErrorKey: url.path,
                                     NSLocalizedDescriptionKey: "\(rotation.path) could not replace the log: \(String(cString: strerror(failure)))"])
        }
        let replaced = handle
        handle = try FileHandle(forWritingTo: url)
        size = try handle.seekToEnd()
        do {
            try replaced.close()
        } catch {
            NSLog("CircuitMCP could not close the rotated server log %@: %@", url.path, "\(error)")
        }
    }
}
