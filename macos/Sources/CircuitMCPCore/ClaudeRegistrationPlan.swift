import Foundation

public struct RegistrationMessage: Equatable {
    public let title: String
    public let detail: String

    public init(title: String, detail: String) {
        self.title = title
        self.detail = detail
    }
}

public enum ClaudeRegistrationPreparation: Equatable {
    case copyCommand(String)
    case run(executable: URL, command: String, arguments: [String])
    case failure(RegistrationMessage)
}

public enum ClaudeRegistrationPlan {
    public static func prepare(
        claudeExecutable: URL?,
        displayName: String = "Circuit MCP",
        command: (URL?) throws -> String,
        arguments: () throws -> [String]
    ) -> ClaudeRegistrationPreparation {
        do {
            let rendered = try command(claudeExecutable)
            guard let claudeExecutable else { return .copyCommand(rendered) }
            return .run(executable: claudeExecutable, command: rendered,
                        arguments: try arguments())
        } catch MCPCommandError.translocated {
            return .failure(RegistrationMessage(
                title: "Move \(displayName) to Applications first.",
                detail: "macOS is running the app from a temporary copy, so its path would change after a restart."))
        } catch {
            let title = claudeExecutable == nil
                ? "Claude Code was not found, and the Terminal command could not be built."
                : "The Claude Code command could not be built."
            return .failure(RegistrationMessage(title: title, detail: error.localizedDescription))
        }
    }
}
