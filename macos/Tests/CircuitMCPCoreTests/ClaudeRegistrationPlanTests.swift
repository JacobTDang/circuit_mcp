import XCTest
@testable import CircuitMCPCore

final class ClaudeRegistrationPlanTests: XCTestCase {
    private let claude = URL(fileURLWithPath: "/tmp/fake-claude")

    func testMissingClaudeWithAStableBundleCopiesTheFallbackCommand() {
        let result = ClaudeRegistrationPlan.prepare(
            claudeExecutable: nil,
            command: { executable in
                XCTAssertNil(executable)
                return "claude mcp add-json"
            },
            arguments: { XCTFail("arguments are not needed without Claude"); return [] })

        XCTAssertEqual(result, .copyCommand("claude mcp add-json"))
    }

    func testFoundClaudeWithAStableBundleRunsTheExecutable() {
        let result = ClaudeRegistrationPlan.prepare(
            claudeExecutable: claude,
            command: { executable in
                XCTAssertEqual(executable, self.claude)
                return "'/tmp/fake-claude' mcp add-json"
            },
            arguments: { ["mcp", "add-json"] })

        XCTAssertEqual(result, .run(executable: claude,
                                    command: "'/tmp/fake-claude' mcp add-json",
                                    arguments: ["mcp", "add-json"]))
    }

    func testMissingClaudeUnderTranslocationExplainsHowToRecover() {
        let result = ClaudeRegistrationPlan.prepare(
            claudeExecutable: nil,
            command: { _ in throw MCPCommandError.translocated("/private/AppTranslocation/Circuit MCP.app") },
            arguments: { [] })

        XCTAssertEqual(result, .failure(RegistrationMessage(
            title: "Move Circuit MCP to Applications first.",
            detail: "macOS is running the app from a temporary copy, so its path would change after a restart.")))
        XCTAssertFalse(String(describing: result).contains("translocated("))
    }

    func testFoundClaudeUnderTranslocationUsesTheSameRecoveryMessage() {
        let result = ClaudeRegistrationPlan.prepare(
            claudeExecutable: claude,
            command: { _ in throw MCPCommandError.translocated("/private/AppTranslocation/Circuit MCP.app") },
            arguments: { [] })

        XCTAssertEqual(result, .failure(RegistrationMessage(
            title: "Move Circuit MCP to Applications first.",
            detail: "macOS is running the app from a temporary copy, so its path would change after a restart.")))
    }
}
