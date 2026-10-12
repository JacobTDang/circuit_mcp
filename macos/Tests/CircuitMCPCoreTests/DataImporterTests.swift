import XCTest
@testable import CircuitMCPCore

final class DataImporterTests: XCTestCase {
    private var root: URL!
    private let fileManager = FileManager.default

    override func setUpWithError() throws {
        root = fileManager.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? fileManager.removeItem(at: root)
    }

    private func commandCenter(_ name: String, marker: String) throws -> URL {
        let folder = root.appendingPathComponent(name, isDirectory: true)
        try fileManager.createDirectory(at: folder.appendingPathComponent("files"), withIntermediateDirectories: true)
        try Data(marker.utf8).write(to: folder.appendingPathComponent("circuit_mcp.sqlite3"))
        return folder
    }

    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testImportIntoAMissingDestinationCopiesWithoutABackup() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let destination = root.appendingPathComponent("app/command_center", isDirectory: true)
        let result = try DataImporter().importCommandCenter(from: source, to: destination, now: now)
        XCTAssertNil(result.backup)
        XCTAssertEqual(try String(contentsOf: destination.appendingPathComponent("circuit_mcp.sqlite3")), "repo-db")
        XCTAssertTrue(fileManager.fileExists(atPath: source.appendingPathComponent("circuit_mcp.sqlite3").path))
    }

    func testExistingAppDataIsMovedAsideNotDeleted() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let destination = try commandCenter("command_center", marker: "fresh-app-db")
        let result = try DataImporter().importCommandCenter(from: source, to: destination, now: now)
        let backup = try XCTUnwrap(result.backup)
        XCTAssertEqual(backup.lastPathComponent, "command_center.backup-20270115T080000Z")
        XCTAssertEqual(try String(contentsOf: backup.appendingPathComponent("circuit_mcp.sqlite3")), "fresh-app-db")
        XCTAssertEqual(try String(contentsOf: destination.appendingPathComponent("circuit_mcp.sqlite3")), "repo-db")
    }

    func testAFolderWithoutADatabaseIsRefused() throws {
        let empty = root.appendingPathComponent("not-a-store", isDirectory: true)
        try fileManager.createDirectory(at: empty, withIntermediateDirectories: true)
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: empty, to: root.appendingPathComponent("dest"))) {
            XCTAssertEqual($0 as? DataImportError, .notACommandCenter(empty.path))
        }
    }

    func testASourceInUseByAnotherServerIsRefused() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let lockPath = source.appendingPathComponent("server.lock").path
        try Data("777".utf8).write(to: URL(fileURLWithPath: lockPath))
        let held = open(lockPath, O_RDWR)
        XCTAssertGreaterThanOrEqual(held, 0)
        XCTAssertEqual(flock(held, LOCK_EX | LOCK_NB), 0)
        defer { close(held) }
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: source, to: root.appendingPathComponent("dest"))) {
            XCTAssertEqual($0 as? DataImportError, .sourceInUse("777"))
        }
    }

    func testImportingAFolderIntoItselfIsRefused() throws {
        let source = try commandCenter("command_center", marker: "db")
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: source, to: source)) {
            XCTAssertEqual($0 as? DataImportError, .sameFolder)
        }
    }

    func testAMissingSourceIsRefused() {
        let absent = root.appendingPathComponent("gone", isDirectory: true)
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: absent, to: root.appendingPathComponent("dest"))) {
            XCTAssertEqual($0 as? DataImportError, .sourceMissing(absent.path))
        }
    }

    /// A stale `server.lock` is what a folder left by a crashed server looks like. Nothing holds
    /// it, so the copy is safe and refusing would strand the user's data in the old folder.
    func testAnUnheldLockDoesNotBlockTheImport() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        try Data("777".utf8).write(to: source.appendingPathComponent("server.lock"))
        let destination = root.appendingPathComponent("app/command_center", isDirectory: true)
        XCTAssertNoThrow(try DataImporter().importCommandCenter(from: source, to: destination, now: now))
    }

    /// The backup name carries a whole-second timestamp, so a second import in the same second
    /// wants the name the first import's backup already has. Refusing is the only option that
    /// keeps the promise that nothing is deleted.
    func testASecondImportInTheSameSecondWillNotOverwriteTheFirstBackup() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let destination = try commandCenter("command_center", marker: "fresh-app-db")
        _ = try DataImporter().importCommandCenter(from: source, to: destination, now: now)
        let backup = root.appendingPathComponent("command_center.backup-20270115T080000Z", isDirectory: true)
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: source, to: destination, now: now)) {
            XCTAssertEqual($0 as? DataImportError, .backupExists(backup.path))
        }
        XCTAssertEqual(try String(contentsOf: backup.appendingPathComponent("circuit_mcp.sqlite3")), "fresh-app-db")
    }

    /// Importing a folder that lives inside the destination moves the destination -- and the
    /// source with it -- out from under the copy. It has to be refused up front, like the
    /// same-folder case it is a variant of.
    func testImportingAFolderFromInsideTheDestinationIsRefused() throws {
        let destination = try commandCenter("command_center", marker: "app-db")
        let source = try commandCenter("command_center/old", marker: "old-db")
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: source, to: destination, now: now)) {
            XCTAssertEqual($0 as? DataImportError, .sameFolder)
        }
        XCTAssertEqual(try String(contentsOf: destination.appendingPathComponent("circuit_mcp.sqlite3")), "app-db")
    }

    /// A copy that fails partway must leave the student looking at their own data, not at an
    /// empty app and a folder named after a timestamp that no error mentions. The partial copy
    /// holds only what this call just wrote, so it goes; the moved-aside data comes back; and the
    /// copy's own failure is what reaches the caller, because that is the reason to report.
    func testAFailedCopyPutsTheDataBackAndLeavesNoOrphanedBackup() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let unreadable = source.appendingPathComponent("files/uploaded.bin")
        try Data("an upload".utf8).write(to: unreadable)
        try fileManager.setAttributes([.posixPermissions: 0o000], ofItemAtPath: unreadable.path)
        defer { try? fileManager.setAttributes([.posixPermissions: 0o644], ofItemAtPath: unreadable.path) }
        let destination = try commandCenter("command_center", marker: "fresh-app-db")

        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: source, to: destination, now: now)) { error in
            XCTAssertNil(error as? DataImportError, "the copy's own failure is the reason the user needs")
            XCTAssertEqual((error as NSError).domain, NSCocoaErrorDomain)
            XCTAssertEqual((error as NSError).code, NSFileWriteNoPermissionError)
            XCTAssertEqual((error as NSError).userInfo[NSFilePathErrorKey] as? String, unreadable.path,
                           "the error names the file the copy tripped on, not a rollback step")
        }
        XCTAssertEqual(try String(contentsOf: destination.appendingPathComponent("circuit_mcp.sqlite3")), "fresh-app-db")
        XCTAssertTrue(fileManager.fileExists(atPath: destination.appendingPathComponent("files").path))
        let leftovers = try fileManager.contentsOfDirectory(atPath: root.path)
            .filter { $0.hasPrefix("command_center.") }
        XCTAssertEqual(leftovers, [], "the data is back at the destination, so no backup or partial copy may be left behind")
        XCTAssertEqual(try String(contentsOf: source.appendingPathComponent("circuit_mcp.sqlite3")), "repo-db")
    }

    // MARK: - A copy that can be watched and cancelled

    /// A command center with `uploads` files of `bytes` each under `files/`, as a notebook with
    /// uploaded worksheets has.
    private func notebook(_ name: String, marker: String, uploads: Int, bytes: Int) throws -> URL {
        let folder = try commandCenter(name, marker: marker)
        for number in 1...uploads {
            try Data(repeating: UInt8(number % 256), count: bytes)
                .write(to: folder.appendingPathComponent("files/upload-\(number).bin"))
        }
        return folder
    }

    private func appData(_ destination: URL) throws -> String {
        try String(contentsOf: destination.appendingPathComponent("circuit_mcp.sqlite3"))
    }

    /// The old import moved the student's data aside first and then ran one uncancellable copy into
    /// its place, so stopping part-way meant a rollback. The copy is now made beside the data
    /// folder, which is not touched until the copy is complete; putting it in place is a rename.
    func testTheDataFolderIsLeftInPlaceUntilTheCopyIsComplete() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let destination = try commandCenter("command_center", marker: "app-db")
        var copiedInto: URL?
        var dataDuringTheCopy: String?
        let importer = DataImporter(copy: { from, to, progress in
            copiedInto = to
            dataDuringTheCopy = try self.appData(destination)
            try DataImporter.copyFolder(from, to: to, progress: progress)
        })
        _ = try importer.importCommandCenter(from: source, to: destination, now: now)

        XCTAssertEqual(dataDuringTheCopy, "app-db", "the data folder was moved before the copy was complete")
        let staging = try XCTUnwrap(copiedInto)
        XCTAssertEqual(staging.deletingLastPathComponent().standardizedFileURL, root.standardizedFileURL,
                       "the copy is made beside the data folder, so putting it in place is a rename")
        XCTAssertNotEqual(staging.standardizedFileURL, destination.standardizedFileURL)
        XCTAssertFalse(fileManager.fileExists(atPath: staging.path), "the finished copy is the data folder now")
        XCTAssertEqual(try appData(destination), "repo-db")
    }

    /// The acceptance case: cancelling while the copy is under way removes what it had copied, and
    /// the student's data is exactly as it was -- not moved, so nothing has to be put back.
    func testCancellingDuringTheCopyRemovesThePartialCopyAndLeavesTheDataAsItWas() throws {
        let source = try notebook("repo", marker: "repo-db", uploads: 20, bytes: 10_000)
        let destination = try commandCenter("command_center", marker: "app-db")
        let staging = DataImporter.stagingFolder(for: destination)
        let progress = Progress.discreteProgress(totalUnitCount: -1)
        var partialCopyExisted = false
        // The count changes on the copying thread, so this cancels after the first file, every time.
        let observation = progress.observe(\.completedUnitCount) { progress, _ in
            guard progress.completedUnitCount > 0, !progress.isCancelled else { return }
            partialCopyExisted = FileManager.default.fileExists(atPath: staging.path)
            progress.cancel()
        }
        defer { observation.invalidate() }

        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: source, to: destination, now: now, progress: progress)) {
            XCTAssertTrue($0 is CancellationError, "a cancelled import says it was cancelled, not that it failed: \($0)")
        }
        XCTAssertTrue(partialCopyExisted, "the cancel has to land in the middle of the copy for this to test anything")
        XCTAssertEqual(try appData(destination), "app-db")
        XCTAssertEqual(try fileManager.contentsOfDirectory(atPath: root.path).sorted(), ["command_center", "repo"],
                       "no partial copy and no backup is left behind")
    }

    func testAnImportCancelledBeforeItsCopyStartsChangesNothing() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let destination = try commandCenter("command_center", marker: "app-db")
        let progress = Progress.discreteProgress(totalUnitCount: -1)
        progress.cancel()
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: source, to: destination, now: now, progress: progress)) {
            XCTAssertTrue($0 is CancellationError, "\($0)")
        }
        XCTAssertEqual(try appData(destination), "app-db")
        XCTAssertEqual(try fileManager.contentsOfDirectory(atPath: root.path).sorted(), ["command_center", "repo"])
    }

    /// What the import screen's bar is drawn from: every byte to copy is counted before the copy
    /// starts, and the count moves as each file is copied rather than once at the end.
    func testProgressCountsEveryByteAndMovesFileByFile() throws {
        let source = try notebook("repo", marker: "repo-db", uploads: 20, bytes: 10_000)
        let destination = root.appendingPathComponent("app/command_center", isDirectory: true)
        let progress = Progress.discreteProgress(totalUnitCount: -1)
        var seen: [Int64] = []
        let observation = progress.observe(\.completedUnitCount) { progress, _ in seen.append(progress.completedUnitCount) }
        defer { observation.invalidate() }

        _ = try DataImporter().importCommandCenter(from: source, to: destination, now: now, progress: progress)
        XCTAssertEqual(progress.totalUnitCount, 20 * 10_000 + Int64("repo-db".utf8.count))
        XCTAssertEqual(progress.completedUnitCount, progress.totalUnitCount)
        XCTAssertEqual(seen, seen.sorted(), "the count never goes backwards")
        XCTAssertGreaterThanOrEqual(Set(seen.filter { $0 > 0 && $0 < progress.totalUnitCount }).count, 19,
                                    "the count moves with each file: \(seen)")
    }

    /// A force-quit, or a quit that stopped waiting for a stalled copy, leaves the partial copy
    /// behind. It holds nothing of the student's -- the folder it came from is untouched -- and the
    /// next import removes it rather than letting it sit in Application Support for good.
    func testAPartialCopyAnImportDidNotLiveToFinishIsRemovedByTheNext() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let destination = try commandCenter("command_center", marker: "app-db")
        let abandoned = DataImporter.stagingFolder(for: destination)
        try fileManager.createDirectory(at: abandoned.appendingPathComponent("files"), withIntermediateDirectories: true)
        try Data("half a worksheet".utf8).write(to: abandoned.appendingPathComponent("files/half.bin"))

        _ = try DataImporter().importCommandCenter(from: source, to: destination, now: now)
        XCTAssertFalse(fileManager.fileExists(atPath: abandoned.path))
        XCTAssertEqual(try appData(destination), "repo-db")
        XCTAssertFalse(fileManager.fileExists(atPath: destination.appendingPathComponent("files/half.bin").path),
                       "the abandoned copy must not be mixed into the new one")
    }

    // MARK: - How many backups successful imports keep

    private func at(_ day: Int) -> Date { now.addingTimeInterval(Double(day) * 86_400) }

    private func folders(_ prefix: String) throws -> [String] {
        try fileManager.contentsOfDirectory(atPath: root.path).filter { $0.hasPrefix(prefix) }.sorted()
    }

    /// Every successful import kept the whole notebook it replaced, forever. Now the backup an
    /// import makes is kept until the next successful import, which deletes it: however many
    /// imports run, Application Support holds the data folder and one backup -- the one that holds
    /// what the latest import replaced.
    func testRepeatedImportsKeepOnlyTheBackupOfTheLatestImport() throws {
        let destination = try commandCenter("command_center", marker: "original")
        var previous = "original"
        for (day, name) in ["a", "b", "c", "d"].enumerated() {
            let source = try commandCenter("checkout-\(name)", marker: "db-\(name)")
            let result = try DataImporter().importCommandCenter(from: source, to: destination, now: at(day))
            let backup = try XCTUnwrap(result.backup)
            XCTAssertEqual(try folders("command_center."), [backup.lastPathComponent], "after import \(name)")
            XCTAssertEqual(try appData(backup), previous, "the backup holds what this import replaced")
            XCTAssertEqual(try appData(destination), "db-\(name)")
            XCTAssertEqual(result.deletedBackups.count, day == 0 ? 0 : 1)
            previous = "db-\(name)"
        }
    }

    /// Cleanup runs only once an import's copy is in place. One that fails deletes nothing, so the
    /// latest good import's backup is still there for the student to go back to.
    func testAFailedImportDeletesNoBackup() throws {
        let destination = try commandCenter("command_center", marker: "original")
        let first = try DataImporter().importCommandCenter(from: try commandCenter("checkout-a", marker: "db-a"),
                                                            to: destination, now: at(0))
        let broken = try notebook("checkout-b", marker: "db-b", uploads: 1, bytes: 10)
        let unreadable = broken.appendingPathComponent("files/upload-1.bin")
        try fileManager.setAttributes([.posixPermissions: 0o000], ofItemAtPath: unreadable.path)
        defer { try? fileManager.setAttributes([.posixPermissions: 0o644], ofItemAtPath: unreadable.path) }

        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: broken, to: destination, now: at(1)))
        XCTAssertEqual(try folders("command_center."), [try XCTUnwrap(first.backup).lastPathComponent])
        XCTAssertEqual(try appData(XCTUnwrap(first.backup)), "original")
        XCTAssertEqual(try appData(destination), "db-a")
    }

    /// A folder an import moved aside and could not put back may be the only copy of the student's
    /// data -- the server creates an empty data folder over the gap at its next start. Cleanup only
    /// ever deletes the backups successful imports named as theirs, so this one is never touched.
    func testCleanupNeverDeletesDataAnImportCouldNotPutBack() throws {
        let destination = try commandCenter("command_center", marker: "empty desk the server made")
        let stranded = try commandCenter("command_center.before-import-20270101T000000Z", marker: "the only copy")
        for (day, name) in ["a", "b"].enumerated() {
            _ = try DataImporter().importCommandCenter(from: try commandCenter("checkout-\(name)", marker: "db-\(name)"),
                                                       to: destination, now: at(day))
        }
        XCTAssertEqual(try appData(stranded), "the only copy")
    }

    /// The backups are named after the time of their import, and a clock set back would make the
    /// newest backup sort as the oldest. Which one survives is decided by which import made it.
    func testTheBackupThisImportMadeIsKeptEvenWhenTheClockHasGoneBack() throws {
        let destination = try commandCenter("command_center", marker: "original")
        _ = try DataImporter().importCommandCenter(from: try commandCenter("checkout-a", marker: "db-a"),
                                                   to: destination, now: at(5))
        let result = try DataImporter().importCommandCenter(from: try commandCenter("checkout-b", marker: "db-b"),
                                                            to: destination, now: at(1))
        let kept = try XCTUnwrap(result.backup)
        XCTAssertEqual(try folders("command_center."), [kept.lastPathComponent])
        XCTAssertEqual(try appData(kept), "db-a")
    }

    /// With no data folder to replace, an import makes no backup -- and so the backup from the
    /// import before it is still the latest good one, and stays.
    func testAnImportWithNoDataToReplaceKeepsTheLastBackup() throws {
        let destination = root.appendingPathComponent("command_center", isDirectory: true)
        let earlier = try commandCenter("command_center.backup-20270101T000000Z", marker: "earlier")
        let result = try DataImporter().importCommandCenter(from: try commandCenter("checkout-a", marker: "db-a"),
                                                            to: destination, now: at(0))
        XCTAssertNil(result.backup)
        XCTAssertEqual(result.deletedBackups, [])
        XCTAssertEqual(try appData(earlier), "earlier")
    }

    /// The success alert used to name the backup and stop. It has to say how long it is kept, and
    /// what an import deleted, so nothing disappears without the student having been told.
    func testTheSuccessAlertSaysHowLongTheBackupIsKeptAndWhatWasDeleted() throws {
        let destination = try commandCenter("command_center", marker: "original")
        let first = try DataImporter().importCommandCenter(from: try commandCenter("checkout-a", marker: "db-a"),
                                                           to: destination, now: at(0))
        let firstBackup = try XCTUnwrap(first.backup)
        XCTAssertTrue(first.summary.contains(firstBackup.path), first.summary)
        XCTAssertTrue(first.summary.contains("until your next import"), first.summary)
        XCTAssertFalse(first.summary.contains("deleted"), first.summary)

        let second = try DataImporter().importCommandCenter(from: try commandCenter("checkout-b", marker: "db-b"),
                                                            to: destination, now: at(1))
        XCTAssertEqual(second.deletedBackups.map(\.standardizedFileURL), [firstBackup.standardizedFileURL])
        XCTAssertTrue(second.summary.contains(try XCTUnwrap(second.backup).path), second.summary)
        XCTAssertTrue(second.summary.contains("deleted"), second.summary)
        XCTAssertTrue(second.summary.contains(firstBackup.path), second.summary)

        let fresh = try DataImporter().importCommandCenter(from: try commandCenter("checkout-c", marker: "db-c"),
                                                           to: root.appendingPathComponent("elsewhere/command_center"),
                                                           now: at(2))
        XCTAssertEqual(fresh.summary, "There was no previous app data.")
    }

    /// The confirmation before an import is where the student can still move a backup somewhere
    /// safe, so it names the one the import will delete.
    func testTheImportConfirmationCanNameTheBackupItWillDelete() throws {
        let destination = try commandCenter("command_center", marker: "original")
        XCTAssertEqual(try DataImporter().backupsDeletedByNextImport(at: destination), [])
        let first = try DataImporter().importCommandCenter(from: try commandCenter("checkout-a", marker: "db-a"),
                                                           to: destination, now: at(0))
        XCTAssertEqual(try DataImporter().backupsDeletedByNextImport(at: destination).map(\.standardizedFileURL),
                       [try XCTUnwrap(first.backup).standardizedFileURL])
        try fileManager.removeItem(at: destination)
        XCTAssertEqual(try DataImporter().backupsDeletedByNextImport(at: destination), [],
                       "with no data folder the import makes no backup, so it deletes none")
    }

    func testTheImportScreenSaysWhatTheCopyIsDoing() {
        let progress = Progress.discreteProgress(totalUnitCount: -1)
        XCTAssertEqual(DataImporter.progressDescription(progress), "Counting the files to copy…")
        progress.totalUnitCount = 3_000_000
        progress.completedUnitCount = 1_500_000
        XCTAssertEqual(DataImporter.progressDescription(progress), "1.5 MB of 3 MB copied")
        progress.cancel()
        XCTAssertEqual(DataImporter.progressDescription(progress), "Stopping the copy. Your data is left as it was.")
    }

    /// A rollback that fails reports only to the unified log, and the error the caller is handed
    /// is the copy's, so this is the app's only way to tell a clean rollback from one that left
    /// the student's data in a folder named after a timestamp no error mentions.
    func testRecoveryAfterACleanRollbackShowsNothingLeftBehind() throws {
        let destination = try commandCenter("command_center", marker: "app-db")
        let state = try DataImporter().recovery(at: destination)
        XCTAssertTrue(state.dataFolderPresent)
        XCTAssertEqual(state.backupsLeftBehind, [])
    }

    func testRecoveryNamesTheFoldersAnImportMovedAside() throws {
        let destination = root.appendingPathComponent("command_center", isDirectory: true)
        let older = try commandCenter("command_center.before-import-20270115T080000Z", marker: "older")
        let newer = try commandCenter("command_center.before-import-20270116T080000Z", marker: "newer")
        _ = try commandCenter("unrelated", marker: "not a backup")
        let state = try DataImporter().recovery(at: destination)
        XCTAssertFalse(state.dataFolderPresent, "a rollback that did not finish leaves no data folder")
        XCTAssertEqual(state.backupsLeftBehind.map(\.path), [older.path, newer.path],
                       "oldest first, so the newest is the one holding the data that was moved aside")
    }

    func testRecoveryOfAFolderWhoseParentIsGoneSaysWhyRatherThanReportingNothing() throws {
        let destination = root.appendingPathComponent("gone/command_center", isDirectory: true)
        XCTAssertThrowsError(try DataImporter().recovery(at: destination))
    }

    // MARK: - What a refused import tells the student

    /// Every case of this enum reached the student as its Swift form -- the alert really did
    /// read `sourceInUse("41273")` -- because `AppDelegate` interpolated the error into it.
    /// These are the sentences that replaced it, so each one is checked for the thing it tells
    /// them to do rather than for being non-empty.
    private static let everyRefusal: [DataImportError] = [
        .sourceMissing("/Users/andrew/Documents/gone"),
        .notACommandCenter("/Users/andrew/Documents"),
        .sameFolder,
        .sourceInUse("41273"),
        .backupExists("/Users/andrew/Library/Application Support/CircuitMCP/command_center.before-import-20270115T080000Z"),
    ]

    /// The list above is hand-kept, so a case added without a sentence would simply not be
    /// checked. The switch is exhaustive and the count is pinned: a new case stops compiling
    /// here until it is written and added.
    func testEveryRefusalIsCovered() {
        for refusal in Self.everyRefusal {
            switch refusal {
            case .sourceMissing, .notACommandCenter, .sameFolder, .sourceInUse, .backupExists: break
            }
        }
        XCTAssertEqual(Self.everyRefusal.count, 5, "a new DataImportError case needs a sentence and an entry above")
    }

    func testAMissingSourceSaysToChooseTheFolderAgain() {
        let message = "\(DataImportError.sourceMissing("/Users/andrew/Documents/gone"))"
        XCTAssertTrue(message.contains("/Users/andrew/Documents/gone"), "the folder they chose: \(message)")
        XCTAssertTrue(message.contains("Choose the folder again"), "the action to take: \(message)")
    }

    func testAFolderWithoutADatabaseSaysWhatMakesAFolderImportable() {
        let message = "\(DataImportError.notACommandCenter("/Users/andrew/Documents"))"
        XCTAssertTrue(message.contains("/Users/andrew/Documents"), "the folder they chose: \(message)")
        XCTAssertTrue(message.contains("circuit_mcp.sqlite3"), "what was looked for and not found: \(message)")
        XCTAssertTrue(message.contains("command_center"), "the folder to choose instead: \(message)")
    }

    func testImportingAFolderIntoItselfSaysWhichFolderToPickInstead() {
        let message = "\(DataImportError.sameFolder)"
        XCTAssertTrue(message.contains(".local/command_center"), "where a checkout keeps its data: \(message)")
        XCTAssertTrue(message.contains("inside the other"), "nesting is the other half of this refusal: \(message)")
    }

    /// The README tells the student to stop `run_ui.py` before importing, so this is the refusal
    /// they are most likely to see. It has to name the thing to quit.
    func testASourceInUseNamesTheProcessAndWhatToQuit() {
        let message = "\(DataImportError.sourceInUse("41273"))"
        XCTAssertTrue(message.contains("41273"), "the process holding the lock: \(message)")
        XCTAssertTrue(message.contains("run_ui.py"), "what the README told them to stop: \(message)")
        XCTAssertTrue(message.contains("try the import again"), "the action to take: \(message)")
    }

    func testAnExistingBackupNamesTheFolderInTheWay() {
        let aside = "/Users/andrew/Library/Application Support/CircuitMCP/command_center.before-import-20270115T080000Z"
        let message = "\(DataImportError.backupExists(aside))"
        XCTAssertTrue(message.contains(aside), "the folder in the way: \(message)")
        XCTAssertTrue(message.lowercased().contains("rename"), "the action to take: \(message)")
    }

    /// The defect this replaces was not that the text was terse but that it was Swift source.
    func testNoRefusalShowsItsSwiftCaseName() {
        let caseNames = ["sourceMissing", "notACommandCenter", "sameFolder", "sourceInUse", "backupExists"]
        for refusal in Self.everyRefusal {
            let message = "\(refusal)"
            for name in caseNames {
                XCTAssertFalse(message.contains(name), "\(name) is developer text and reached the alert: \(message)")
            }
            XCTAssertFalse(message.contains("(\""), "a rendered associated value reached the alert: \(message)")
            XCTAssertTrue(message.hasSuffix("."), "the alert shows sentences: \(message)")
        }
    }

    /// `"\(error)"` is what the alert interpolates and `localizedDescription` is what any future
    /// caller reaches for. Neither may be the one that says "DataImportError error 3".
    func testBothRenderingsOfARefusalAreTheSameSentence() {
        for refusal in Self.everyRefusal {
            XCTAssertEqual(refusal.localizedDescription, "\(refusal)")
            XCTAssertFalse(refusal.localizedDescription.contains("DataImportError"), refusal.localizedDescription)
        }
    }

    /// Every one of these is thrown before the destination is moved or a byte is copied, which is
    /// what lets the alert close with "Nothing was changed." rather than "Your data is back the
    /// way it was" -- a sentence that implies a move on a path where none ever happened.
    func testEveryRefusalIsRaisedBeforeAnythingOnDiskIsTouched() {
        for refusal in Self.everyRefusal {
            XCTAssertTrue(refusal.refusedBeforeTouchingDisk, "\(refusal)")
        }
    }

    /// The claim above is only as good as the disk it is checked against: a refusal that had
    /// moved the data aside would leave a backup folder behind and make the sentence untrue.
    func testARefusedImportLeavesTheDestinationAndItsParentUntouched() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let destination = try commandCenter("command_center", marker: "app-db")
        let lockPath = source.appendingPathComponent("server.lock").path
        try Data("777".utf8).write(to: URL(fileURLWithPath: lockPath))
        let held = open(lockPath, O_RDWR)
        XCTAssertEqual(flock(held, LOCK_EX | LOCK_NB), 0)
        defer { close(held) }

        let before = try fileManager.contentsOfDirectory(atPath: root.path).sorted()
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: source, to: destination, now: now))
        XCTAssertEqual(try fileManager.contentsOfDirectory(atPath: root.path).sorted(), before)
        XCTAssertEqual(try String(contentsOf: destination.appendingPathComponent("circuit_mcp.sqlite3")), "app-db")
    }
}
