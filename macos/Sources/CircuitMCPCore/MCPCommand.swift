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
                                       fileManager: FileManager = .default,
                                       claudeExecutable: URL? = nil) throws -> String {
        let arguments = try registrationArguments(appBundle: appBundle, locations: locations,
                                                  fileManager: fileManager)
        let executable = claudeExecutable.map { "'\(shellSingleQuotedContents($0.path))'" } ?? "claude"
        return "\(executable) mcp add-json -s user circuit '\(shellSingleQuotedContents(arguments.last!))'"
    }

    /// Raw arguments let the app use `Process` instead of asking a shell to reinterpret JSON.
    public static func registrationArguments(appBundle: URL, locations: AppLocations,
                                             fileManager: FileManager = .default) throws -> [String] {
        let server = try serverObject(appBundle: appBundle, locations: locations,
                                      fileManager: fileManager)
        let data = try JSONSerialization.data(withJSONObject: server,
                                              options: [.sortedKeys, .withoutEscapingSlashes])
        return ["mcp", "add-json", "-s", "user", "circuit", String(decoding: data, as: UTF8.self)]
    }

    /// Claude Code is commonly installed outside an app's deliberately short PATH. Check only
    /// documented user and package-manager locations, and require an executable rather than
    /// returning a path whose launch will fail later.
    public static func findClaude(homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser,
                                  fileManager: FileManager = .default) -> URL? {
        let candidates = [
            homeDirectory.appendingPathComponent(".local/bin/claude"),
            URL(fileURLWithPath: "/opt/homebrew/bin/claude"),
            URL(fileURLWithPath: "/usr/local/bin/claude"),
            homeDirectory.appendingPathComponent(".claude/local/claude"),
            homeDirectory.appendingPathComponent(".claude/local/bin/claude"),
        ]
        return candidates.first { fileManager.isExecutableFile(atPath: $0.path) }
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
