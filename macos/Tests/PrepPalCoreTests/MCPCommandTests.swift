import XCTest
@testable import PrepPalCore

final class MCPCommandTests: XCTestCase {
    private let locations = try! AppLocations.current(environment: [:], homeLibrary: URL(fileURLWithPath: "/Users/someone/Library"))

    func testTheConfigPointsClaudeCodeAtTheBundledPythonAndTheAppData() throws {
        let app = URL(fileURLWithPath: "/Applications/Andrew's PrepPal.app", isDirectory: true)
        let json = try MCPCommand.configJSON(appBundle: app, locations: locations)
        let parsed = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        let circuit = try XCTUnwrap((parsed["mcpServers"] as? [String: Any])?["circuit"] as? [String: Any])
        XCTAssertEqual(circuit["command"] as? String, "/Applications/Andrew's PrepPal.app/Contents/Resources/python/bin/python3")
        XCTAssertEqual(circuit["args"] as? [String], ["-m", "circuit_mcp.server"])
        let env = try XCTUnwrap(circuit["env"] as? [String: String])
        XCTAssertEqual(env["CIRCUIT_MCP_DATA_DIR"], "/Users/someone/Library/Application Support/PrepPal/command_center")
        XCTAssertNil(env["OPENROUTER_API_KEY"])
    }

    func testATranslocatedAppIsRefused() {
        let app = URL(fileURLWithPath: "/private/var/folders/x/T/AppTranslocation/ABC/d/Andrew's PrepPal.app")
        XCTAssertThrowsError(try MCPCommand.configJSON(appBundle: app, locations: locations)) {
            XCTAssertEqual($0 as? MCPCommandError, .translocated(app.path))
        }
    }

    /// The user pastes this into a JSON file by hand, so the apostrophe in the app name and the
    /// slashes in every path have to survive the round trip unescaped and readable.
    func testTheConfigIsPastableJSON() throws {
        let app = URL(fileURLWithPath: "/Applications/Andrew's PrepPal.app", isDirectory: true)
        let json = try MCPCommand.configJSON(appBundle: app, locations: locations)
        XCTAssertTrue(json.contains("/Applications/Andrew's PrepPal.app/Contents/Resources/python/bin/python3"), json)
        XCTAssertFalse(json.contains("\\/"), json)
        XCTAssertTrue(json.contains("\n"), "the config is pasted into a file, so it is pretty-printed")
    }
}
