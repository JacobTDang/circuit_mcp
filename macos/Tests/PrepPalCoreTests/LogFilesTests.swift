import XCTest
@testable import PrepPalCore

final class LogFilesTests: XCTestCase {
    private var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    func testEachLaunchGetsItsOwnLogAndOnlyTheNewestFiveAreKept() throws {
        let logs = LogFiles(directory: directory)
        var created: [URL] = []
        for second in 0..<7 {
            created.append(try logs.startNewLog(now: Date(timeIntervalSince1970: 1_800_000_000 + Double(second))))
        }
        let kept = try logs.existingLogs()
        XCTAssertEqual(kept.map(\.lastPathComponent), created.suffix(5).map(\.lastPathComponent))
        XCTAssertTrue(kept.allSatisfy { $0.lastPathComponent.hasPrefix("server-") && $0.pathExtension == "log" })
    }

    func testTailReturnsTheLastFortyLines() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent("server-x.log")
        try (1...100).map { "line \($0)" }.joined(separator: "\n").appending("\n").write(to: log, atomically: true, encoding: .utf8)
        let tail = try LogFiles.tail(of: log)
        let lines = tail.split(separator: "\n")
        XCTAssertEqual(lines.count, 40)
        XCTAssertEqual(lines.first, "line 61")
        XCTAssertEqual(lines.last, "line 100")
    }

    func testTailOfAShortLogIsTheWholeLog() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent("server-y.log")
        try "only\ntwo".write(to: log, atomically: true, encoding: .utf8)
        XCTAssertEqual(try LogFiles.tail(of: log), "only\ntwo")
    }
}
