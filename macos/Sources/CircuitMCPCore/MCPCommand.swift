import Foundation

public enum MCPCommandError: Error, Equatable {
    case translocated(String)
}

/// The Claude Code MCP config for the app's current location. Always generated, never stored,
/// because it contains the app's path.
public enum MCPCommand {
    public static func configJSON(appBundle: URL, locations: AppLocations,
                                  fileManager: FileManager = .default) throws -> String {
        let config: [String: Any] = [
            "mcpServers": ["circuit": try serverObject(appBundle: appBundle, locations: locations,
                                                        fileManager: fileManager)],
        ]
        let data = try JSONSerialization.data(withJSONObject: config, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        return String(decoding: data, as: UTF8.self)
    }

    /// A single command for Terminal. Claude accepts the server object itself, not the wrapper
    /// used in a project configuration file, and single-quoting keeps every JSON character
    /// literal. An apostrophe is the one character that must leave and re-enter single quotes.
    public static func terminalCommand(appBundle: URL, locations: AppLocations,
                                       fileManager: FileManager = .default) throws -> String {
        let server = try serverObject(appBundle: appBundle, locations: locations,
                                      fileManager: fileManager)
        let data = try JSONSerialization.data(withJSONObject: server,
                                              options: [.sortedKeys, .withoutEscapingSlashes])
        let json = String(decoding: data, as: UTF8.self)
        return "claude mcp add-json -s user circuit '\(shellSingleQuotedContents(json))'"
    }

    private static func serverObject(appBundle: URL, locations: AppLocations,
                                     fileManager: FileManager) throws -> [String: Any] {
        guard !AppLocations.isTranslocated(appBundle) else {
            throw MCPCommandError.translocated(appBundle.path)
        }
        return [
            "command": AppLocations.bundledPython(in: appBundle).path,
            "args": ["-m", "circuit_mcp.server"],
            "env": ServerEnvironment.clientVariables(locations: locations, appBundle: appBundle,
                                                      fileManager: fileManager),
        ]
    }

    private static func shellSingleQuotedContents(_ text: String) -> String {
        text.replacingOccurrences(of: "'", with: "'\\''")
    }
}
