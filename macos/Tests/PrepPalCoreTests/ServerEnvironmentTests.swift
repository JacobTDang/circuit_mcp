import XCTest
@testable import PrepPalCore

final class ServerEnvironmentTests: XCTestCase {
    private let locations = try! AppLocations.current(environment: ["PREPPAL_HOME": "/tmp/pp"],
                                                     homeLibrary: URL(fileURLWithPath: "/unused"))

    func testTheServerGetsItsFoldersABoundedPathAndTheSecrets() {
        let variables = ServerEnvironment.variables(locations: locations,
                                                    secrets: ["OPENROUTER_API_KEY": "sk-or-key"],
                                                    home: "/Users/someone")
        XCTAssertEqual(variables["CIRCUIT_MCP_DATA_DIR"], "/tmp/pp/Application Support/PrepPal/command_center")
        XCTAssertEqual(variables["CIRCUIT_MCP_SHOWMAN_DATA_DIR"], "/tmp/pp/Application Support/PrepPal/showman")
        XCTAssertEqual(variables["CIRCUIT_MCP_WORKSPACE_CONFIG"], "/tmp/pp/Application Support/PrepPal/workspace.json")
        XCTAssertEqual(variables["OPENROUTER_API_KEY"], "sk-or-key")
        XCTAssertEqual(variables["HOME"], "/Users/someone")
        XCTAssertEqual(variables["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")
        XCTAssertEqual(variables["PYTHONDONTWRITEBYTECODE"], "1")
        XCTAssertNil(variables["PYTHONPATH"])
    }

    func testDataVariablesCarryNoSecrets() {
        let variables = ServerEnvironment.dataVariables(locations: locations)
        XCTAssertEqual(Set(variables.keys), ["CIRCUIT_MCP_DATA_DIR", "CIRCUIT_MCP_SHOWMAN_DATA_DIR", "CIRCUIT_MCP_WORKSPACE_CONFIG"])
    }

    /// Unset, `paths.ocr_python()` and `paths.ocr_model()` fall back to `REPO_ROOT`, which inside
    /// the bundle is `Resources/python/lib/python3.12`: read-only paths no installer can ever
    /// fill. Both must name the writable folder sub-project 5 installs into instead.
    func testOCRPointsAtTheWritableDataFolderAndNotIntoTheBundle() {
        let variables = ServerEnvironment.variables(locations: locations, secrets: [:], home: "/Users/someone")
        XCTAssertEqual(variables["CIRCUIT_MCP_OCR_PYTHON"], "/tmp/pp/Application Support/PrepPal/ocr/venv/bin/python")
        XCTAssertEqual(variables["CIRCUIT_MCP_OCR_MODEL"], "/tmp/pp/Application Support/PrepPal/ocr/models/unimernet_small")
    }

    /// `paths._override` raises "is set but empty" for any variable that is set to blank, which
    /// would take down every tool that reads a path, not just the empty one.
    func testNoVariableIsBlank() {
        let variables = ServerEnvironment.variables(locations: locations,
                                                    secrets: ["OPENROUTER_API_KEY": "sk-or-key"],
                                                    home: "/Users/someone")
        for (name, value) in variables {
            XCTAssertFalse(value.trimmingCharacters(in: .whitespaces).isEmpty, "\(name) is blank")
        }
    }

    /// Unset, `paths.runtime_dir()` falls back to `REPO_ROOT/.local/runtime`, which inside the
    /// bundle is under `Resources/python/lib/python3.12`. `ipad_capture` *writes* `RUNTIME/ipad`,
    /// so leaving it unset aims a write at a read-only folder instead of reporting a reason.
    func testTheRuntimeFolderIsWritableAndNotInsideTheBundle() {
        let variables = ServerEnvironment.variables(locations: locations, secrets: [:], home: "/Users/someone")
        XCTAssertEqual(variables["CIRCUIT_MCP_RUNTIME_DIR"], "/tmp/pp/Application Support/PrepPal/runtime")
    }
}
