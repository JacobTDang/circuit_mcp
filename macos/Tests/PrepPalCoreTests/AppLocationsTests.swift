import XCTest
@testable import PrepPalCore

final class AppLocationsTests: XCTestCase {
    private let library = URL(fileURLWithPath: "/Users/someone/Library", isDirectory: true)

    func testDefaultsLiveUnderTheUsersLibrary() throws {
        let locations = try AppLocations.current(environment: [:], homeLibrary: library)
        XCTAssertEqual(locations.supportDirectory.path, "/Users/someone/Library/Application Support/PrepPal")
        XCTAssertEqual(locations.logsDirectory.path, "/Users/someone/Library/Logs/PrepPal")
        XCTAssertEqual(locations.commandCenterDirectory.path, "/Users/someone/Library/Application Support/PrepPal/command_center")
        XCTAssertEqual(locations.showmanDirectory.path, "/Users/someone/Library/Application Support/PrepPal/showman")
        XCTAssertEqual(locations.workspaceConfig.path, "/Users/someone/Library/Application Support/PrepPal/workspace.json")
    }

    func testPrepPalHomeReplacesTheLibrary() throws {
        let locations = try AppLocations.current(environment: ["PREPPAL_HOME": "/tmp/preppal-test"], homeLibrary: library)
        XCTAssertEqual(locations.supportDirectory.path, "/tmp/preppal-test/Application Support/PrepPal")
        XCTAssertEqual(locations.logsDirectory.path, "/tmp/preppal-test/Logs/PrepPal")
    }

    func testAnEmptyPrepPalHomeIsRefused() {
        XCTAssertThrowsError(try AppLocations.current(environment: ["PREPPAL_HOME": ""], homeLibrary: library)) { error in
            XCTAssertEqual(error as? AppLocationsError, .emptyHome)
        }
    }

    func testCreateDirectoriesMakesBothFolders() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let locations = try AppLocations.current(environment: ["PREPPAL_HOME": root.path], homeLibrary: library)
        try locations.createDirectories()
        var isDirectory: ObjCBool = false
        XCTAssertTrue(FileManager.default.fileExists(atPath: locations.supportDirectory.path, isDirectory: &isDirectory) && isDirectory.boolValue)
        XCTAssertTrue(FileManager.default.fileExists(atPath: locations.logsDirectory.path, isDirectory: &isDirectory) && isDirectory.boolValue)
    }

    func testBundledPythonIsInsideTheApp() {
        let app = URL(fileURLWithPath: "/Applications/Andrew's PrepPal.app", isDirectory: true)
        XCTAssertEqual(AppLocations.bundledPython(in: app).path,
                       "/Applications/Andrew's PrepPal.app/Contents/Resources/python/bin/python3")
    }

    func testTranslocationIsDetectedFromThePath() {
        XCTAssertTrue(AppLocations.isTranslocated(URL(fileURLWithPath: "/private/var/folders/x/T/AppTranslocation/ABC/d/Andrew's PrepPal.app")))
        XCTAssertFalse(AppLocations.isTranslocated(URL(fileURLWithPath: "/Applications/Andrew's PrepPal.app")))
    }
}
