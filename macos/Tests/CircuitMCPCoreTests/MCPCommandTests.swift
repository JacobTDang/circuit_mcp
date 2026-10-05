import XCTest
@testable import CircuitMCPCore

final class MCPCommandTests: XCTestCase {
    private let locations = try! AppLocations.current(environment: [:], homeLibrary: URL(fileURLWithPath: "/Users/someone/Library"))

    private final class NoBundledToolsFileManager: FileManager {
        override func isExecutableFile(atPath path: String) -> Bool { false }
    }

    func testTheConfigPointsClaudeCodeAtTheBundledPythonAndTheAppData() throws {
        let app = URL(fileURLWithPath: "/Applications/Circuit MCP.app", isDirectory: true)
        let json = try MCPCommand.configJSON(appBundle: app, locations: locations,
                                             fileManager: NoBundledToolsFileManager())
        let parsed = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        let circuit = try XCTUnwrap((parsed["mcpServers"] as? [String: Any])?["circuit"] as? [String: Any])
        XCTAssertEqual(circuit["command"] as? String, "/Applications/Circuit MCP.app/Contents/Resources/python/bin/python3")
        XCTAssertEqual(circuit["args"] as? [String], ["-m", "circuit_mcp.server"])
        let env = try XCTUnwrap(circuit["env"] as? [String: String])
        XCTAssertEqual(env["CIRCUIT_MCP_DATA_DIR"], "/Users/someone/Library/Application Support/CircuitMCP/command_center")
        XCTAssertNil(env["OPENROUTER_API_KEY"])
    }

    func testATranslocatedAppIsRefused() {
        let app = URL(fileURLWithPath: "/private/var/folders/x/T/AppTranslocation/ABC/d/Circuit MCP.app")
        XCTAssertThrowsError(try MCPCommand.configJSON(appBundle: app, locations: locations)) {
            XCTAssertEqual($0 as? MCPCommandError, .translocated(app.path))
        }
    }

    func testTheTerminalCommandShellQuotesSpacesAndApostrophesInTheBundlePath() throws {
        let app = URL(fileURLWithPath: "/Applications/Student's Circuit MCP.app", isDirectory: true)
        let command = try MCPCommand.terminalCommand(appBundle: app, locations: locations,
                                                     fileManager: NoBundledToolsFileManager())
        XCTAssertTrue(command.hasPrefix("claude mcp add-json -s user circuit '"), command)
        XCTAssertTrue(command.contains("Student'\\''s Circuit MCP.app"), command)
        XCTAssertFalse(command.contains("\n"), "a Terminal command must stay on one line")
    }

    func testTheTerminalCommandCarriesOnlyTheInnerServerObjectAsJSON() throws {
        let app = URL(fileURLWithPath: "/Applications/Student's Circuit MCP.app", isDirectory: true)
        let command = try MCPCommand.terminalCommand(appBundle: app, locations: locations,
                                                     fileManager: NoBundledToolsFileManager())
        let prefix = "claude mcp add-json -s user circuit '"
        let quoted = String(command.dropFirst(prefix.count).dropLast())
        let json = quoted.replacingOccurrences(of: "'\\''", with: "'")
        let server = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        XCTAssertNil(server["mcpServers"])
        XCTAssertEqual(server["command"] as? String,
                       "/Applications/Student's Circuit MCP.app/Contents/Resources/python/bin/python3")
        XCTAssertEqual(server["args"] as? [String], ["-m", "circuit_mcp.server"])
    }

    /// A client started from this config runs the same server the app runs, so it has to reach
    /// the same folders: with only the data variables it reported OCR and the runtime tools
    /// against bundle-internal paths, disagreeing with the app about the very same install.
    func testTheConfigCarriesEveryFolderTheAppsOwnServerGetsAndNothingElse() throws {
        let app = URL(fileURLWithPath: "/Applications/Circuit MCP.app", isDirectory: true)
        let json = try MCPCommand.configJSON(appBundle: app, locations: locations,
                                             fileManager: NoBundledToolsFileManager())
        let parsed = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        let circuit = try XCTUnwrap((parsed["mcpServers"] as? [String: Any])?["circuit"] as? [String: Any])
        let env = try XCTUnwrap(circuit["env"] as? [String: String])
        let support = "/Users/someone/Library/Application Support/CircuitMCP"
        XCTAssertEqual(env, ["CIRCUIT_MCP_DATA_DIR": "\(support)/command_center",
                             "CIRCUIT_MCP_SHOWMAN_DATA_DIR": "\(support)/showman",
                             "CIRCUIT_MCP_WORKSPACE_CONFIG": "\(support)/workspace.json",
                             "CIRCUIT_MCP_RUNTIME_DIR": "\(support)/runtime",
                             "CIRCUIT_MCP_OCR_PYTHON": "\(support)/ocr/venv/bin/python",
                             "CIRCUIT_MCP_OCR_MODEL": "\(support)/ocr/models/unimernet_small"])
    }
}
