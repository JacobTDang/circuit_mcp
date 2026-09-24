import XCTest
@testable import CircuitMCPCore

final class AppLocationsTests: XCTestCase {
    private let library = URL(fileURLWithPath: "/Users/someone/Library", isDirectory: true)

    func testDefaultsLiveUnderTheUsersLibrary() throws {
        let locations = try AppLocations.current(environment: [:], homeLibrary: library)
        XCTAssertEqual(locations.supportDirectory.path, "/Users/someone/Library/Application Support/CircuitMCP")
        XCTAssertEqual(locations.logsDirectory.path, "/Users/someone/Library/Logs/CircuitMCP")
        XCTAssertEqual(locations.commandCenterDirectory.path, "/Users/someone/Library/Application Support/CircuitMCP/command_center")
        XCTAssertEqual(locations.showmanDirectory.path, "/Users/someone/Library/Application Support/CircuitMCP/showman")
        XCTAssertEqual(locations.workspaceConfig.path, "/Users/someone/Library/Application Support/CircuitMCP/workspace.json")
    }

    func testCircuitMCPHomeReplacesTheLibrary() throws {
        let locations = try AppLocations.current(environment: ["CIRCUIT_MCP_APP_HOME": "/tmp/circuitmcp-test"], homeLibrary: library)
        XCTAssertEqual(locations.supportDirectory.path, "/tmp/circuitmcp-test/Application Support/CircuitMCP")
        XCTAssertEqual(locations.logsDirectory.path, "/tmp/circuitmcp-test/Logs/CircuitMCP")
    }

    func testAnEmptyCircuitMCPHomeIsRefused() {
        XCTAssertThrowsError(try AppLocations.current(environment: ["CIRCUIT_MCP_APP_HOME": ""], homeLibrary: library)) { error in
            XCTAssertEqual(error as? AppLocationsError, .emptyHome)
        }
    }

    func testCreateDirectoriesMakesBothFolders() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let locations = try AppLocations.current(environment: ["CIRCUIT_MCP_APP_HOME": root.path], homeLibrary: library)
        try locations.createDirectories()
        var isDirectory: ObjCBool = false
        XCTAssertTrue(FileManager.default.fileExists(atPath: locations.supportDirectory.path, isDirectory: &isDirectory) && isDirectory.boolValue)
        XCTAssertTrue(FileManager.default.fileExists(atPath: locations.logsDirectory.path, isDirectory: &isDirectory) && isDirectory.boolValue)
    }

    func testBundledPythonIsInsideTheApp() {
        let app = URL(fileURLWithPath: "/Applications/Circuit MCP.app", isDirectory: true)
        XCTAssertEqual(AppLocations.bundledPython(in: app).path,
                       "/Applications/Circuit MCP.app/Contents/Resources/python/bin/python3")
    }

    func testTranslocationIsDetectedFromThePath() {
        XCTAssertTrue(AppLocations.isTranslocated(URL(fileURLWithPath: "/private/var/folders/x/T/AppTranslocation/ABC/d/Circuit MCP.app")))
        XCTAssertFalse(AppLocations.isTranslocated(URL(fileURLWithPath: "/Applications/Circuit MCP.app")))
    }
}
