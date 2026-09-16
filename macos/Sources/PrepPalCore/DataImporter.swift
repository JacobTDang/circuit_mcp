import Darwin
import Foundation

public enum DataImportError: Error, Equatable {
    case sourceMissing(String)
    case notACommandCenter(String)
    case sameFolder
    case sourceInUse(String)
    case backupExists(String)
}

public struct ImportResult: Equatable {
    public let backup: URL?
}

/// One-time copy of an existing command-center folder into the app's data folder.
/// Nothing is ever deleted: existing app data is moved aside first.
public struct DataImporter {
    private let fileManager: FileManager

    public init(fileManager: FileManager = .default) {
        self.fileManager = fileManager
    }

    public func importCommandCenter(from source: URL, to destination: URL, now: Date = Date()) throws -> ImportResult {
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: source.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw DataImportError.sourceMissing(source.path)
        }
        guard fileManager.fileExists(atPath: source.appendingPathComponent("circuit_mcp.sqlite3").path) else {
            throw DataImportError.notACommandCenter(source.path)
        }
        let sourcePath = source.standardizedFileURL.resolvingSymlinksInPath().path
        let destinationPath = destination.standardizedFileURL.resolvingSymlinksInPath().path
        guard sourcePath != destinationPath, !destinationPath.hasPrefix(sourcePath + "/") else {
            throw DataImportError.sameFolder
        }
        if let holder = lockHolder(of: source) {
            throw DataImportError.sourceInUse(holder)
        }

        var backup: URL?
        if fileManager.fileExists(atPath: destination.path) {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(identifier: "UTC")
            formatter.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
            let aside = destination.deletingLastPathComponent()
                .appendingPathComponent("\(destination.lastPathComponent).before-import-\(formatter.string(from: now))", isDirectory: true)
            guard !fileManager.fileExists(atPath: aside.path) else { throw DataImportError.backupExists(aside.path) }
            try fileManager.moveItem(at: destination, to: aside)
            backup = aside
        }
        try fileManager.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        try fileManager.copyItem(at: source, to: destination)
        return ImportResult(backup: backup)
    }

    /// The pid recorded in `server.lock` if another process holds that lock, else nil.
    private func lockHolder(of folder: URL) -> String? {
        let path = folder.appendingPathComponent("server.lock").path
        guard fileManager.fileExists(atPath: path) else { return nil }
        let fd = open(path, O_RDONLY)
        guard fd >= 0 else { return "unknown" }
        defer { close(fd) }
        if flock(fd, LOCK_EX | LOCK_NB) == 0 {
            flock(fd, LOCK_UN)
            return nil
        }
        let holder = (try? String(contentsOfFile: path, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
        return holder?.isEmpty == false ? holder : "unknown"
    }
}
