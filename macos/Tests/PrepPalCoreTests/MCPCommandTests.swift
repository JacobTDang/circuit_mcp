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

    /// A client started from this config runs the same server the app runs, so it has to reach
    /// the same folders: with only the data variables it reported OCR and the runtime tools
    /// against bundle-internal paths, disagreeing with the app about the very same install.
    func testTheConfigCarriesEveryFolderTheAppsOwnServerGetsAndNothingElse() throws {
        let app = URL(fileURLWithPath: "/Applications/Andrew's PrepPal.app", isDirectory: true)
        let json = try MCPCommand.configJSON(appBundle: app, locations: locations)
        let parsed = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        let circuit = try XCTUnwrap((parsed["mcpServers"] as? [String: Any])?["circuit"] as? [String: Any])
        let env = try XCTUnwrap(circuit["env"] as? [String: String])
        let support = "/Users/someone/Library/Application Support/PrepPal"
        XCTAssertEqual(env, ["CIRCUIT_MCP_DATA_DIR": "\(support)/command_center",
                             "CIRCUIT_MCP_SHOWMAN_DATA_DIR": "\(support)/showman",
                             "CIRCUIT_MCP_WORKSPACE_CONFIG": "\(support)/workspace.json",
                             "CIRCUIT_MCP_RUNTIME_DIR": "\(support)/runtime",
                             "CIRCUIT_MCP_OCR_PYTHON": "\(support)/ocr/venv/bin/python",
                             "CIRCUIT_MCP_OCR_MODEL": "\(support)/ocr/models/unimernet_small"])
    }
}
