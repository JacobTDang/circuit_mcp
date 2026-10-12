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
            return "CircuitMCP moves your current data aside before it imports, and \(path) is already there. Rename or move that folder, then try the import again."
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
    /// Where the data the import replaced is now, or nil when there was none.
    public let backup: URL?
    /// The earlier imports' backups this import deleted.
    public let deletedBackups: [URL]
    /// What the success alert says: where the replaced data is, how long it is kept, and what was
    /// deleted or could not be. The alert used to name the backup and stop there.
    public let summary: String
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
///
/// The data an import replaces is kept as `command_center.backup-<time>` until the next successful
/// import, which deletes it: one backup at most, holding what the latest import replaced. Keeping
/// every one had repeated imports grow Application Support by a whole notebook each. A folder named
/// `command_center.before-import-<time>` is data an import moved aside and could not put back, and
/// may be the student's only copy; nothing deletes one of those.
///
/// The folder is copied beside the data folder first, and the data folder is only moved aside once
/// that copy is complete, so putting the copy in place is two renames. The copy is the long part
/// -- it is the student's whole notebook, possibly on a slow or failing volume -- and keeping the
/// data folder out of it is what lets a copy be cancelled, or abandoned by a quit that will not
/// wait for it, without anything to put back.
///
/// The import copy runs off the main thread. This is a value type whose stored properties are a
/// `FileManager` and the copy; the calls made on the file manager here -- `fileExists`, `moveItem`,
/// `removeItem`, `createDirectory`, `contentsOfDirectory` -- are the ones Apple documents as safe
/// to use from multiple threads, and the copy runs only on the thread that called the import.
extension DataImporter: @unchecked Sendable {}

public struct DataImporter {
    private static let log = Logger(subsystem: "io.github.jacobtdang.circuitmcp", category: "DataImporter")

    /// Copies a folder to a path that does not exist yet, counting its bytes into `progress` and
    /// stopping once `progress` is cancelled. Replaceable so a test can stand in for a volume that
    /// has stopped answering.
    public typealias Copy = (_ source: URL, _ destination: URL, _ progress: Progress) throws -> Void

    private let fileManager: FileManager
    private let copy: Copy

    public init(fileManager: FileManager = .default, copy: @escaping Copy = DataImporter.copyFolder) {
        self.fileManager = fileManager
        self.copy = copy
    }

    /// Where the copy is made before it replaces the data folder. There is only ever one: imports
    /// do not overlap, and a copy left here is one the app did not live to finish.
    static func stagingFolder(for destination: URL) -> URL {
        destination.deletingLastPathComponent()
            .appendingPathComponent("\(destination.lastPathComponent).importing", isDirectory: true)
    }

    /// The line the import screen shows under its progress bar.
    public static func progressDescription(_ progress: Progress) -> String {
        if progress.isCancelled { return "Stopping the copy. Your data is left as it was." }
        if progress.isIndeterminate { return "Counting the files to copy…" }
        let copied = ByteCountFormatter.string(fromByteCount: progress.completedUnitCount, countStyle: .file)
        let total = ByteCountFormatter.string(fromByteCount: progress.totalUnitCount, countStyle: .file)
        return "\(copied) of \(total) copied"
    }

    /// `progress` reports the copy, and cancelling it stops the import at the end of the file being
    /// copied; the import then throws `CancellationError` with the data folder as it was.
    public func importCommandCenter(from source: URL, to destination: URL, now: Date = Date(),
                                    progress: Progress = .discreteProgress(totalUnitCount: -1)) throws -> ImportResult {
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

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
        let stamp = formatter.string(from: now)
        let aside = destination.deletingLastPathComponent()
            .appendingPathComponent("\(destination.lastPathComponent).before-import-\(stamp)", isDirectory: true)
        let kept = destination.deletingLastPathComponent()
            .appendingPathComponent("\(Self.backupPrefix(for: destination))\(stamp)", isDirectory: true)
        let replacesData = fileManager.fileExists(atPath: destination.path)
        if replacesData, let taken = [aside, kept].first(where: { fileManager.fileExists(atPath: $0.path) }) {
            throw DataImportError.backupExists(taken.path)
        }

        let staging = Self.stagingFolder(for: destination)
        // Only an import the app did not live to finish leaves one, and it holds nothing but part
        // of a copy of a folder that is still where it was.
        if fileManager.fileExists(atPath: staging.path) {
            try fileManager.removeItem(at: staging)
        }
        try fileManager.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        do {
            try copy(source, staging, progress)
            // The last point a cancel is honoured: what follows is two renames, which are not left
            // half-done on the student's data.
            if progress.isCancelled { throw CancellationError() }
        } catch {
            discard(staging)
            throw error
        }

        var backup: URL?
        if replacesData {
            do {
                try fileManager.moveItem(at: destination, to: aside)
            } catch {
                discard(staging)
                throw error
            }
            backup = aside
        }
        do {
            try fileManager.moveItem(at: staging, to: destination)
        } catch {
            if let backup { restore(backup, to: destination) }
            discard(staging)
            throw error
        }
        guard let backup else {
            // Nothing was replaced, so no backup was made, and the one an earlier import left is
            // still the latest: it stays.
            return ImportResult(backup: nil, deletedBackups: [], summary: "There was no previous app data.")
        }
        return keepOnly(backup, as: kept, beside: destination)
    }

    /// `command_center.backup-`: the only folders the retention policy ever deletes.
    private static func backupPrefix(for destination: URL) -> String {
        "\(destination.lastPathComponent).backup-"
    }

    /// The backups of earlier imports, which the next import that replaces data deletes. The
    /// confirmation names them, while the student can still move one somewhere safe.
    public func backupsDeletedByNextImport(at destination: URL) throws -> [URL] {
        guard fileManager.fileExists(atPath: destination.path) else { return [] }
        return try backups(beside: destination)
    }

    private func backups(beside destination: URL) throws -> [URL] {
        let parent = destination.deletingLastPathComponent()
        let prefix = Self.backupPrefix(for: destination)
        return try fileManager.contentsOfDirectory(atPath: parent.path).filter { $0.hasPrefix(prefix) }.sorted()
            .map { parent.appendingPathComponent($0, isDirectory: true) }
    }

    /// The retention policy, run once this import's copy is in place: what it replaced becomes
    /// the one backup, and every earlier import's backup is deleted.
    ///
    /// The data is moved aside as `before-import-` and only renamed to `backup-` here, after the
    /// import has succeeded, so a folder a failed import could not put back never carries the name
    /// that is deleted. Which backup survives is decided by which import made it, not by the time
    /// in its name: a clock set back would otherwise delete the newest. The import has already
    /// succeeded, so nothing here can fail it; what cleanup could not do is said in the summary.
    private func keepOnly(_ aside: URL, as kept: URL, beside destination: URL) -> ImportResult {
        var sentences: [String] = []
        let backup: URL
        do {
            try fileManager.moveItem(at: aside, to: kept)
            backup = kept
            sentences.append("Your previous data is in \(kept.path). It is kept until your next import, which deletes it.")
        } catch {
            backup = aside
            sentences.append("""
                Your previous data is in \(aside.path). It could not be renamed as this import's \
                backup (\(error.localizedDescription)), so later imports will not delete it: delete \
                it yourself once you no longer need it.
                """)
        }

        var deleted: [URL] = []
        do {
            for earlier in try backups(beside: destination) where earlier.lastPathComponent != backup.lastPathComponent {
                do {
                    try fileManager.removeItem(at: earlier)
                    deleted.append(earlier)
                } catch {
                    sentences.append("\(earlier.path) could not be deleted (\(error.localizedDescription)). Delete it in Finder to free its space.")
                }
            }
        } catch {
            sentences.append("""
                The backups of earlier imports could not be listed to delete them \
                (\(error.localizedDescription)). They are in \(destination.deletingLastPathComponent().path).
                """)
        }
        if !deleted.isEmpty {
            let paths = deleted.map { "  \($0.path)" }.joined(separator: "\n")
            sentences.insert("The backup from your previous import was deleted to free its space:\n\(paths)", at: 1)
        }
        return ImportResult(backup: backup, deletedBackups: deleted, summary: sentences.joined(separator: "\n\n"))
    }

    /// `FileManager`'s own recursive copy, which keeps what a copy of the folder should --
    /// permissions, extended attributes, links -- and names the file it tripped on when it fails.
    /// Its delegate is asked before each item is copied, which is where the bytes are counted and
    /// where a cancelled copy stops: every item after that is skipped, so the copy returns at the
    /// end of the file it was on.
    public static func copyFolder(_ source: URL, to destination: URL, progress: Progress) throws {
        progress.totalUnitCount = try bytesToCopy(in: source, progress: progress)
        let counter = CopyCounter(progress: progress)
        let copier = FileManager()
        copier.delegate = counter
        try withExtendedLifetime(counter) {
            try copier.copyItem(at: source, to: destination)
        }
        counter.finish()
    }

    /// Counted before the copy starts so the bar has an end. Counting is reading the same volume
    /// the copy will, so it stops on a cancel too, and a folder it cannot read fails the import
    /// here with the error that names it.
    private static func bytesToCopy(in folder: URL, progress: Progress) throws -> Int64 {
        var unreadable: Error?
        let keys: [URLResourceKey] = [.isRegularFileKey, .fileSizeKey]
        guard let items = FileManager.default.enumerator(at: folder, includingPropertiesForKeys: keys,
                                                         errorHandler: { _, error in unreadable = error; return false }) else {
            throw CocoaError(.fileReadUnknown, userInfo: [NSFilePathErrorKey: folder.path])
        }
        var total: Int64 = 0
        for case let item as URL in items {
            if progress.isCancelled { throw CancellationError() }
            let values = try item.resourceValues(forKeys: Set(keys))
            if values.isRegularFile == true, let size = values.fileSize {
                total += Int64(size)
            }
        }
        if let unreadable { throw unreadable }
        return total
    }

    /// The copy's delegate: counts each file's bytes as the next item starts, and skips everything
    /// once the import is cancelled.
    private final class CopyCounter: NSObject, FileManagerDelegate {
        private let progress: Progress
        /// The size of the file being copied now, added once the copy moves past it.
        private var current: Int64 = 0

        init(progress: Progress) {
            self.progress = progress
        }

        func fileManager(_ fileManager: FileManager, shouldCopyItemAt srcURL: URL, to dstURL: URL) -> Bool {
            progress.completedUnitCount += current
            // An item that cannot be looked at here fails its own copy a moment later, with an
            // error naming it; that error is the one the student is shown, and the bar only has to
            // count it as nothing until then.
            let values = try? srcURL.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
            current = values?.isRegularFile == true ? Int64(values?.fileSize ?? 0) : 0
            return !progress.isCancelled
        }

        func finish() {
            progress.completedUnitCount += current
            current = 0
        }
    }

    /// Where the data stands after an import. `restore` reports a rollback it could not finish only
    /// to the unified log, and the error the caller is handed is the one that stopped the import,
    /// so a caller that wants to tell a clean rollback from one that left the data in a timestamped
    /// folder has to look at the disk. Reading the folder is allowed to fail, and the reason goes to
    /// the caller rather than being reported as nothing left behind.
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

    /// Puts the data folder back when the copy could not be put in its place. Without this the app
    /// looks for data that is no longer there, creates an empty database, and the user's only copy
    /// sits in a folder named after a timestamp that no error mentions.
    ///
    /// The rename's error carries the reason the import failed and is what the caller must see, so
    /// a rollback that fails cannot take its place -- it goes to the log instead, naming the folder
    /// the data was left in.
    private func restore(_ backup: URL, to destination: URL) {
        do {
            try fileManager.moveItem(at: backup, to: destination)
        } catch {
            Self.log.error("""
                Rolling back a failed command_center import did not finish: \
                \(error.localizedDescription, privacy: .public). \
                The data moved aside is at \(backup.path, privacy: .public).
                """)
        }
    }

    /// Removes a copy that will not be used. It holds nothing of the student's -- the folder it was
    /// copied from is untouched -- so failing to remove it is not the failure to report: the reason
    /// the import stopped is. It goes to the log, and the next import removes the copy.
    private func discard(_ staging: URL) {
        guard fileManager.fileExists(atPath: staging.path) else { return }
        do {
            try fileManager.removeItem(at: staging)
        } catch {
            Self.log.error("""
                A partial command_center copy could not be removed: \
                \(error.localizedDescription, privacy: .public). \
                It is at \(staging.path, privacy: .public); the next import removes it.
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
