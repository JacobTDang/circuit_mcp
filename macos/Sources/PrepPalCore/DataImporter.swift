import Darwin
import Foundation
import os

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

/// What an import left on disk, so a failure can be explained in terms the student can act on.
public struct ImportRecovery: Equatable {
    /// False when no data folder is in place, which is what a rollback that did not finish leaves.
    public let dataFolderPresent: Bool
    /// Folders an import moved aside and did not put back, oldest first.
    public let backupsLeftBehind: [URL]

    public init(dataFolderPresent: Bool, backupsLeftBehind: [URL]) {
        self.dataFolderPresent = dataFolderPresent
        self.backupsLeftBehind = backupsLeftBehind
    }
}

/// One-time copy of an existing command-center folder into the app's data folder.
/// Nothing is ever deleted: existing app data is moved aside first.
public struct DataImporter {
    private static let log = Logger(subsystem: "io.github.jacobtdang.preppal", category: "DataImporter")

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
        // Either folder nested in the other is the same mistake as importing a folder into itself:
        // moving the destination aside carries the source with it, and the copy is left reading a
        // path that no longer exists.
        guard sourcePath != destinationPath,
              !destinationPath.hasPrefix(sourcePath + "/"),
              !sourcePath.hasPrefix(destinationPath + "/") else {
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
        do {
            try fileManager.copyItem(at: source, to: destination)
        } catch {
            undo(partialCopyAt: destination, restoring: backup)
            throw error
        }
        return ImportResult(backup: backup)
    }

    /// Where the data stands after an import. `undo` reports a rollback it could not finish only
    /// to the unified log, and the error the caller is handed is the copy's, so a caller that
    /// wants to tell a clean rollback from one that left the data in a timestamped folder has to
    /// look at the disk. Reading the folder is allowed to fail, and the reason goes to the caller
    /// rather than being reported as nothing left behind.
    public func recovery(at destination: URL) throws -> ImportRecovery {
        let parent = destination.deletingLastPathComponent()
        let prefix = "\(destination.lastPathComponent).before-import-"
        let names = try fileManager.contentsOfDirectory(atPath: parent.path)
        return ImportRecovery(
            dataFolderPresent: fileManager.fileExists(atPath: destination.path),
            backupsLeftBehind: names.filter { $0.hasPrefix(prefix) }.sorted()
                .map { parent.appendingPathComponent($0, isDirectory: true) }
        )
    }

    /// Puts a failed import back the way it found it. Without this the app looks for data that is
    /// no longer there, creates an empty database, and the user's only copy sits in a folder named
    /// after a timestamp that no error mentions.
    ///
    /// Removing the destination deletes nothing of the user's: it holds only what this call just
    /// wrote. The copy's error carries the reason the import failed and is what the caller must
    /// see, so a rollback that fails cannot take its place -- it goes to the log instead, naming
    /// the folder the data was left in.
    private func undo(partialCopyAt destination: URL, restoring backup: URL?) {
        do {
            if fileManager.fileExists(atPath: destination.path) {
                try fileManager.removeItem(at: destination)
            }
            if let backup {
                try fileManager.moveItem(at: backup, to: destination)
            }
        } catch {
            Self.log.error("""
                Rolling back a failed command_center import did not finish: \
                \(error.localizedDescription, privacy: .public). \
                The data moved aside is at \(backup?.path ?? "<none: the destination was empty>", privacy: .public).
                """)
        }
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
