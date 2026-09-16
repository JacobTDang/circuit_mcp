import Foundation

public enum MCPCommandError: Error, Equatable {
    case translocated(String)
}

/// The Claude Code MCP config for the app's current location. Always generated, never stored,
/// because it contains the app's path.
public enum MCPCommand {
    public static func configJSON(appBundle: URL, locations: AppLocations) throws -> String {
        guard !AppLocations.isTranslocated(appBundle) else { throw MCPCommandError.translocated(appBundle.path) }
        let config: [String: Any] = [
            "mcpServers": [
                "circuit": [
                    "command": AppLocations.bundledPython(in: appBundle).path,
                    "args": ["-m", "circuit_mcp.server"],
                    "env": ServerEnvironment.clientVariables(locations: locations),
                ],
            ],
        ]
        let data = try JSONSerialization.data(withJSONObject: config, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        return String(decoding: data, as: UTF8.self)
    }
}
