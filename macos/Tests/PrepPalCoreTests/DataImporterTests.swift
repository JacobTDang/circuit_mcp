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
}
