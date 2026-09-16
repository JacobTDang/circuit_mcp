import XCTest
@testable import PrepPalCore

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
        XCTAssertEqual(backup.lastPathComponent, "command_center.before-import-20270115T080000Z")
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
        let backup = root.appendingPathComponent("command_center.before-import-20270115T080000Z", isDirectory: true)
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
            .filter { $0.hasPrefix("command_center.before-import-") }
        XCTAssertEqual(leftovers, [], "the data is back at the destination, so no backup may be left behind")
        XCTAssertEqual(try String(contentsOf: source.appendingPathComponent("circuit_mcp.sqlite3")), "repo-db")
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
}
