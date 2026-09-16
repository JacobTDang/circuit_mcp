import XCTest
@testable import PrepPalCore

final class ServerLineTests: XCTestCase {
    func testReadyCarriesThePort() {
        XCTAssertEqual(ServerLine.parse("READY 54321\n"), .ready(port: 54321))
    }

    func testLockedCarriesTheHolder() {
        XCTAssertEqual(ServerLine.parse("LOCKED 812"), .locked(pid: "812"))
    }

    func testOrdinaryLogOutputIsNotAProtocolLine() {
        XCTAssertNil(ServerLine.parse("INFO:     Started server process [1234]"))
        XCTAssertNil(ServerLine.parse(""))
    }

    func testABrokenProtocolLineIsReportedNotIgnored() {
        XCTAssertEqual(ServerLine.parse("READY"), .malformed(line: "READY"))
        XCTAssertEqual(ServerLine.parse("READY abc"), .malformed(line: "READY abc"))
        XCTAssertEqual(ServerLine.parse("READY 70000"), .malformed(line: "READY 70000"))
        XCTAssertEqual(ServerLine.parse("LOCKED"), .malformed(line: "LOCKED"))
    }
}
