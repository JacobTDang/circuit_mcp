import XCTest
@testable import CircuitMCPCore

final class ServerEnvironmentTests: XCTestCase {
    private let locations = try! AppLocations.current(environment: ["CIRCUIT_MCP_APP_HOME": "/tmp/pp"],
                                                     homeLibrary: URL(fileURLWithPath: "/unused"))

    func testTheServerGetsItsFoldersABoundedPathAndTheSecrets() {
        let variables = ServerEnvironment.variables(locations: locations,
                                                    secrets: ["OPENROUTER_API_KEY": "sk-or-key"],
                                                    home: "/Users/someone")
        XCTAssertEqual(variables["CIRCUIT_MCP_DATA_DIR"], "/tmp/pp/Application Support/CircuitMCP/command_center")
        XCTAssertEqual(variables["CIRCUIT_MCP_SHOWMAN_DATA_DIR"], "/tmp/pp/Application Support/CircuitMCP/showman")
        XCTAssertEqual(variables["CIRCUIT_MCP_WORKSPACE_CONFIG"], "/tmp/pp/Application Support/CircuitMCP/workspace.json")
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
        XCTAssertEqual(variables["CIRCUIT_MCP_OCR_PYTHON"], "/tmp/pp/Application Support/CircuitMCP/ocr/venv/bin/python")
        XCTAssertEqual(variables["CIRCUIT_MCP_OCR_MODEL"], "/tmp/pp/Application Support/CircuitMCP/ocr/models/unimernet_small")
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
        XCTAssertEqual(variables["CIRCUIT_MCP_RUNTIME_DIR"], "/tmp/pp/Application Support/CircuitMCP/runtime")
    }
}

/// The simulator the app carries. A Mac that never installed Homebrew has no `ngspice` on PATH,
/// so without this every `simulate_spice`, every `expected` card and every `compare_readings`
/// fails on the machine the app was built for.
extension ServerEnvironmentTests {
    private func bundle(withSimulator: Bool) throws -> URL {
        let root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("circuitmcp-ngspice-\(UUID().uuidString)/CircuitMCP.app", isDirectory: true)
        let binary = AppLocations.bundledNgspice(in: root)
        try FileManager.default.createDirectory(at: binary.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        if withSimulator {
            FileManager.default.createFile(atPath: binary.path, contents: Data("#!/bin/sh\n".utf8),
                                           attributes: [.posixPermissions: 0o755])
        }
        addTeardownBlock { try? FileManager.default.removeItem(at: root.deletingLastPathComponent()) }
        return root
    }

    func testTheBundledSimulatorIsNamedWhenTheAppCarriesOne() throws {
        let app = try bundle(withSimulator: true)
        let variables = ServerEnvironment.variables(locations: locations, secrets: [:],
                                                    home: "/Users/someone", appBundle: app)
        XCTAssertEqual(variables["CIRCUIT_MCP_NGSPICE"],
                       app.appendingPathComponent("Contents/Resources/ngspice/bin/ngspice").path)
    }

    /// Naming a binary that is not there is worse than naming none: `paths.ngspice()` refuses a
    /// variable it cannot run, so every simulation would fail with a broken-bundle message on a
    /// machine whose own ngspice was sitting on PATH the whole time.
    func testNoSimulatorVariableWhenTheBundleCarriesNone() throws {
        let app = try bundle(withSimulator: false)
        let variables = ServerEnvironment.variables(locations: locations, secrets: [:],
                                                    home: "/Users/someone", appBundle: app)
        XCTAssertNil(variables["CIRCUIT_MCP_NGSPICE"])
    }

    /// An MCP client started from the generated config runs the same tools against the same
    /// install, so it needs the same simulator the app's own server uses.
    func testTheGeneratedMCPConfigNamesTheBundledSimulator() throws {
        let app = try bundle(withSimulator: true)
        let json = try MCPCommand.configJSON(appBundle: app, locations: locations)
        XCTAssertTrue(json.contains("\"CIRCUIT_MCP_NGSPICE\""), json)
        XCTAssertTrue(json.contains("Contents/Resources/ngspice/bin/ngspice"), json)
    }
}

/// The AirPlay receiver the app carries. UxPlay is GStreamer, which finds its plugins through
/// the environment rather than through its own load commands, so naming the binary is not
/// enough: without the plugin path, the scanner and a writable registry, a bundled GStreamer
/// silently uses whatever the machine has, or nothing.
extension ServerEnvironmentTests {
    private func receiverBundle(withReceiver: Bool) throws -> URL {
        let root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("circuitmcp-uxplay-\(UUID().uuidString)/CircuitMCP.app", isDirectory: true)
        let binary = AppLocations.bundledUxplay(in: root)
        try FileManager.default.createDirectory(at: binary.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        if withReceiver {
            FileManager.default.createFile(atPath: binary.path, contents: Data("#!/bin/sh\n".utf8),
                                           attributes: [.posixPermissions: 0o755])
        }
        addTeardownBlock { try? FileManager.default.removeItem(at: root.deletingLastPathComponent()) }
        return root
    }

    func testTheBundledReceiverAndItsPluginsAreNamedWhenTheAppCarriesThem() throws {
        let app = try receiverBundle(withReceiver: true)
        let variables = ServerEnvironment.variables(locations: locations, secrets: [:],
                                                    home: "/Users/someone", appBundle: app)
        let resources = app.appendingPathComponent("Contents/Resources/uxplay")
        XCTAssertEqual(variables["CIRCUIT_MCP_UXPLAY"],
                       resources.appendingPathComponent("bin/uxplay").path)
        XCTAssertEqual(variables["GST_PLUGIN_SYSTEM_PATH"],
                       resources.appendingPathComponent("plugins").path)
        XCTAssertEqual(variables["GST_PLUGIN_PATH"],
                       resources.appendingPathComponent("plugins").path)
        XCTAssertEqual(variables["GST_PLUGIN_SCANNER"],
                       resources.appendingPathComponent("libexec/gst-plugin-scanner").path)
    }

    /// A .app is read-only once installed. GStreamer writes its registry on first run, so
    /// pointed inside the bundle every launch rescans 7 plugins and warns, and pointed at
    /// nothing it rescans forever.
    func testTheGStreamerRegistryGoesSomewhereWritable() throws {
        let app = try receiverBundle(withReceiver: true)
        let variables = ServerEnvironment.variables(locations: locations, secrets: [:],
                                                    home: "/Users/someone", appBundle: app)
        let registry = try XCTUnwrap(variables["GST_REGISTRY"])
        XCTAssertTrue(registry.hasPrefix(locations.supportDirectory.path), registry)
        XCTAssertFalse(registry.hasPrefix(app.path), registry)
    }

    /// Naming a receiver that is not there would be worse than naming none: `paths.uxplay()`
    /// refuses a variable it cannot run, so a bundle built without the uxplay stage would
    /// report a broken receiver on a machine whose own build was sitting in the runtime folder.
    func testNoReceiverVariablesWhenTheBundleCarriesNone() throws {
        let app = try receiverBundle(withReceiver: false)
        let variables = ServerEnvironment.variables(locations: locations, secrets: [:],
                                                    home: "/Users/someone", appBundle: app)
        for name in ["CIRCUIT_MCP_UXPLAY", "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_PATH",
                     "GST_PLUGIN_SCANNER", "GST_REGISTRY"] {
            XCTAssertNil(variables[name], name)
        }
    }
}
