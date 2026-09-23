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

/// Every one of these is shown to the student in an alert, and the alert used to interpolate the
/// case itself: a forgotten `run_ui.py` -- the one failure the README all but schedules -- read
/// `sourceInUse("41273")`. The sentence a refusal is worth lives here rather than in the app so
/// that it is in the same target as the code that throws it, and can be tested.
extension DataImportError: CustomStringConvertible, LocalizedError {
    public var description: String {
        switch self {
        case .sourceMissing(let path):
            return "There is no folder at \(path). It may have been renamed or moved since you chose it. Choose the folder again."
        case .notACommandCenter(let path):
            return "\(path) has no circuit_mcp.sqlite3 in it, so it is not a command center folder. Choose the command_center folder itself, not the folder it sits in."
        case .sameFolder:
            return "That folder is the app's own data folder, or one of the two is inside the other. Choose a command center folder from outside the app's data folder, such as a checkout's .local/command_center."
        case .sourceInUse(let holder):
            return "Another server is using that folder (process \(holder)). Quit run_ui.py, or whatever else is using that folder, and try the import again."
        case .backupExists(let path):
            return "PrepPal moves your current data aside before it imports, and \(path) is already there. Rename or move that folder, then try the import again."
        }
    }

    /// `"\(error)"` is what the alert interpolates; `localizedDescription` is what the next caller
    /// will reach for. Without this conformance that one would read "DataImportError error 3".
    public var errorDescription: String? { description }

    /// True when the import gave up before it moved the destination aside or copied a byte, which
    /// is every case here: all five are thrown by the checks that run before anything on disk is
    /// touched. That is what lets the app close the alert with "Nothing was changed." instead of
    /// "Your data is back the way it was", which implies a move that never happened. The switch is
    /// exhaustive so that a case added after the first `moveItem` has to answer this question.
    public var refusedBeforeTouchingDisk: Bool {
        switch self {
        case .sourceMissing, .notACommandCenter, .sameFolder, .sourceInUse, .backupExists: return true
        }
    }
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
/// The import copy runs off the main thread. This is a value type whose only stored property is a
/// `FileManager`, and the calls made on it here -- `fileExists`, `moveItem`, `copyItem`,
/// `createDirectory` -- are the ones Apple documents as safe to use from multiple threads.
extension DataImporter: @unchecked Sendable {}

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
