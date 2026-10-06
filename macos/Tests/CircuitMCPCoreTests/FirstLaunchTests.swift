import XCTest
@testable import CircuitMCPCore

final class FirstLaunchTests: XCTestCase {
    func testSetupIsOfferedOnlyWhileTheDataFolderIsEmpty() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }

        XCTAssertTrue(try FirstLaunch.shouldOfferSetup(dataDirectory: root))
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        XCTAssertTrue(try FirstLaunch.shouldOfferSetup(dataDirectory: root))
        try Data("notebook".utf8).write(to: root.appendingPathComponent("circuit_mcp.sqlite3"))
        XCTAssertFalse(try FirstLaunch.shouldOfferSetup(dataDirectory: root))
    }
}
